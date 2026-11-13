"""skill_files is the safety layer between the internet and the disk.

Install paths (URL, upload, hub) all funnel into ``write_files`` /
``extract_zip``; edit funnels into ``write_main``. These tests pin
the promises that matter: frontmatter survives a roundtrip, a
hostile key or archive member cannot escape ``data/skills``, and
the limits (size, count) actually fire.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.core.errors import ValidationFailed
from app.services import skill_files


@pytest.fixture()
def skills_root(tmp_path, monkeypatch):
    """Point settings.data_dir at a throwaway directory."""
    monkeypatch.setattr(skill_files.settings, "data_dir", tmp_path)
    return tmp_path / "skills"


SKILL_MD = """---
name: pack-demo
description: 打包测试技能
version: 2.0.0
---

# Pack demo

Body line one.
"""


def make_zip(*members: tuple[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in members:
            archive.writestr(name, text)
    return buffer.getvalue()


# -- frontmatter roundtrip ----------------------------------------------------


def test_frontmatter_roundtrip_is_lossless():
    meta, body = skill_files.parse_frontmatter(SKILL_MD)
    assert meta["name"] == "pack-demo"
    assert meta["version"] == "2.0.0"
    assert body.startswith("# Pack demo")

    rebuilt = skill_files.compose_skill_md(meta, body)
    meta2, body2 = skill_files.parse_frontmatter(rebuilt)
    assert meta2 == meta
    assert body2 == body


def test_missing_frontmatter_returns_empty_meta():
    meta, body = skill_files.parse_frontmatter("# just a body\n")
    assert meta == {}
    assert body.startswith("# just a body")


def test_broken_yaml_degrades_to_empty_meta():
    text = "---\n: : : not yaml [\n---\nbody\n"
    meta, body = skill_files.parse_frontmatter(text)
    assert meta == {}
    assert "body" in body


def test_non_dict_frontmatter_is_ignored():
    text = "---\n- a\n- b\n---\nbody\n"
    meta, _ = skill_files.parse_frontmatter(text)
    assert meta == {}


# -- key validation -----------------------------------------------------------


def test_valid_keys_accepted(skills_root):
    assert skill_files.skill_dir("pack-demo") == skills_root / "pack-demo"
    assert skill_files.skill_dir("a.b_c-d2") == skills_root / "a.b_c-d2"


@pytest.mark.parametrize(
    "key",
    [
        "../evil",  # traversal
        "a/b",  # separator
        "a\\b",  # windows separator
        "",  # empty
        "-leading",  # must start alnum
        "UPPER",  # lowercase only
        "x",  # too short (needs 2+)
        "x" * 65,  # too long
    ],
)
def test_hostile_keys_rejected(key):
    with pytest.raises(ValidationFailed):
        skill_files.skill_dir(key)


def test_key_from_name_slugs_and_falls_back():
    assert skill_files.key_from_name("Pack Demo!") == "pack-demo"
    assert skill_files.key_from_name("  A  B  ") == "a-b"
    assert skill_files.key_from_name("///") == "skill"
    # lowercasing rescues this one; truly unusable names fall back
    assert skill_files.key_from_name("CON") == "con"
    assert skill_files.key_from_name("..") == "skill"


# -- zip extraction -----------------------------------------------------------


def test_zip_single_top_folder_is_stripped(skills_root):
    members = skill_files.extract_zip(
        make_zip(("pack-main/SKILL.md", SKILL_MD), ("pack-main/notes.md", "# n"))
    )
    assert dict(members).keys() == {"SKILL.md", "notes.md"}


def test_zip_flat_layout_stays_flat(skills_root):
    members = skill_files.extract_zip(make_zip(("SKILL.md", SKILL_MD)))
    assert [name for name, _ in members] == ["SKILL.md"]


@pytest.mark.parametrize(
    "member",
    [
        "../evil.md",  # parent traversal
        "a/../../evil.md",  # mid-path traversal
        "a/..\\evil.md",  # backslash traversal
        "dir/",  # directory entry
    ],
)
def test_hostile_zip_members_dropped(member):
    """A traversal or directory member never reaches the disk."""
    payload = make_zip((member, "x"), ("SKILL.md", SKILL_MD))
    members = skill_files.extract_zip(payload)
    names = [name for name, _ in members]
    assert "SKILL.md" in names  # the good member survived
    assert not any(".." in name for name in names)


def test_oversized_zip_refused():
    with pytest.raises(ValidationFailed):
        skill_files.extract_zip(b"x" * (skill_files.MAX_SKILL_ARCHIVE_BYTES + 1))


def test_garbage_zip_refused():
    with pytest.raises(ValidationFailed):
        skill_files.extract_zip(b"not a zip")


def test_absolute_members_are_sanitized_not_extracted_verbatim(skills_root):
    """Leading slashes and drive letters are stripped, not kept.

    ``/abs.md`` becomes ``abs.md`` (lstrip) and ``C:abs.md`` fails
    the drive-prefix regex, so nothing ever lands outside the skill
    directory even when an archive asks for an absolute path.
    """
    payload = make_zip(("/abs.md", "x"), ("SKILL.md", SKILL_MD))
    names = [name for name, _ in skill_files.extract_zip(payload)]
    assert "abs.md" in names
    assert "/abs.md" not in names


# -- write / limits -----------------------------------------------------------


def test_write_files_creates_directory(skills_root):
    directory = skill_files.write_files(
        "demo", [("SKILL.md", SKILL_MD.encode()), ("ref/notes.md", b"# n")]
    )
    assert (directory / "SKILL.md").is_file()
    assert (directory / "ref" / "notes.md").is_file()


def test_write_files_wipes_previous_content(skills_root):
    skill_files.write_files("demo", [("SKILL.md", b"one"), ("old.md", b"stale")])
    skill_files.write_files("demo", [("SKILL.md", b"two")])
    listing = {entry["path"] for entry in skill_files.list_files("demo")}
    assert listing == {"SKILL.md"}


def test_write_files_rejects_traversal_member(skills_root):
    with pytest.raises(ValidationFailed):
        skill_files.write_files("demo", [("../escape.md", b"x")])


def test_write_files_rejects_oversized_file(skills_root):
    big = b"x" * (skill_files.MAX_SKILL_FILE_BYTES + 1)
    with pytest.raises(ValidationFailed):
        skill_files.write_files("demo", [("SKILL.md", big)])


def test_write_files_rejects_too_many_files(skills_root):
    files = [(f"f{i}.md", b"x") for i in range(skill_files.MAX_SKILL_FILES + 1)]
    with pytest.raises(ValidationFailed):
        skill_files.write_files("demo", files)


# -- read / edit / delete -----------------------------------------------------


def test_read_main_parses_and_write_main_preserves_meta(skills_root):
    skill_files.write_files("demo", [("SKILL.md", SKILL_MD.encode())])
    main = skill_files.read_main("demo")
    assert main is not None
    assert main["meta"]["name"] == "pack-demo"

    edited = skill_files.write_main("demo", "new body here")
    main2 = skill_files.read_main("demo")
    assert main2["meta"] == main["meta"]  # metadata survives the edit
    assert "new body here" in edited


def test_list_files_flags_binary_and_truncated(skills_root):
    skill_files.write_files(
        "demo",
        [
            ("SKILL.md", SKILL_MD.encode()),
            ("blob.bin", bytes(range(256)) * 4),
        ],
    )
    entries = {entry["path"]: entry for entry in skill_files.list_files("demo")}
    assert entries["blob.bin"].get("binary") is True
    assert "content" not in entries["blob.bin"]
    assert "Pack demo" in entries["SKILL.md"]["content"]


def test_remove_dir_deletes_and_reports_missing(skills_root):
    skill_files.write_files("demo", [("SKILL.md", b"x")])
    assert skill_files.remove_dir("demo") is True
    assert not (skills_root / "demo").exists()
    assert skill_files.remove_dir("demo") is False
