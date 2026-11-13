"""End-to-end test for skill content: install / view / edit / delete.

Runs against a real API process with its own data dir, so nothing here
can corrupt the developer's ``data/``. A local HTTP server plays the
role of GitHub (a SKILL.md URL and a zip URL), which keeps the test
offline and deterministic.

    python tools/skills_smoke.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

BASE = os.environ.get("WB_BASE", "http://127.0.0.1:8000/api/v1")
PASS, FAIL = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    PASS += ok
    FAIL += not ok
    mark = "PASS" if ok else "FAIL"
    note = (f" — {detail}") if detail and not ok else ""
    print(f"[{mark}] {label}{note}", flush=True)


SKILL_MD = """---
name: pack-demo
description: 打包测试技能
version: 2.0.0
---

# Pack demo

Body line one.
"""

HELPER_MD = "# notes\n\nhelper file\n"


def make_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("pack-demo-main/SKILL.md", SKILL_MD)
        archive.writestr("pack-demo-main/references/notes.md", HELPER_MD)
    return buffer.getvalue()


class Hub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path == "/SKILL.md":
            body = SKILL_MD.encode()
        elif self.path == "/pack.zip":
            body = make_zip()
        elif self.path == "/not-md.txt":
            body = b"hello"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        kind = "application/zip" if self.path.endswith(".zip") else "text/markdown"
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


async def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Hub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        # --- install from a single SKILL.md URL --------------------------
        demo_url = f"http://127.0.0.1:{port}/SKILL.md"
        response = await client.post("/skills/install-url", json={"url": demo_url})
        ok = response.status_code in (200, 201)
        check("install from SKILL.md url works", ok, response.text[:200])
        skill = response.json()
        check("key derived from frontmatter name", skill["key"] == "pack-demo", skill["key"])
        version = str(skill.get("version"))
        check("metadata parsed into the row", version == "2.0.0", version)

        # --- content is on disk ------------------------------------------
        content = (await client.get(f"/skills/{skill['id']}/content")).json()
        check("content endpoint returns main", content["main"] is not None)
        check("body survives the roundtrip", "Body line one." in content["main"]["body"])
        listed = [f["path"] for f in content["files"]]
        check("files listed", "SKILL.md" in listed, str(listed))

        # --- install from a zip URL (top folder stripped) -----------------
        response = await client.post("/skills/install-url", json={"url": f"http://127.0.0.1:{port}/pack.zip"})
        check("install from zip url works", response.status_code in (200, 201), response.text[:200])
        content = (await client.get(f"/skills/{response.json()['id']}/content")).json()
        paths = sorted(f["path"] for f in content["files"])
        check("zip top folder stripped", paths == ["SKILL.md", "references/notes.md"], str(paths))
        notes = next(f for f in content["files"] if f["path"].endswith("notes.md"))
        check("helper content readable", "helper file" in notes["content"])

        # --- upload (base64 zip) ------------------------------------------
        archive = base64.b64encode(make_zip()).decode()
        payload = {"name": "packed.zip", "archive": archive}
        response = await client.post("/skills/upload", json=payload)
        check("upload base64 zip works", response.status_code in (200, 201), response.text[:200])

        # --- edit ---------------------------------------------------------
        new_body = "# Pack demo\n\nEdited body.\n"
        response = await client.put(
            f"/skills/{skill['id']}/content", json={"body": new_body}
        )
        check("edit saves", response.status_code == 200, response.text[:200])
        content = (await client.get(f"/skills/{skill['id']}/content")).json()
        check("edited body visible", "Edited body." in content["main"]["raw"])
        meta_after = content["main"]["meta"]
        check("frontmatter kept on edit", meta_after.get("name") == "pack-demo", str(meta_after))

        # --- rescan does not duplicate ------------------------------------
        response = await client.post("/skills/scan")
        assert response.status_code == 200
        await client.post("/skills/scan")
        listing = (await client.get("/skills")).json()
        keys = [s["key"] for s in listing]
        check("scan re-registers without duplicating", keys.count("pack-demo") == 1, str(keys))

        # --- traversal & bad input refused --------------------------------
        wrong = f"http://127.0.0.1:{port}/not-md.txt"
        response = await client.post("/skills/install-url", json={"url": wrong})
        status = response.status_code
        check("install without SKILL.md refused", status == 422, str(status))
        response = await client.post("/skills/install-url", json={"url": "ftp://example.com/x"})
        check("non-http url refused", response.status_code == 422, str(response.status_code))
        oversized = {"body": "x" * (600 * 1024)}
        response = await client.put(f"/skills/{skill['id']}/content", json=oversized)
        check("oversized body refused", response.status_code == 422, str(response.status_code))

        # --- delete removes the directory ---------------------------------
        data_dir = Path(os.environ.get("WB_DATA_DIR", "unused"))
        skill_dir = data_dir / "skills" / "pack-demo"
        existed_before = skill_dir.is_dir()
        response = await client.delete(f"/skills/{skill['id']}")
        check("delete works", response.status_code == 204, str(response.status_code))
        check("directory removed too", existed_before and not skill_dir.exists())

        # --- hub listing ---------------------------------------------------
        response = await client.get("/skills/hub")
        hub = response.json()
        check("hub returns entries", response.status_code == 200 and len(hub["entries"]) >= 1)

    server.shutdown()
    print(f"\n{PASS} passed, {FAIL} failed", flush=True)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
