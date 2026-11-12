"""Tiny forward-only migrations.

``SQLModel.metadata.create_all`` never alters an existing table, so adding a column to
a live database silently breaks every query that selects it. Real deployments should
use Alembic; this keeps the "clone, run, it works" property without that ceremony.

Rules: additive only. No renames, no drops, no data rewrites.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.core.db import engine

logger = logging.getLogger("workbench.migrate")

# table -> {column: DDL fragment}
COLUMNS: dict[str, dict[str, str]] = {
    "projects": {
        "owner_id": "VARCHAR DEFAULT ''",
        "org_id": "VARCHAR DEFAULT ''",
        "build_command": "VARCHAR DEFAULT ''",
        "dev_command": "VARCHAR DEFAULT ''",
        "dev_port": "INTEGER",
        "preview_fqdn": "VARCHAR DEFAULT ''",
        "preview_url": "VARCHAR DEFAULT ''",
        "health_path": "VARCHAR DEFAULT '/'",
    },
    "deployments": {
        "commit_sha": "VARCHAR DEFAULT ''",
    },
    "roles": {
        # empty on purpose: the backfill below decides between "all" and "own",
        # because the right value depends on which role the row is
        "data_scope": "VARCHAR DEFAULT ''",
    },
}

# Data fixes that run after the columns exist. Each statement is written so that
# re-running it cannot undo an operator's decision: the WHERE clause only matches
# rows that still hold the empty value, and the API never writes an empty scope.
BACKFILLS: list[str] = [
    "UPDATE roles SET data_scope = CASE "
    "WHEN builtin = 1 AND key IN ('owner', 'admin') THEN 'all' ELSE 'own' END "
    "WHERE data_scope IS NULL OR data_scope = ''",
    # Organizations got an owner later than they got a membership list: adopt the
    # earliest ``owner`` membership so the project backfill below has an answer.
    "UPDATE organizations SET owner_id = ("
    "  SELECT m.user_id FROM memberships m "
    "  WHERE m.org_id = organizations.id AND m.role_key = 'owner' AND m.status = 'active' "
    "  ORDER BY m.created_at LIMIT 1"
    ") WHERE (owner_id IS NULL OR owner_id = '') "
    "AND EXISTS (SELECT 1 FROM memberships m WHERE m.org_id = organizations.id "
    "AND m.role_key = 'owner' AND m.status = 'active')",
    # Rows created before ownership was keyed on the user id stored an email or
    # display name. Normalise them, otherwise the same person would look like a
    # different owner once their name changes.
    "UPDATE projects SET owner_id = ("
    "  SELECT u.id FROM users u "
    "  WHERE u.email = projects.owner_id OR u.name = projects.owner_id"
    ") WHERE owner_id <> '' AND owner_id NOT LIKE 'usr!_%' ESCAPE '!'",
    # Rows created before organizations existed have no owner at all. Hand them
    # to the organization's owner rather than leaving them unreachable for every
    # ``own``-scoped role — disappearing projects would be the worse surprise.
    "UPDATE projects SET owner_id = ("
    "  SELECT o.owner_id FROM organizations o WHERE o.id = projects.org_id"
    ") WHERE (owner_id IS NULL OR owner_id = '') AND org_id <> '' "
    "AND EXISTS (SELECT 1 FROM organizations o WHERE o.id = projects.org_id "
    "AND o.owner_id <> '')",
]

# created after the column exists; IF NOT EXISTS keeps it idempotent
INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_projects_owner_id ON projects (owner_id)",
    "CREATE INDEX IF NOT EXISTS ix_projects_org_id ON projects (org_id)",
]


def ensure_columns() -> list[str]:
    applied: list[str] = []
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in COLUMNS.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns.items():
            if name in existing:
                continue
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            applied.append(f"{table}.{name}")
            logger.info("migrated: added %s.%s", table, name)
    if applied:
        inspector = inspect(engine)
        with engine.begin() as connection:
            for statement in INDEXES:
                table = statement.split(" ON ")[1].split(" ")[0]
                if table in set(inspector.get_table_names()):
                    connection.execute(text(statement))
    run_backfills()
    return applied


def run_backfills() -> list[str]:
    """Cheap idempotent data fixes (see ``BACKFILLS``). Safe on every boot."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    done: list[str] = []
    with engine.begin() as connection:
        for statement in BACKFILLS:
            table = statement.split(" SET ")[0].split()[-1]
            if table not in tables:
                continue
            result = connection.execute(text(statement))
            if result.rowcount:
                done.append(f"{table}: {result.rowcount} row(s)")
                logger.info("backfilled %s (%s rows)", table, result.rowcount)
    return done
