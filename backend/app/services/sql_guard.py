"""SQL guard for the project database console.

The console talks to a real sqlite file, so "just run whatever the user typed" is an
obvious foot-gun: ``ATTACH``, ``load_extension`` and ``PRAGMA writable_schema`` all
escape the sandbox, and a stray ``DROP`` destroys a demo. The guard is deliberately a
*parser-free* allow-list — first-keyword plus a small deny-list of escape hatches.
That is stricter than a regex sandbox and cheap to reason about.

``WB_SQL_MODE=readonly`` (default) makes the console a query tool. Set ``write`` to
allow INSERT/UPDATE/DELETE/CREATE, still refusing the escape hatches.
"""

from __future__ import annotations

import re

from app.core.config import settings
from app.core.errors import ValidationFailed

READ_KEYWORDS = {"select", "with", "explain", "pragma", "show", "describe", "analyze"}
WRITE_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
    "replace",
    "truncate",
    "vacuum",
    "reindex",
}

# anything that can reach outside the file / corrupt the engine
DENIED_PATTERNS = (
    re.compile(r"\battach\b", re.I),
    re.compile(r"\bdetach\b", re.I),
    re.compile(r"\bload_extension\s*\(", re.I),
    re.compile(r"\bwritefile\s*\(", re.I),
    re.compile(r"\breadfile\s*\(", re.I),
    re.compile(r"\bwritable_schema\b", re.I),
    re.compile(r"\bpragma\s+\w+\s*=", re.I),
    re.compile(r"\bvacuum\s+into\b", re.I),
)

READ_ONLY_PRAGMAS = {
    "table_info",
    "table_list",
    "table_xinfo",
    "index_list",
    "index_info",
    "foreign_key_list",
    "database_list",
    "compile_options",
    "user_version",
    "page_count",
    "integrity_check",
    "quick_check",
    "encoding",
    "journal_mode",
}

_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)


def _strip_comments(sql: str) -> str:
    return _COMMENT_BLOCK.sub(" ", _COMMENT_LINE.sub(" ", sql)).strip()


def first_keyword(statement: str) -> str:
    match = re.match(r"\s*([a-zA-Z_]+)", statement)
    return match.group(1).lower() if match else ""


def check(sql: str, *, mode: str | None = None) -> str:
    """Validate and normalise one statement. Raises :class:`ValidationFailed`."""
    mode = (mode or settings.sql_mode).lower()
    statement = _strip_comments(sql)
    if not statement:
        raise ValidationFailed("SQL 不能为空")

    body = statement.rstrip().rstrip(";").rstrip()
    if ";" in body:
        raise ValidationFailed("一次只能执行一条语句（检测到多个分号）")

    for pattern in DENIED_PATTERNS:
        if pattern.search(body):
            raise ValidationFailed(f"语句包含被禁止的操作: {pattern.pattern}")

    keyword = first_keyword(body)
    if keyword == "pragma":
        if mode == "readonly":
            name = first_keyword(body[6:]) or ""
            if name not in READ_ONLY_PRAGMAS:
                raise ValidationFailed(
                    f"只读模式下不允许 PRAGMA {name}（允许: {', '.join(sorted(READ_ONLY_PRAGMAS))}）"
                )
        return body

    if keyword in READ_KEYWORDS:
        return body

    if keyword in WRITE_KEYWORDS:
        if mode == "readonly":
            raise ValidationFailed(
                f"只读模式拒绝 {keyword.upper()}；如需写入请设置 WB_SQL_MODE=write 并重启服务"
            )
        return body

    raise ValidationFailed(f"不支持的语句类型: {keyword or '(空)'}")


def describe_mode() -> dict[str, object]:
    return {
        "mode": settings.sql_mode,
        "readonly": settings.sql_mode.lower() == "readonly",
        "row_limit": settings.sql_row_limit,
        "allowed_read": sorted(READ_KEYWORDS),
        "allowed_write": sorted(WRITE_KEYWORDS) if settings.sql_mode.lower() != "readonly" else [],
    }
