from __future__ import annotations

import sqlite3
from typing import Any

from app.core.config import settings
from app.core.errors import ProviderError
from app.providers.base import DatabaseInfo, DatabaseProvider, register_provider


@register_provider("database", "local")
class LocalDatabaseProvider(DatabaseProvider):
    """One sqlite file per project database, queryable through the same interface."""

    name = "local"

    def _path(self, name: str):
        if "/" in name or "\\" in name or ".." in name:
            raise ProviderError("非法的数据库名称")
        settings.db_provider_dir.mkdir(parents=True, exist_ok=True)
        return settings.db_provider_dir / f"{name}.db"

    def create_database(self, name: str, *, project_id: str) -> DatabaseInfo:
        path = self._path(name)
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO _meta(key, value) VALUES ('project_id', ?)", (project_id,)
            )
            conn.commit()
        return self.describe(name)

    def drop_database(self, name: str) -> None:
        path = self._path(name)
        if path.exists():
            path.unlink()

    def describe(self, name: str) -> DatabaseInfo:
        path = self._path(name)
        return DatabaseInfo(
            name=name,
            provider=self.name,
            engine="sqlite",
            dsn=f"sqlite:///{path.as_posix()}",
            endpoint=path.as_posix(),
            meta={"size_bytes": path.stat().st_size if path.exists() else 0},
        )

    def execute(self, name: str, sql: str, *, limit: int = 200) -> list[dict[str, Any]]:
        path = self._path(name)
        if not path.exists():
            raise ProviderError(f"数据库不存在: {name}")
        statement = sql.strip().rstrip(";")
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(statement)
            if cursor.description is None:
                conn.commit()
                return [{"rowcount": cursor.rowcount}]
            columns = [c[0] for c in cursor.description]
            rows = cursor.fetchmany(limit)
        return [dict(zip(columns, tuple(row), strict=False)) for row in rows]
