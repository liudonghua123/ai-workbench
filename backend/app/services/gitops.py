"""Version control, without leaving the workbench.

Every project is a real git repository from the moment it is scaffolded. This module
is the only place that shells out to ``git``; REST, MCP tools and the agent harness
all call these functions, so "the agent committed" and "I committed" produce the
same object in the same repository.

Design notes
------------
* ``deploy/v<N>`` tags are the link between a running deployment and a tree. A
  deployment row stores the commit sha, so rollback is a revert + redeploy — never a
  guess about which files were live.
* Commits are authored as ``AI Workbench`` unless a repo config already sets an
  identity; nothing here needs global git configuration.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.errors import ValidationFailed

AUTHOR = ("AI Workbench", "workbench@local")
_FIELD = "\x1f"
_RECORD = "\x1e"
LOG_FORMAT = _FIELD.join(["%H", "%h", "%an", "%aI", "%s", "%d"]) + _RECORD


def _env() -> dict[str, str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat"}
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    return env


def _git(root: Path, *args: str, timeout: int = 25) -> tuple[int, str, str]:
    ident = [
        "-c", f"user.name={AUTHOR[0]}",
        "-c", f"user.email={AUTHOR[1]}",
        "-c", "core.quotepath=false",
    ]
    try:
        proc = subprocess.run(
            ["git", *ident, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_env(),
        )
    except FileNotFoundError as exc:  # pragma: no cover - git is a soft dependency
        raise ValidationFailed("环境中没有 git，无法使用版本控制") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationFailed(f"git {args[0]} 超时") from exc
    return proc.returncode, proc.stdout, proc.stderr


def _count_diff_lines(text: str, marker: str, header: str) -> int:
    """Count diff lines starting with ``marker`` but not the file header."""
    return sum(
        1 for line in text.splitlines() if line.startswith(marker) and not line.startswith(header)
    )


def available() -> bool:
    try:
        return _git(Path.cwd(), "--version")[0] == 0
    except (ValidationFailed, OSError):
        return False


def is_repo(root: Path) -> bool:
    return (root / ".git").exists()


def ensure_repo(root: Path) -> bool:
    """Make the workspace a repo with at least one commit. Idempotent."""
    if not root.is_dir():
        return False
    if not is_repo(root):
        if _git(root, "init", "-q")[0] != 0:
            return False
    code, out, _ = _git(root, "rev-parse", "--verify", "HEAD")
    if code != 0:
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "chore: initial import")
        return _git(root, "rev-parse", "--verify", "HEAD")[0] == 0
    return True


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def _numstat(root: Path) -> dict[str, tuple[int, int]]:
    """additions/deletions per path, tracked changes only."""
    stats: dict[str, tuple[int, int]] = {}
    code, out, _ = _git(root, "diff", "--numstat", "HEAD")
    if code != 0:
        return stats
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, removed, path = parts[0], parts[1], parts[2]
        stats[path] = (
            int(added) if added.isdigit() else 0,
            int(removed) if removed.isdigit() else 0,
        )
    return stats


def _line_count(path: Path) -> int:
    try:
        if path.stat().st_size > 512 * 1024:
            return 0
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return 0


def status(root: Path) -> dict[str, Any]:
    """Working tree summary: branch, per-file change kind, diff stats."""
    if not root.is_dir():
        return {"is_repo": False, "branch": "", "files": [], "clean": True, "ahead": 0, "behind": 0}

    repo = is_repo(root) or ensure_repo(root)
    if not repo:
        return {"is_repo": False, "branch": "", "files": [], "clean": True, "ahead": 0, "behind": 0}

    code, out, _ = _git(root, "status", "--porcelain=v1", "--branch", "-uall")
    branch = ""
    ahead = behind = 0
    files: list[dict[str, Any]] = []
    stats = _numstat(root)

    if code == 0:
        for line in out.splitlines():
            if line.startswith("## "):
                head = line[3:]
                branch = head.split("...")[0].split(" ")[0]
                if "[" in head:
                    tracking = head[head.index("[") + 1 : head.rindex("]")]
                    for token in tracking.split(","):
                        token = token.strip()
                        if token.startswith("ahead "):
                            ahead = int(token.split(" ")[1])
                        elif token.startswith("behind "):
                            behind = int(token.split(" ")[1])
                continue
            if len(line) < 4:
                continue
            code_xy, path = line[:2], line[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            added, removed = stats.get(path, (0, 0))
            if "?" in code_xy:
                added, removed = _line_count(root / path), 0
            files.append(
                {
                    "path": path,
                    "status": _change_kind(code_xy),
                    "staged": code_xy[0] not in " ?",
                    "additions": added,
                    "deletions": removed,
                }
            )

    head = head_commit(root)
    return {
        "is_repo": True,
        "branch": branch or "main",
        "files": files,
        "clean": not files,
        "ahead": ahead,
        "behind": behind,
        "head": head,
        "tags": tags(root),
        "staged_count": sum(1 for f in files if f["staged"]),
    }


def _change_kind(code_xy: str) -> str:
    if "?" in code_xy:
        return "untracked"
    if "D" in code_xy:
        return "deleted"
    if "R" in code_xy:
        return "renamed"
    if "A" in code_xy:
        return "added"
    return "modified"


def head_commit(root: Path) -> dict[str, Any] | None:
    code, out, _ = _git(root, "log", "-1", f"--pretty=format:{LOG_FORMAT}")
    if code != 0 or not out.strip():
        return None
    return _parse_log(out)[0]


def _parse_log(raw: str) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    for record in raw.split(_RECORD):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(_FIELD)
        if len(parts) < 5:
            continue
        sha, short, author, date, subject = parts[:5]
        decorate = parts[5] if len(parts) > 5 else ""
        commits.append(
            {
                "sha": sha,
                "short": short,
                "author": author,
                "date": _iso(date),
                "message": subject,
                "tags": _tags_from_decorate(decorate),
                "refs": [t.strip() for t in decorate.strip(" ()").split(",") if t.strip()],
            }
        )
    return commits


def _iso(value: str) -> str:
    try:
        return datetime.fromisoformat(value).isoformat()
    except ValueError:
        return value


def _tags_from_decorate(decorate: str) -> list[str]:
    return [
        part.strip().removeprefix("tag: ")
        for part in decorate.strip(" ()").split(",")
        if part.strip().startswith("tag: ")
    ]


def log(root: Path, limit: int = 30) -> list[dict[str, Any]]:
    code, out, _ = _git(root, "log", f"-{max(1, min(limit, 200))}", f"--pretty=format:{LOG_FORMAT}")
    if code != 0 or not out.strip():
        return []
    return _parse_log(out)


def diff(root: Path, path: str = "", ref: str = "") -> dict[str, Any]:
    """Unified diff of one path (or the whole tree) against HEAD or an explicit ref."""
    args = ["diff", "--no-color", "--unified=3"]
    if ref:
        args.append(ref)
    elif path:
        args.append("HEAD")
    if path:
        args.extend(["--", path])
    code, out, err = _git(root, *args)

    if not ref and not path:
        staged_code, staged, _ = _git(root, "diff", "--no-color", "--cached")
        if staged_code == 0 and staged:
            out = (out + "\n" + staged).strip("\n")

    if code != 0:
        return {
            "path": path,
            "ref": ref or "HEAD",
            "diff": "",
            "error": err.strip() or "git diff failed",
        }

    # an untracked file has no diff — synthesise one so the UI never shows a blank pane
    if not out.strip() and path and (root / path).is_file():
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        out = f"--- /dev/null\n+++ b/{path}\n" + "\n".join(
            f"+{line}" for line in text.splitlines()[:400]
        )

    additions = _count_diff_lines(out, "+", "+++")
    deletions = _count_diff_lines(out, "-", "---")
    return {
        "path": path,
        "ref": ref or "HEAD",
        "diff": out,
        "additions": additions,
        "deletions": deletions,
    }


def commit_diff(root: Path, sha: str, path: str = "") -> dict[str, Any]:
    """The diff a single commit introduced (``git show``), not the working tree."""
    if not sha:
        raise ValidationFailed("必须指定提交")
    args = ["show", "--no-color", "--format=", "--unified=3", sha]
    if path:
        args.extend(["--", path])
    code, out, err = _git(root, *args)
    if code != 0:
        return {"path": path, "ref": sha, "diff": "", "error": err.strip() or "git show failed"}
    additions = _count_diff_lines(out, "+", "+++")
    deletions = _count_diff_lines(out, "-", "---")
    return {
        "path": path,
        "ref": sha,
        "diff": out,
        "additions": additions,
        "deletions": deletions,
    }


def tags(root: Path) -> list[str]:
    code, out, _ = _git(root, "tag", "--list", "--sort=-creatordate")
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()][:50]


def show(root: Path, ref: str, path: str) -> str:
    code, out, err = _git(root, "show", f"{ref}:{path}")
    if code != 0:
        raise ValidationFailed(f"读取 {ref}:{path} 失败: {err.strip()}")
    return out


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def commit(root: Path, message: str, paths: list[str] | None = None) -> dict[str, Any]:
    """Stage and commit. Returns ``{"committed": bool}`` instead of raising when clean,
    because "nothing to commit" is a normal outcome, not an error."""
    if not ensure_repo(root):
        raise ValidationFailed("工作区不是 git 仓库")
    if paths:
        _git(root, "add", "-A", "--", *paths)
    else:
        _git(root, "add", "-A")

    code, _, _ = _git(root, "diff", "--cached", "--quiet")
    if code == 0:
        return {"committed": False, "reason": "nothing to commit", "head": head_commit(root)}

    text = (message or "").strip() or "chore: save workbench edits"
    code, out, err = _git(root, "commit", "-qm", text)
    if code != 0:
        raise ValidationFailed(f"提交失败: {err.strip() or out.strip()}")
    head = head_commit(root) or {}
    return {
        "committed": True,
        "sha": head.get("sha", ""),
        "short": head.get("short", ""),
        "message": text,
        "head": head,
    }


def restore(root: Path, path: str, ref: str = "HEAD") -> dict[str, Any]:
    """Discard local changes to one file (``git checkout <ref> -- <path>``)."""
    if not path:
        raise ValidationFailed("必须指定要恢复的文件路径")
    code, _, err = _git(root, "checkout", ref, "--", path)
    if code != 0:
        # no commit for this path yet -> it was untracked, so just delete it
        target = root / path
        if ref == "HEAD" and target.is_file():
            target.unlink()
            return {"path": path, "restored": True, "deleted": True, "ref": ref}
        raise ValidationFailed(f"恢复失败: {err.strip()}")
    return {"path": path, "restored": True, "deleted": False, "ref": ref}


def revert(root: Path, sha: str) -> dict[str, Any]:
    """Create a new commit that undoes ``sha`` — history stays append-only."""
    if not sha:
        raise ValidationFailed("必须指定要回滚的提交")
    code, out, err = _git(root, "revert", "--no-commit", sha)
    if code != 0:
        _git(root, "revert", "--abort")
        raise ValidationFailed(f"回滚失败（可能有冲突）: {err.strip() or out.strip()}")
    result = commit(root, f'revert: 撤销 {sha[:8]}')
    return {"reverted": sha, **result}


def tag(root: Path, name: str, message: str = "") -> dict[str, Any]:
    if not name:
        raise ValidationFailed("标签名不能为空")
    args = ["tag", "-a", name, "-m", message] if message else ["tag", name]
    code, _, err = _git(root, *args)
    if code != 0:
        raise ValidationFailed(f"打标签失败: {err.strip()}")
    return {"tag": name, "head": head_commit(root)}
