"""Seeds a realistic organization for the access console (dev demo data).

Everything is created through the public API, which is the point: if the console
can be populated over HTTP, so can a real tenant. Re-running it tops up whatever
is missing instead of duplicating rows.

    python tools/access_demo.py                 # the demo org this account owns
    python tools/access_demo.py --org default   # an org the account already joined

Prerequisites: auth enabled (`WB_AUTH_ENABLED=true`) and signup allowed. Without
`--org` the script uses the org the account created on first run, so a fresh
install needs no bootstrap token and no manual membership.
"""

from __future__ import annotations

import argparse

import httpx

PASSWORD = "workbench-demo-1"
OWNER_EMAIL, OWNER_NAME = "owner@acme.test", "Ada Lovelace"
ORG_NAME, ORG_SLUG = "Acme Labs", "acme-labs"
PROJECT_SLUG = "acme-storefront"
PEERS = [
    ("grace@acme.test", "Grace Hopper", "viewer", "Platform"),
    ("alan@acme.test", "Alan Turing", "developer", "Runtime"),
]
# The four kinds an operator actually meets. Every config is deliberately
# plausible-but-unverified, so the console shows the honest "configured, not
# tested" state instead of a wall of green ticks.
PROVIDERS = [
    {
        "kind": "oidc", "name": "Microsoft Entra ID", "default_role_key": "developer",
        "allowed_domains": ["acme.test"],
        "config": {
            "issuer": "https://login.microsoftonline.com/00000000-0000-0000-0000-000000000000/v2.0",
            "client_id": "5f1c2c8e-0f4b-4a3e-9d7a-0b6c9e2f1234",
            "scope": "openid profile email",
            "role_map": {"wb-admins": "admin", "wb-release": "release-manager"},
        },
        "secret": {"client_secret": "demo-entra-secret"},
    },
    {
        "kind": "cas", "name": "校园统一认证", "default_role_key": "viewer",
        "config": {"base_url": "https://cas.example.edu.cn/cas", "version": "3",
                   "email_attr": "mail", "name_attr": "displayName"},
        "secret": {},
    },
    {
        "kind": "wecom", "name": "企业微信", "default_role_key": "developer",
        "config": {"corp_id": "ww1234567890abcdef", "agent_id": "1000002", "mode": "qr",
                   "role_map": {"wb-dev": "developer"}},
        "secret": {"corp_secret": "demo-wecom-secret"},
    },
    {
        "kind": "dingtalk", "name": "钉钉", "default_role_key": "operator",
        "config": {"client_id": "dingdemoappkey", "scope": "openid"},
        "secret": {"client_secret": "demo-dingtalk-secret"},
    },
]

CUSTOM_ROLE = {
    "name": "Release manager",
    "description": "可以发布与回滚，但不能改平台配置",
    "rank": 55,
    "permissions": [
        "project:read", "project:create", "project:update",
        "code:read", "code:write",
        "deploy:read", "deploy:run", "deploy:publish", "deploy:rollback",
        "env:read", "domain:read", "data:read",
        "member:read", "role:read",
    ],
}


def signup_or_login(client: httpx.Client, email: str, name: str, org_name: str) -> dict:
    created = client.post(
        "/auth/signup",
        json={"email": email, "password": PASSWORD, "name": name, "org_name": org_name},
    )
    if created.status_code == 201:
        return created.json()
    if created.status_code == 409:
        login = client.post("/auth/local/login", json={"email": email, "password": PASSWORD})
        login.raise_for_status()
        return login.json()
    created.raise_for_status()
    return {}


