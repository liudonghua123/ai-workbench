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
}

# created after the column exists; IF NOT EXISTS keeps it idempotent
INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS ix_projects_owner_id ON projects (owner_id)",
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
    return applied
