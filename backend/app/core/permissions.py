"""The permission catalog, the builtin roles, and the route → permission table.

Why one file holds all three:

* **The catalog is data.** ``PERMISSIONS`` is the only place a permission is
  declared. The API returns it verbatim, the UI renders checkboxes from it, and
  the validator rejects unknown keys — so a typo cannot become a silent no-op.
* **Roles are just permission sets.** No inheritance graph, no ACL evaluation
  engine, no per-row policy language. "What may this role do" is a list you can
  read out loud, which is the single most useful property a permission system
  can have for a team of five to fifty.
* **Routes map explicitly.** ``ROUTE_RULES`` is checked in order, most specific
  first. Unmatched mutating routes fall back to ``system:manage`` (fail closed)
  while unmatched reads only need authentication — new GET endpoints are cheap
  to add, new writes always require a deliberate decision.

Granularity note: permission keys are ``domain:action``. ``domain:*`` and ``*``
are wildcards resolved by :func:`expand`, so the ``owner`` role is one string
rather than a list that rots as the catalog grows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# --------------------------------------------------------------------------
# catalog
# --------------------------------------------------------------------------

# group -> (label_en, label_zh)
GROUPS: dict[str, tuple[str, str]] = {
    "project": ("Projects", "项目"),
    "delivery": ("Code & delivery", "代码与交付"),
    "data": ("Data & runtime", "数据与运行时"),
    "capability": ("Agents & capabilities", "Agent 与能力"),
    "access": ("Access control", "访问控制"),
    "platform": ("Platform", "平台"),
}

# key -> (group, label_en, label_zh, description_en, description_zh)
_RAW: list[tuple[str, str, str, str, str, str]] = [
    ("project:read", "project", "View projects", "查看项目",
     "List projects and read their overview", "列出项目并查看概览"),
    ("project:create", "project", "Create projects", "创建项目",
     "Scaffold a new project and provision its resources", "生成项目骨架并装配资源"),
    ("project:update", "project", "Edit project settings", "修改项目设置",
     "Change name, description, archive state", "改名称、描述、归档状态"),
    ("project:delete", "project", "Delete projects", "删除项目",
     "Destroy a project and its resources", "销毁项目及其资源"),
    ("code:read", "delivery", "Read code", "读取代码",
     "Browse the file tree, read files, inspect diffs", "浏览文件树、读文件、看 diff"),
    ("code:write", "delivery", "Write code", "写入代码",
     "Create, edit, delete files and commit them", "新建、编辑、删除文件并提交"),
    ("deploy:read", "delivery", "View deployments", "查看部署",
     "Deployment history, logs, preview status", "部署历史、日志、预览状态"),
    ("deploy:run", "delivery", "Run deployments", "执行部署",
     "Start/stop production deployments and previews", "启动/停止生产部署与预览"),
    ("deploy:publish", "delivery", "Publish releases", "发布版本",
     "Commit, tag deploy/v<N> and deploy that commit", "提交、打 deploy/v<N> 标签并部署该提交"),
    ("deploy:rollback", "delivery", "Roll back", "回滚",
     "Revert a commit and republish it", "回滚提交并重新发布"),
    ("data:read", "data", "Query data", "查询数据",
     "Run guarded read-only SQL, list objects", "执行受护栏的只读 SQL、列对象"),
    ("data:write", "data", "Write data", "写入数据",
     "Write statements when the SQL guard allows them", "在护栏允许时执行写语句"),
    ("env:read", "data", "View environment", "查看环境变量",
     "List env vars and their scopes", "列出环境变量及其作用域"),
    ("env:write", "data", "Change environment", "修改环境变量",
     "Set or remove env vars injected on deploy", "设置或删除部署时注入的环境变量"),
    ("domain:read", "data", "View domains", "查看域名",
     "List bound domains and DNS state", "列出绑定域名与解析状态"),
    ("domain:manage", "data", "Manage domains", "管理域名",
     "Bind, verify or unbind a domain", "绑定、校验或解绑域名"),
    ("agent:read", "capability", "View agents", "查看 Agent",
     "List agents, runs and their wiring", "列出 Agent、运行记录与装配"),
    ("agent:run", "capability", "Run agents", "运行 Agent",
     "Execute an agent against a project", "对项目执行 Agent"),
    ("agent:manage", "capability", "Manage agents", "管理 Agent",
     "Create, edit, delete agents", "新建、编辑、删除 Agent"),
    ("mcp:read", "capability", "View MCP servers", "查看 MCP",
     "List registered MCP servers and tools", "列出已注册 MCP 服务与工具"),
    ("mcp:manage", "capability", "Manage MCP servers", "管理 MCP",
     "Register, probe, delete MCP servers", "注册、探测、删除 MCP 服务"),
    ("skill:read", "capability", "View skills", "查看技能",
     "List skills and manifests", "列出技能与清单"),
    ("skill:manage", "capability", "Manage skills", "管理技能",
     "Install, scan, enable or delete skills", "安装、扫描、启停或删除技能"),
    ("member:read", "access", "View members", "查看成员",
     "See who belongs to the organization", "查看组织成员"),
    ("member:manage", "access", "Manage members", "管理成员",
     "Invite, change role, suspend or remove", "邀请、改角色、停用或移除"),
    ("role:read", "access", "View roles", "查看角色",
     "Read roles and their permission sets", "查看角色及其权限集"),
    ("role:manage", "access", "Manage roles", "管理角色",
     "Create custom roles and edit permission sets", "新建自定义角色与编辑权限集"),
    ("sso:read", "access", "View identity providers", "查看身份源",
     "See configured CAS/OAuth2/OIDC/WeCom/DingTalk connections", "查看已配置的 CAS/OAuth2/OIDC/企微/钉钉 连接"),
    ("sso:manage", "access", "Manage identity providers", "管理身份源",
     "Add, edit, enable or test an SSO connection", "新增、编辑、启停或测试 SSO 连接"),
    ("org:read", "access", "View organization", "查看组织",
     "Read org profile and settings", "读取组织信息与设置"),
    ("org:update", "access", "Edit organization", "修改组织",
     "Rename, change description or settings", "改名称、描述或设置"),
    ("org:delete", "access", "Delete organization", "删除组织",
     "Destroy the organization and everything in it", "销毁组织及其全部内容"),
    ("audit:read", "platform", "View audit log", "查看审计日志",
     "Inspect who did what, including denied attempts", "查看谁做了什么，含被拒绝的尝试"),
    ("system:read", "platform", "View platform", "查看平台",
     "Runtime overview, providers, harness status", "运行时概览、Provider、Harness 状态"),
    ("system:manage", "platform", "Manage platform", "管理平台",
     "Change runtime settings, reload templates, test LLM", "修改运行时设置、重载模板、测试 LLM"),
]

PERMISSIONS: list[dict[str, Any]] = [
    {
        "key": key,
        "group": group,
        "action": key.split(":", 1)[1],
        "domain": key.split(":", 1)[0],
        "label": label_en,
        "label_zh": label_zh,
        "description": desc_en,
        "description_zh": desc_zh,
    }
    for key, group, label_en, label_zh, desc_en, desc_zh in _RAW
]

PERMISSION_KEYS: frozenset[str] = frozenset(item["key"] for item in PERMISSIONS)
ALL_PERMISSIONS: tuple[str, ...] = tuple(sorted(PERMISSION_KEYS))


def catalog() -> dict[str, Any]:
    """Everything the role editor needs, in one payload."""
    groups = [
        {"key": key, "label": label_en, "label_zh": label_zh,
         "items": [p for p in PERMISSIONS if p["group"] == key]}
        for key, (label_en, label_zh) in GROUPS.items()
    ]
    return {"groups": groups, "permissions": PERMISSIONS, "total": len(PERMISSIONS)}


# --------------------------------------------------------------------------
# wildcards
# --------------------------------------------------------------------------


def expand(grants: list[str] | tuple[str, ...] | set[str]) -> frozenset[str]:
    """Resolve ``*`` and ``domain:*`` against the catalog.

    Unknown keys are dropped rather than raising: a role saved under a newer
    catalog must not break login for everyone who holds it.
    """
    resolved: set[str] = set()
    for grant in grants or ():
        raw = (grant or "").strip()
        if not raw:
            continue
        if raw == "*":
            resolved.update(PERMISSION_KEYS)
        elif raw.endswith(":*"):
            domain = raw[:-2]
            resolved.update(key for key in PERMISSION_KEYS if key.startswith(f"{domain}:"))
        elif raw in PERMISSION_KEYS:
            resolved.add(raw)
    return frozenset(resolved)


def normalize(grants: list[str]) -> list[str]:
    """Validate a permission list coming from the API (raises on typos)."""
    allowed = set(PERMISSION_KEYS) | {"*"}
    allowed |= {f"{domain}:*" for domain in {key.split(":", 1)[0] for key in PERMISSION_KEYS}}
    deduped: list[str] = []
    for grant in grants:
        item = (grant or "").strip()
        if not item:
            continue
        if item not in allowed:
            raise ValueError(f"未知权限: {item}")
        if item not in deduped:
            deduped.append(item)
    return deduped


def may(permissions: frozenset[str], needed: str) -> bool:
    return needed in permissions


# --------------------------------------------------------------------------
# builtin roles
# --------------------------------------------------------------------------

_READ_ALL = sorted(key for key in PERMISSION_KEYS if key.endswith(":read"))


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    key: str
    name: str
    name_zh: str
    description: str
    description_zh: str
    rank: int
    grants: list[str]

    def to_public(self) -> dict[str, Any]:
        expanded = expand(self.grants)
        return {
            "key": self.key,
            "name": self.name,
            "name_zh": self.name_zh,
            "description": self.description,
            "description_zh": self.description_zh,
            "rank": self.rank,
            "builtin": True,
            "grants": list(self.grants),
            "permissions": sorted(expanded),
            "count": len(expanded),
        }


BUILTIN_ROLES: tuple[BuiltinRole, ...] = (
    BuiltinRole(
        key="owner",
        name="Owner",
        name_zh="所有者",
        description="Full control, including deleting the organization and its billing.",
        description_zh="完全控制，含删除组织与计费设置。",
        rank=100,
        grants=["*"],
    ),
    BuiltinRole(
        key="admin",
        name="Admin",
        name_zh="管理员",
        description="Everything except destroying the organization.",
        description_zh="除删除组织外的一切权限。",
        rank=80,
        grants=[key for key in PERMISSION_KEYS if key != "org:delete"],
    ),
    BuiltinRole(
        key="developer",
        name="Developer",
        name_zh="开发者",
        description="Build projects: code, preview, deploy, publish, query data.",
        description_zh="构建项目：改代码、调试、部署、发布、查数据。",
        rank=60,
        grants=[
            *_READ_ALL,
            "project:create",
            "project:update",
            "code:write",
            "deploy:run",
            "deploy:publish",
            "deploy:rollback",
            "data:write",
            "env:write",
            "domain:manage",
            "agent:run",
            "skill:manage",
            "mcp:manage",
        ],
    ),
    BuiltinRole(
        key="operator",
        name="Operator",
        name_zh="运维",
        description="Ship what developers built; cannot touch code or access control.",
        description_zh="只负责把开发者做的东西发出去，不碰代码与访问控制。",
        rank=40,
        grants=[
            *_READ_ALL,
            "deploy:run",
            "deploy:publish",
            "deploy:rollback",
            "env:write",
        ],
    ),
    BuiltinRole(
        key="viewer",
        name="Viewer",
        name_zh="只读",
        description="Read-only: dashboards, code, logs, data.",
        description_zh="只读：看板、代码、日志、数据。",
        rank=10,
        grants=list(_READ_ALL),
    ),
)

BUILTIN_BY_KEY: dict[str, BuiltinRole] = {role.key: role for role in BUILTIN_ROLES}


def role_permissions(key: str, extra: list[str] | None = None) -> frozenset[str]:
    """Expand a role key plus ad-hoc grants (used by project bindings)."""
    grants: list[str] = []
    builtin = BUILTIN_BY_KEY.get(key)
    if builtin:
        grants.extend(builtin.grants)
    grants.extend(extra or [])
    return expand(grants)


def role_label(key: str) -> tuple[str, str]:
    builtin = BUILTIN_BY_KEY.get(key)
    return (builtin.name, builtin.name_zh) if builtin else (key, key)


# --------------------------------------------------------------------------
# route → permission
# --------------------------------------------------------------------------

PROJECT_ID = r"(?P<project_id>proj_[A-Za-z0-9]+)"

# Sentinel for endpoints that only require a valid identity.
AUTHENTICATED = ""


def _rw(read: str, write: str) -> dict[str, str]:
    return {"GET": read, "HEAD": read, "OPTIONS": AUTHENTICATED, "*": write}


@dataclass(frozen=True, slots=True)
class RouteRule:
    pattern: re.Pattern[str]
    methods: dict[str, str]

    def match(self, path: str) -> tuple[str, str | None] | None:
        found = self.pattern.fullmatch(path)
        if not found:
            return None
        groups = found.groupdict()
        return groups.get("project_id"), groups.get("org_id")


# Order matters: first match wins, so specific paths come before their parents.
ROUTE_RULES: tuple[RouteRule, ...] = (
    # --- identity -----------------------------------------------------------
    RouteRule(re.compile(r"/api/v1/auth/.*"), {"*": AUTHENTICATED}),
    # --- access control -----------------------------------------------------
    # anyone authenticated may create their own organization (they become owner)
    RouteRule(re.compile(r"/api/v1/orgs"), _rw("org:read", AUTHENTICATED)),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)/members.*"),
              _rw("member:read", "member:manage")),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)/invitations.*"),
              _rw("member:read", "member:manage")),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)/roles.*"),
              _rw("role:read", "role:manage")),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)/sso.*"),
              _rw("sso:read", "sso:manage")),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)/audit.*"),
              {"*": "audit:read"}),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)/bindings.*"),
              _rw("member:read", "member:manage")),
    RouteRule(re.compile(r"/api/v1/orgs/(?P<org_id>org_[A-Za-z0-9]+)"),
              {"GET": "org:read", "HEAD": "org:read", "PATCH": "org:update",
               "PUT": "org:update", "DELETE": "org:delete"}),
    RouteRule(re.compile(r"/api/v1/roles.*"), _rw("role:read", "role:manage")),
    RouteRule(re.compile(r"/api/v1/sso.*"), _rw("sso:read", "sso:manage")),
    RouteRule(re.compile(r"/api/v1/invitations.*"), {"*": AUTHENTICATED}),
    RouteRule(re.compile(r"/api/v1/users.*"), _rw("member:read", "member:manage")),
    RouteRule(re.compile(r"/api/v1/audit.*"), {"*": "audit:read"}),
    RouteRule(re.compile(r"/api/v1/permissions"), {"*": AUTHENTICATED}),
    # --- project delivery ---------------------------------------------------
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/publish"), {"*": "deploy:publish"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/run"), {"*": "deploy:run"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/preview/start"), {"*": "deploy:run"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/preview/stop"), {"*": "deploy:run"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/preview"), {"*": "deploy:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/git/log"), {"*": "code:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/git/diff"), {"*": "code:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/git/show"), {"*": "code:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/git/revert"),
              {"*": "deploy:rollback"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/git.*"), _rw("code:read", "code:write")),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/file"), _rw("code:read", "code:write")),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/tree"), {"*": "code:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/database.*"), _rw("data:read", "data:read")),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/storage.*"), {"*": "data:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/resources"), {"*": "data:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/env"), _rw("env:read", "env:write")),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/domains.*"),
              _rw("domain:read", "domain:manage")),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/deployments.*"),
              _rw("deploy:read", "deploy:run")),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/conventions"), {"*": "project:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/archive"), {"*": "project:update"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}/ide"), {"*": "code:read"}),
    RouteRule(re.compile(rf"/api/v1/projects/{PROJECT_ID}"), _rw("project:read", "project:update")),
    RouteRule(re.compile(r"/api/v1/projects"), _rw("project:read", "project:create")),
    RouteRule(re.compile(r"/api/v1/deployments/.*/stop"), {"*": "deploy:run"}),
    RouteRule(re.compile(r"/api/v1/deployments/.*"), {"*": "deploy:read"}),
    # --- capabilities -------------------------------------------------------
    RouteRule(re.compile(r"/api/v1/agents/[^/]+/run"), {"*": "agent:run"}),
    RouteRule(re.compile(r"/api/v1/agent-runs.*"), {"*": "agent:read"}),
    RouteRule(re.compile(r"/api/v1/agents.*"), _rw("agent:read", "agent:manage")),
    RouteRule(re.compile(r"/api/v1/mcp-servers.*"), _rw("mcp:read", "mcp:manage")),
    RouteRule(re.compile(r"/api/v1/skills.*"), _rw("skill:read", "skill:manage")),
    RouteRule(re.compile(r"/api/v1/templates.*"), _rw("project:read", "system:manage")),
    # --- platform -----------------------------------------------------------
    RouteRule(re.compile(r"/api/v1/system/llm/test"), {"*": "system:manage"}),
    RouteRule(re.compile(r"/api/v1/system/sql-mode"), {"*": "system:manage"}),
    RouteRule(re.compile(r"/api/v1/system/.*"), _rw("system:read", "system:manage")),
)

# Paths under /api/v1 that never need a principal.
PUBLIC_API_PREFIXES = (
    "/api/v1/system/health",
    "/api/v1/system/settings",
    "/api/v1/auth/",
)

UNMATCHED_WRITE = "system:manage"
UNMATCHED_READ = AUTHENTICATED


def permission_for(method: str, path: str) -> tuple[str, str | None]:
    """Resolve ``(permission, project_id)`` for a request.

    ``permission == ""`` means "any authenticated principal". A non-empty
    ``project_id`` means the check should also consider project-scoped role
    bindings, not just the org-level role.
    """
    normalized = path.rstrip("/") or "/"
    verb = method.upper()
    for rule in ROUTE_RULES:
        found = rule.match(path) or rule.match(normalized)
        if found is None:
            continue
        project_id, _org_id = found
        permission = rule.methods.get(verb, rule.methods.get("*", AUTHENTICATED))
        return permission, project_id
    if not path.startswith("/api/v1"):
        return AUTHENTICATED, None
    if verb in {"GET", "HEAD", "OPTIONS"}:
        return UNMATCHED_READ, None
    return UNMATCHED_WRITE, None


def describe() -> dict[str, Any]:
    return {
        "total": len(PERMISSIONS),
        "groups": len(GROUPS),
        "roles": [role.to_public() for role in BUILTIN_ROLES],
        "wildcards": ["*", "<domain>:*"],
    }