def ensure_project(client: httpx.Client, org: dict, org_id: str) -> dict | None:
    """The demo project for this org, created on first run.

    Lookup is by prefix because a previous run may have landed on a numbered slug:
    directories are global (``data/projects/<slug>``) while tenants are not, so a
    taken name is a real possibility that must not turn into a crash.
    """
    base = PROJECT_SLUG if org["slug"] == "default" else f"{PROJECT_SLUG}-{org['slug']}"
    rows = client.get("/projects").json()
    existing = next(
        (row for row in rows if row["slug"] == base or row["slug"].startswith(f"{base}-")),
        None,
    )
    if existing is not None:
        print(f"proj   = {existing['id']} {existing['slug']}")
        return existing

    last: httpx.Response | None = None
    for suffix in ("", "-2", "-3"):
        made = client.post(
            "/projects",
            json={
                "name": "Acme Storefront",
                "slug": f"{base}{suffix}",
                "type": "fullstack",
                "template_key": "fullstack-fastapi-nextjs",
                "description": "演示项目：前后端分离 + 云数据库",
                "org_id": org_id,
            },
        )
        if made.status_code in (200, 201):
            print(f"proj   + {made.status_code} {made.json()['id']} {made.json()['slug']}")
            return made.json()
        last = made
    print(f"proj   ! {last.status_code if last else '-'} "
          f"{(last.text[:100] if last else '')} —— 跳过项目级授权")
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--org", default="", help="target org slug (default: the demo org)")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base.rstrip("/"), timeout=60) as client:
        payload = signup_or_login(client, OWNER_EMAIL, OWNER_NAME, ORG_NAME)
        token = payload["token"]
        client.headers["authorization"] = f"Bearer {token}"

        orgs = client.get("/orgs").json()
        if args.org:
            org = next((row for row in orgs if row["slug"] == args.org), None)
            if org is None:
                raise SystemExit(
                    f"{OWNER_EMAIL} is not a member of '{args.org}'. Add the account there "
                    "first (an invitation, or WB_AUTH_BOOTSTRAP_TOKEN), then re-run — or drop "
                    "--org to seed whichever organization it can already reach."
                )
        else:
            # prefer the instance's default org so the seeded data matches the docs,
            # otherwise fall back to the org this account created on first run
            org = (
                next((row for row in orgs if row["slug"] == "default"), None)
                or next((row for row in orgs if row["slug"] == ORG_SLUG), None)
                or next((row for row in orgs if row["role_key"] == "owner"), None)
                or (orgs[0] if orgs else None)
            )
            if org is None:
                raise SystemExit(f"{OWNER_EMAIL} has no organization to seed")
        org_id = org["id"]
        # pin the session to the target org: /projects is scoped to the active one
        client.post("/auth/switch-org", json={"org_id": org_id})
        print(f"org    {org['name']} ({org_id}) role={org['role_key']}")

        roles = client.get(f"/orgs/{org_id}/roles").json()
        print(f"roles  {', '.join(role['key'] for role in roles)}")
        if not any(role["key"] == "release-manager" for role in roles):
            made = client.post(f"/orgs/{org_id}/roles", json=CUSTOM_ROLE)
            print(f"role   + release-manager {made.status_code} "
                  f"{made.json().get('count', made.text[:80])}")

        for peer_email, peer_name, role_key, title in PEERS:
            peer = signup_or_login(client, peer_email, peer_name, f"{peer_name.split()[0]} Sandbox")
            added = client.post(
                f"/orgs/{org_id}/members",
                json={"user_id": (peer.get("user") or {}).get("id", ""),
                      "role_key": role_key, "title": title},
            )
            print(f"member {peer_email:22} {role_key:9} {added.status_code}")

        # a project, so project-scoped grants have something to point at.
        # Slugs are global on disk (data/projects/<slug>), so a second org gets its
        # own suffix instead of colliding with the first tenant's directory.
        project = ensure_project(client, org, org_id)

        invites = client.get(f"/orgs/{org_id}/invitations").json()
        if not any(row["email"] == "newcomer@acme.test" and row["status"] == "pending"
                   for row in invites):
            made = client.post(
                f"/orgs/{org_id}/invitations",
                json={"email": "newcomer@acme.test", "role_key": "developer", "ttl_days": 7},
            )
            print(f"invite + {made.status_code}")

        bindings = client.get(f"/orgs/{org_id}/bindings").json()
        members = client.get(f"/orgs/{org_id}/members").json()
        developer = next((row for row in members if row["role_key"] == "developer"), None)
        granted = {row.get("scope_id") for row in bindings}
        if project and developer and project["id"] not in granted:
            made = client.post(
                f"/orgs/{org_id}/bindings",
                json={"user_id": developer["id"], "role_key": "admin", "permissions": [],
                      "scope_type": "project", "scope_id": project["id"]},
            )
            print(f"grant  + {made.status_code}")

        existing = {row["kind"] for row in client.get(f"/orgs/{org_id}/sso").json()}
        for provider in PROVIDERS:
            if provider["kind"] in existing:
                print(f"sso    = {provider['kind']}")
                continue
            made = client.post(f"/orgs/{org_id}/sso", json={**provider, "enabled": False})
            body = made.json()
            print(f"sso    + {provider['kind']:9} {body.get('key')} "
                  f"status={body.get('status')} missing={body.get('missing')}")

        # probe everything that is cheap to reach: the demo IdP answers, the real
        # tenants do not — which is exactly what the status column is for
        for provider in client.get(f"/orgs/{org_id}/sso").json():
            if provider["kind"] != "mock":
                continue
            probe = client.post(f"/orgs/{org_id}/sso/{provider['id']}/probe").json()
            print(f"probe  {provider['key']} ok={probe.get('ok')} status={probe.get('status')}")

        audit = client.get(f"/orgs/{org_id}/audit?limit=5").json()
        print(f"audit  {len(audit)} rows, latest: {audit[0]['label'] if audit else '-'}")
        print(f"\nlogin: {OWNER_EMAIL} / {PASSWORD}")


if __name__ == "__main__":
    main()
