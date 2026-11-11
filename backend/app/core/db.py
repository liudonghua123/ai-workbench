from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

connect_args = {"check_same_thread": False} if settings.resolved_database_url.startswith("sqlite") else {}
engine = create_engine(settings.resolved_database_url, connect_args=connect_args, pool_pre_ping=True)


def init_db() -> None:
    """Create tables, then apply additive migrations for databases that predate them."""
    import app.models  # noqa: F401  (register metadata)

    SQLModel.metadata.create_all(engine)
    from app.core.migrate import ensure_columns

    ensure_columns()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Session for background tasks / MCP tools that live outside a request."""
    with Session(engine) as session:
        yield session
        session.commit()
