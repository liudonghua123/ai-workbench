"""Bootstrap data: system MCP server, starter skills, agents, and (optionally) demos.

Everything here is idempotent — the API can restart as often as you like.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.ids import new_id
from app.models import Agent, McpServer, Project, Skill
from app.services import scaffold
from app.services.scaffold import CreateProjectSpec
from app.services.system_tools import TOOLS

SYSTEM_TOOLS = [
    {"name": name, "description": (fn.__doc__ or "").strip().splitlines()[0]}
    for name, fn in TOOLS.items()
]

SKILLS = [
    {
        "key": "design-system",
        "name": "Design System",
        "description": "统一色彩、间距、排版与组件语汇，输出可复用的 token 与组件规范。",
        "tools": ["write_project_file"],
        "tags": ["ui", "frontend"],
    },
    {
        "key": "api-contract",
        "name": "API Contract",
        "description": "从需求推导 REST 资源模型、状态码与错误结构，保持前后端契约一致。",
        "tools": ["write_project_file", "read_project_file"],
        "tags": ["backend", "api"],
    },
    {
        "key": "deploy-checklist",
        "name": "Deploy Checklist",
        "description": "部署前检查：环境变量、健康检查、域名解析、回滚方案。",
        "tools": ["deployment_status", "set_env_var"],
        "tags": ["ops"],
    },
    {
        "key": "i18n-guide",
        "name": "i18n Guide",
        "description": "中英双语资源组织、默认语言与日期/数字格式化约定。",
        "tools": ["write_project_file"],
        "tags": ["frontend", "docs"],
    },
]

AGENTS = [
    {
        "key": "app-architect",
        "name": "应用架构师",
        "description": "根据一句话需求选择合适的模板，创建项目、部署并汇报入口地址。",
        "system_prompt": (
            "你是应用架构师。先用 list_templates 判断最合适的模板，再 create_project，"
            "然后 deploy_project 并通过 deployment_status 确认健康检查，最后用一段话汇报入口地址与资源清单。"
        ),
        "permission_scope": "full",
        "skills": ["design-system", "api-contract"],
    },
    {
        "key": "ops-copilot",
        "name": "运维副驾",
        "description": "只读巡检：项目状态、部署日志、域名解析异常定位。",
        "system_prompt": "你是运维副驾。只做只读巡检，发现失败部署时给出根因假设与下一步命令。",
        "permission_scope": "readonly",
        "skills": ["deploy-checklist"],
    },
]

DEMO_PROJECTS = [
    {
        "name": "客户成功控制台",
        "slug": "customer-success",
        "template_key": "fullstack-fastapi-nextjs",
        "description": "前后端分离的工单与客户健康度看板，演示云数据库与对象存储的自动装配。",
    },
    {
        "name": "活动落地页",
        "slug": "campaign-landing",
        "template_key": "frontend-vite-react",
        "description": "纯前端静态页，构建产物直传对象存储。",
    },
]


def seed() -> None:
    with Session(engine) as session:
        _seed_identity(session)
        _seed_mcp(session)
        _seed_skills(session)
        _seed_agents(session)
    if settings.bootstrap_demo_data:
        _seed_projects()


def _seed_identity(session: Session) -> None:
    """Default organization, builtin roles, and the demo IdP.

    The demo provider is the OIDC-shaped one served by this backend. It is a real
    round trip (state → code → token → profile), which is why the SSO path can be
    verified offline; the login page labels it 演示.
    """
    from app.models import IdentityProvider, Organization, Project
    from app.services import identity

    org = identity.default_org(session)
    identity.ensure_builtin_roles(session, org)
    # Every other tenant gets the same treatment: builtin labels and data scopes
    # ship with the code, so an org created by an older build is healed here
    # rather than only when somebody happens to open the roles tab.
    for row in session.exec(select(Organization)).all():
        if row.id != org.id:
            identity.ensure_builtin_roles(session, row)

    if not session.exec(
        select(IdentityProvider).where(IdentityProvider.key == "demo-sso")
    ).first():
        session.add(
            IdentityProvider(
                id=new_id("idp"),
                org_id=org.id,
                key="demo-sso",
                kind="mock",
                name="演示身份源",
                icon="mock",
                enabled=True,
                config={"base_url": settings.public_api_base or "http://127.0.0.1:8000"},
                default_role_key="developer",
                status="ready",
            )
        )
        session.commit()

    # projects created before organizations existed belong to the default org
    legacy = session.exec(select(Project).where(Project.org_id == "")).all()
    for project in legacy:
        project.org_id = org.id
        session.add(project)
    if legacy:
        session.commit()


def _seed_mcp(session: Session) -> None:
    if session.exec(select(McpServer).where(McpServer.is_system)).first():
        return
    session.add(
        McpServer(
            id=new_id("mcp"),
            name="workbench-system",
            description="工作台自身的完整 API：项目、资源、部署、域名、Agent 编排。",
            transport="streamable_http",
            endpoint=f"http://127.0.0.1:8000{settings.mcp_path}",
            status="healthy",
            is_system=True,
            tools=SYSTEM_TOOLS,
        )
    )
    session.add(
        McpServer(
            id=new_id("mcp"),
            name="browser-tools",
            description="示例外部 MCP：页面抓取与截图，用于验证注册 / 探测 / 绑定流程。",
            transport="streamable_http",
            endpoint="http://127.0.0.1:9100/mcp",
            status="unknown",
            tools=[
                {"name": "fetch_page", "description": "抓取页面正文"},
                {"name": "screenshot", "description": "整页截图"},
            ],
        )
    )
    session.commit()


def _seed_skills(session: Session) -> None:
    for item in SKILLS:
        if session.exec(select(Skill).where(Skill.key == item["key"])).first():
            continue
        session.add(
            Skill(
                id=new_id("skl"),
                key=item["key"],
                name=item["name"],
                version="1.0.0",
                description=item["description"],
                source="builtin",
                manifest={"tools": item["tools"], "tags": item["tags"]},
            )
        )
    session.commit()


def _seed_agents(session: Session) -> None:
    skills = {s.key: s.id for s in session.exec(select(Skill)).all()}
    servers = [s.id for s in session.exec(select(McpServer).where(McpServer.is_system)).all()]
    for item in AGENTS:
        if session.exec(select(Agent).where(Agent.key == item["key"])).first():
            continue
        session.add(
            Agent(
                id=new_id("agt"),
                key=item["key"],
                name=item["name"],
                description=item["description"],
                model=settings.default_agent_model,
                system_prompt=item["system_prompt"],
                mcp_server_ids=servers,
                skill_ids=[skills[k] for k in item["skills"] if k in skills],
                permission_scope=item["permission_scope"],
            )
        )
    session.commit()


def _seed_projects() -> None:
    with Session(engine) as session:
        existing = session.exec(select(Project)).all()
        if existing:
            return
        from app.services import identity

        org = identity.default_org(session)
        for item in DEMO_PROJECTS:
            try:
                scaffold.create_project(
                    session,
                    CreateProjectSpec(
                        name=item["name"],
                        slug=item["slug"],
                        template_key=item["template_key"],
                        description=item["description"],
                        org_id=org.id,
                    ),
                )
            except Exception:  # noqa: BLE001 - demos must never block startup
                session.rollback()
