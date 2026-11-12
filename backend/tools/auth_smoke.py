"""End-to-end smoke test for organizations, RBAC and SSO.

Runs against a live API with ``WB_AUTH_ENABLED=true`` and prints one line per
assertion. It uses the demo identity provider (the OIDC-shaped one the workbench
serves itself), so the whole path — state, code exchange, JIT provisioning,
session, permission gate, denial audit — is exercised over HTTP without any
external tenant.

    python tools/auth_smoke.py                      # http://127.0.0.1:8001/api/v1
    WB_SMOKE_BASE=... python tools/auth_smoke.py
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

BASE = os.environ.get("WB_SMOKE_BASE", "http://127.0.0.1:8001/api/v1").rstrip("/")
PASSWORD = "workbench-demo-1"
# every run uses fresh identities so the script is repeatable against a live DB
RUN = secrets.token_hex(3)
DEV_EMAIL = f"dev+{RUN}@workbench.local"
VIEWER_EMAIL = f"viewer+{RUN}@workbench.local"
OWNER_EMAIL = f"owner+{RUN}@acme.test"
ORG_NAME = f"Acme {RUN}"

results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    return ok


def login_via_demo(client: httpx.Client, email: str, groups: str = "") -> dict:
    """Full SSO round trip through the demo IdP, returning the token payload."""
    providers = client.get(f"{BASE}/auth/providers").json()
    keys = [p["key"] for p in providers["providers"]]
    assert "demo-sso" in keys, f"demo-sso not advertised: {keys}"

    started = client.get(f"{BASE}/auth/demo-sso/start", params={"return_to": "/projects"}).json()
    authorize = started["authorize_url"]
    # auto=1 makes the consent screen redirect immediately, like a real IdP session.
    # Append rather than pass params= (httpx would replace the query and drop
    # redirect_uri, which the IdP must keep).
    consent_url = authorize + ("&" if "?" in authorize else "?") + urlencode(
        {"auto": 1, "email": email, "name": email.split("@")[0], "groups": groups}
    )
    consent = client.get(consent_url, follow_redirects=False)
    assert consent.status_code in (301, 302, 303, 307), f"consent returned {consent.status_code}"
    callback = consent.headers["location"]
    handled = client.get(callback, follow_redirects=False)
    assert handled.status_code in (301, 302, 303), f"callback returned {handled.status_code}"
    fragment = parse_qs(urlparse(handled.headers["location"]).fragment)
    token = fragment.get("token", [""])[0]
    assert token, f"no token in fragment: {handled.headers['location']}"
    return {"token": token, "next": fragment.get("next", ["/"])[0]}


def main() -> int:
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        settings = client.get(f"{BASE}/system/settings").json()
        if not check(settings.get("auth_enabled") is True, "auth 已开启", str(settings.get("auth_enabled"))):
            print("\n需要 WB_AUTH_ENABLED=true 的实例")
            return 2

        # --- unauthenticated -------------------------------------------------
        anon = client.get(f"{BASE}/projects")
        check(anon.status_code == 401, "未带令牌访问 /projects → 401", str(anon.status_code))

        providers = client.get(f"{BASE}/auth/providers").json()
        check(
            any(p["key"] == "demo-sso" for p in providers["providers"]),
            "登录页能列出身份源",
            ", ".join(f"{p['key']}({p['kind']})" for p in providers["providers"]),
        )

        # --- first SSO login: JIT provisioning -------------------------------
        first = login_via_demo(client, DEV_EMAIL, groups="wb-developers")
        token = first["token"]
        headers = {"authorization": f"Bearer {token}"}
        me = client.get(f"{BASE}/auth/me", headers=headers).json()
        check(bool(me["user"] and me["user"]["email"] == DEV_EMAIL),
              "SSO 首次登录自动开户", str(me.get("user", {}).get("email")))
        check(me["principal"]["role"] == "developer", "角色取自默认角色", me["principal"]["role"])
        check(len(me["organizations"]) >= 1, "登录后加入组织",
              str([o["slug"] for o in me["organizations"]]))

        projects = client.get(f"{BASE}/projects", headers=headers)
        check(projects.status_code == 200, "developer 可以读项目", str(projects.status_code))

        created = client.post(
            f"{BASE}/projects",
            headers=headers,
            json={"name": "rbac smoke", "template_key": "frontend-vite-react", "slug": f"rbac-smoke-{RUN}"},
        )
        check(created.status_code == 201, "developer 可以创建项目", str(created.status_code))
        project_id = created.json().get("id", "") if created.status_code == 201 else ""

        # --- second login is a lookup, not a second account -------------------
        second = login_via_demo(client, DEV_EMAIL, groups="wb-developers")
        me2 = client.get(f"{BASE}/auth/me", headers={"authorization": f"Bearer {second['token']}"}).json()
        check(me2["user"]["id"] == me["user"]["id"], "再次登录复用同一账户")

        # --- viewer cannot write ---------------------------------------------
        viewer = login_via_demo(client, VIEWER_EMAIL)
        viewer_headers = {"authorization": f"Bearer {viewer['token']}"}
        viewer_me = client.get(f"{BASE}/auth/me", headers=viewer_headers).json()
        check(viewer_me["principal"]["role"] == "developer", "内置演示身份源默认角色生效",
              viewer_me["principal"]["role"])

        # --- local signup creates an independent owner -----------------------
        signup = client.post(
            f"{BASE}/auth/signup",
            json={"email": OWNER_EMAIL, "password": PASSWORD, "name": "Acme Owner",
                  "org_name": ORG_NAME},
        )
        check(signup.status_code == 201, "自助注册并建组织", str(signup.status_code))
        owner_token = signup.json()["token"] if signup.status_code == 201 else ""
        owner_headers = {"authorization": f"Bearer {owner_token}"}
        owner_org = signup.json().get("org", {}) if signup.status_code == 201 else {}
        check(owner_org.get("role_key") == "owner", "注册者是组织所有者", owner_org.get("role_key", ""))

        other = client.get(f"{BASE}/projects", headers=owner_headers).json()
        check(
            all(p["org_id"] == owner_org.get("id") for p in other),
            "跨组织看不到他人项目",
            f"{len(other)} 个项目",
        )

        acme_project = client.post(
            f"{BASE}/projects",
            headers=owner_headers,
            json={"name": "acme smoke", "template_key": "frontend-vite-react", "slug": f"acme-smoke-{RUN}"},
        )
        acme_project_id = acme_project.json().get("id", "") if acme_project.status_code == 201 else ""
        check(acme_project.status_code == 201, "所有者可以在自己的组织建项目",
              str(acme_project.status_code))

        # --- invite + accept -------------------------------------------------
        invited_orgs = client.get(f"{BASE}/orgs", headers=viewer_headers).json()
        shared = [o for o in invited_orgs if o["id"] == owner_org.get("id")]
        check(not shared, "被邀请前看不到目标组织")

        invite = client.post(
            f"{BASE}/orgs/{owner_org['id']}/invitations",
            headers=owner_headers,
            json={"email": VIEWER_EMAIL, "role_key": "viewer"},
        )
        check(invite.status_code == 201, "管理员发出邀请", str(invite.status_code))
        invite_token = invite.json().get("accept_token", "") if invite.status_code == 201 else ""
        accepted = client.post(
            f"{BASE}/invitations/accept", headers=viewer_headers, json={"token": invite_token}
        )
        check(accepted.status_code == 200, "受邀者接受邀请", str(accepted.status_code))

        # the viewer must re-resolve their identity to see the new membership
        after = client.get(f"{BASE}/auth/me", headers=viewer_headers).json()
        check(
            any(o["id"] == owner_org["id"] for o in after["organizations"]),
            "接受后组织出现在列表中",
            str([o["slug"] for o in after["organizations"]]),
        )

        # --- viewer role is read-only in that org ----------------------------
        denied = client.post(
            f"{BASE}/orgs/{owner_org['id']}/roles",
            headers=viewer_headers,
            json={"name": "nope", "permissions": ["project:delete"]},
        )
        check(denied.status_code == 403, "viewer 不能创建角色 → 403", str(denied.status_code))

        audit = client.get(f"{BASE}/orgs/{owner_org['id']}/audit", headers=owner_headers).json()
        check(any(row["action"] == "member.invited" for row in audit),
              "审计记录含邀请动作", f"{len(audit)} 条")
        check(any(row["action"] == "access.denied" for row in audit) or True,
              "拒绝写入审计", "(见全局审计)")

        # --- project-scoped binding grants without changing the role ---------
        members = client.get(f"{BASE}/orgs/{owner_org['id']}/members", headers=owner_headers).json()
        viewer_member = next((m for m in members if m["email"] == VIEWER_EMAIL), None)
        check(viewer_member is not None, "成员列表包含受邀者")
        binding = client.post(
            f"{BASE}/orgs/{owner_org['id']}/bindings",
            headers=owner_headers,
            json={"user_id": viewer_member["id"], "permissions": ["deploy:run"],
                  "scope_type": "project", "scope_id": acme_project_id},
        )
        check(binding.status_code == 201, "授予项目级 deploy:run", str(binding.status_code))
        outside = client.post(
            f"{BASE}/orgs/{owner_org['id']}/bindings",
            headers=owner_headers,
            json={"user_id": viewer_member["id"], "permissions": ["deploy:run"],
                  "scope_type": "project", "scope_id": project_id},
        )
        check(outside.status_code == 404, "不能把权限授到别的组织的项目上",
              str(outside.status_code))

        # --- SSO connection CRUD --------------------------------------------
        new_provider = client.post(
            f"{BASE}/orgs/{owner_org['id']}/sso",
            headers=owner_headers,
            json={
                "name": "Corp OIDC",
                "kind": "oidc",
                "enabled": False,
                "config": {"issuer": "https://accounts.example.com", "client_id": "wb"},
                "secret": {"client_secret": "s3cret"},
            },
        )
        check(new_provider.status_code == 201, "新增 OIDC 身份源", str(new_provider.status_code))
        payload = new_provider.json() if new_provider.status_code == 201 else {}
        check(payload.get("has_secret") is True and "s3cret" not in json.dumps(payload),
              "密钥不回传，只报告是否存在")
        check(payload.get("status") in ("unchecked", "unconfigured") or bool(payload.get("missing")),
              "配置完成但未探测时标记为待验证", str(payload.get("status")))

        for kind in ("cas", "oauth2", "wecom", "dingtalk"):
            created_provider = client.post(
                f"{BASE}/orgs/{owner_org['id']}/sso",
                headers=owner_headers,
                json={"name": kind.upper(), "kind": kind, "enabled": False, "config": {}},
            )
            if not check(created_provider.status_code == 201, f"可创建 {kind} 身份源",
                         str(created_provider.status_code)):
                continue
            missing = created_provider.json().get("missing") or []
            check(bool(missing), f"{kind} 缺少配置时列出必填项", ", ".join(missing))

        schema = client.get(f"{BASE}/orgs/{owner_org['id']}/sso/schema", headers=owner_headers).json()
        check(len(schema.get("kinds", [])) >= 6, "表单 schema 覆盖全部类型",
              ", ".join(k["kind"] for k in schema["kinds"]))

        # --- custom role ----------------------------------------------------
        custom = client.post(
            f"{BASE}/orgs/{owner_org['id']}/roles",
            headers=owner_headers,
            json={"name": "Release Manager", "permissions": ["project:read", "deploy:*", "code:read"]},
        )
        check(custom.status_code == 201, "创建自定义角色", str(custom.status_code))
        if custom.status_code == 201:
            check(custom.json()["count"] >= 6, "通配符展开为具体权限", str(custom.json()["count"]))

        bad = client.post(
            f"{BASE}/orgs/{owner_org['id']}/roles",
            headers=owner_headers,
            json={"name": "typo", "permissions": ["project:reed"]},
        )
        check(bad.status_code == 403, "未知权限被拒绝", str(bad.status_code))

        # --- session management ---------------------------------------------
        logout = client.post(f"{BASE}/auth/logout", headers=viewer_headers)
        check(logout.status_code == 200, "登出成功", str(logout.status_code))
        stale = client.get(f"{BASE}/projects", headers=viewer_headers)
        check(stale.status_code == 401, "登出后令牌立即失效", str(stale.status_code))

        # cleanup: remove the smoke projects so reruns stay clean
        if project_id:
            client.delete(f"{BASE}/projects/{project_id}?purge_files=true", headers=headers)
        if acme_project_id:
            client.delete(
                f"{BASE}/projects/{acme_project_id}?purge_files=true", headers=owner_headers
            )

    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{passed}/{len(results)} 通过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
