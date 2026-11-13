"""Skill content on disk.

A skill used to be a row in ``skills`` — metadata only, with the actual
``SKILL.md`` living wherever ``scan`` happened to find it. That made
``source: builtin`` skills unopenable (there was no file at all) and
editing a skill meant editing SQLite by hand.

Now every skill owns a directory: ``data/skills/<key>/``. The manifest
lives in the database as before, but the *content* — ``SKILL.md`` and
any helper files — lives on disk, so the same bytes an agent reads are
the bytes the UI shows and edits. Install (upload/URL/hub) writes the
directory; edit rewrites ``SKILL.md`` and re-parses the frontmatter
back into the row; delete removes the directory it created.

Security shape: paths are always joined from ``data/skills`` and a
sanitized key or a zip member checked against ``..`` / absolute /
backslash tricks — a malicious archive must not be able to write
outside its own directory.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings
from app.core.errors import ValidationFailed

MAX_SKILL_FILE_BYTES = 512 * 1024  # a skill is text, not a dataset
MAX_SKILL_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_SKILL_FILES = 32

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
# key is a directory name and a slug: keep it boring on purpose
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_UNSAFE_MEMBER = re.compile(r"(^|/)\.\.($|/)|^[A-Za-z]:|^/")


def skills_root() -> Path:
    root = settings.data_dir / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


def skill_dir(key: str) -> Path:
    if not _KEY_RE.match(key or ""):
        raise ValidationFailed(f"非法的 skill 标识: {key!r}")
    directory = skills_root() / key
    # the regex already forbids traversal; resolve() is the belt to its braces
    if directory.resolve().parent != skills_root().resolve():
        raise ValidationFailed(f"非法的 skill 标识: {key!r}")
    return directory


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md into (metadata, body). Missing frontmatter is fine."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, text[match.end():].lstrip("\n")


def compose_skill_md(meta: dict[str, Any], body: str) -> str:
    """Inverse of :func:`parse_frontmatter`, used by the editor."""
    lines = ["---"]
    for key, value in meta.items():
        if value in (None, "", [], {}):
            continue
        dumped = yaml.safe_dump({key: value}, allow_unicode=True, sort_keys=False).rstrip("\n")
        lines.append(dumped)
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip("\n") + "\n"


def key_from_name(name: str) -> str:
    """Derive a directory-safe key from a skill name or filename."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", str(name).strip().lower())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    if not slug or not _KEY_RE.match(slug):
        slug = "skill"
    return slug[:64]


def safe_member_name(name: str) -> str | None:
    """A zip/tar member we are willing to extract, relative and inside the skill."""
    normalized = str(name).replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return None
    if _UNSAFE_MEMBER.search(normalized):
        return None
    # nested dirs are fine; a SKILL.md at the root is what we look for
    return normalized


# -- read --------------------------------------------------------------------


def list_files(key: str) -> list[dict[str, Any]]:
    directory = skill_dir(key)
    if not directory.is_dir():
        return []
    files: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        relative = path.relative_to(directory).as_posix()
        size = path.stat().st_size
        entry: dict[str, Any] = {"path": relative, "size": size}
        if size <= MAX_SKILL_FILE_BYTES:
            try:
                entry["content"] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                entry["binary"] = True
        else:
            entry["truncated"] = True
        files.append(entry)
    return files


def read_main(key: str) -> dict[str, Any] | None:
    """The SKILL.md, split into metadata and body for the editor."""
    main = skill_dir(key) / "SKILL.md"
    if not main.is_file():
        return None
    text = main.read_text(encoding="utf-8", errors="replace")
    meta, body = parse_frontmatter(text)
    return {"meta": meta, "body": body, "raw": text}


def ensure_dir_for(key: str, *, wipe: bool = False) -> Path:
    directory = skill_dir(key)
    if wipe and directory.is_dir():
        for child in sorted(directory.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_files(key: str, files: list[tuple[str, bytes]]) -> Path:
    """Write ``SKILL.md`` + companions; refuse to leave the skill directory."""
    if not files:
        raise ValidationFailed("没有可写入的文件")
    if len(files) > MAX_SKILL_FILES:
        raise ValidationFailed(f"文件数超过上限 {MAX_SKILL_FILES}")
    directory = ensure_dir_for(key, wipe=True)
    for name, payload in files:
        member = safe_member_name(name)
        if member is None:
            raise ValidationFailed(f"拒绝写入路径: {name!r}")
        if len(payload) > MAX_SKILL_FILE_BYTES:
            raise ValidationFailed(f"文件过大: {name}")
        target = directory / member
        if not target.resolve().is_relative_to(directory.resolve()):
            raise ValidationFailed(f"拒绝写入路径: {name!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return directory


def extract_zip(payload: bytes) -> list[tuple[str, bytes]]:
    """The text files of an archive, with the folder prefix stripped.

    GitHub and most skill packs zip a top-level directory; keeping it
    would double-nest the skill, so ``foo-bar/SKILL.md`` becomes
    ``SKILL.md`` when ``foo-bar`` is the only top entry.
    """
    if len(payload) > MAX_SKILL_ARCHIVE_BYTES:
        raise ValidationFailed("压缩包超过 8MB 上限")
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValidationFailed("不是有效的 zip 文件") from exc

    members: list[tuple[str, bytes]] = []
    for info in archive.infolist():
        member = safe_member_name(info.filename)
        if member is None or info.is_dir():
            continue
        if info.file_size > MAX_SKILL_FILE_BYTES:
            continue
        try:
            members.append((member, archive.read(info)))
        except (zipfile.BadZipFile, RuntimeError):
            continue
    if not members:
        raise ValidationFailed("压缩包里没有可用的文件")
    # single top-level folder → strip it
    tops = {name.split("/", 1)[0] for name, _ in members if "/" in name}
    if len(tops) == 1 and all("/" in name for name, _ in members):
        prefix = f"{tops.pop()}/"
        members = [
            (name[len(prefix):], payload)
            for name, payload in members
            if name.startswith(prefix) and name[len(prefix):]
        ]
    return members


def remove_dir(key: str) -> bool:
    import shutil

    directory = skill_dir(key)
    if not directory.is_dir():
        return False
    shutil.rmtree(directory)
    return True


def write_main(key: str, body: str, meta: dict[str, Any] | None = None) -> str:
    """Persist an edited SKILL.md; returns the full file text."""
    directory = ensure_dir_for(key)
    if meta is None:
        current = read_main(key)
        meta = current["meta"] if current else {}
    text = compose_skill_md(meta, body)
    if len(text.encode()) > MAX_SKILL_FILE_BYTES:
        raise ValidationFailed("SKILL.md 过大")
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return text
