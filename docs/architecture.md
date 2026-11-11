# AI Workbench 架构设计

> 控制面（control plane）+ 数据面（data plane）分离的可扩展应用托管平台。
> 一个 FastAPI 进程同时提供 REST（`/api/v1`）、MCP（`/mcp`）与生成项目的运行时，
> 资源供应全部经 Provider SPI 抽象，换云只改一行配置。

---

## 1. 设计目标与取舍

| 目标 | 落地方式 | 代价 |
| --- | --- | --- |
| 可视化创建项目 | 模板目录 + 创建向导（`CreateProjectDialog`） | 模板需声明完整契约 |
| 约定大于配置 | 目录布局、桶名、域名、端口、环境变量全部由 slug 推导 | 需要显式"逃生舱"覆盖默认值 |
| 系统可扩展 | 四个维度扩展点：Provider / Template / Tool / Agent | 每个扩展点要有稳定契约 |
| 一套 API 供人机共用 | REST 与 MCP 共用同一批 `system_tools` 函数 | 工具函数签名需同时满足两者 |
| 分层可替换 | 控制面绝不直接调用云 SDK | 每个云能力要写适配器 |

**核心判断**：这个平台的复杂度不在"创建项目"，而在"创建项目之后要动它"。
所以真正的架构重心放在三处——资源供应的 Provider 接缝、模板的声明式契约、
以及 `system_tools` 这一层"人机共用"的操作面。

---

## 2. 分层结构

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 15 / App Router)                                 │
│  /  /projects  /projects/[id]  /templates  /agents  /mcp  /skills   │
│  纯客户端组件 + fetch 封装（lib/api.ts）                             │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ REST /api/v1
┌───────────────────────────▼─────────────────────────────────────────┐
│  API 层  app/api/*            （薄：校验 → 调 service → 序列化）      │
│  projects / platform / registry / system                            │
├─────────────────────────────────────────────────────────────────────┤
│  服务层 app/services/*        （厚：全部业务逻辑在这里）              │
│  scaffold      项目生命周期 + 文件树 + 装配                         │
│  templates     模板目录与渲染                                       │
│  deployments   部署编排 + 日志流 + 状态机                            │
│  system_tools  人机共用的操作面（REST 与 MCP 的唯一实现）            │
│  agent_runtime 意图解析 → 工具编排 → 步骤记录                        │
│  serializers   ORM → API 契约                                       │
├─────────────────────────────────────────────────────────────────────┤
│  Provider SPI  app/providers/base.py  （四类能力的抽象接口）          │
│  storage  database  domain  deploy                                  │
│  ↳ 实现：providers/local/{storage,database,domain,deploy}.py        │
├─────────────────────────────────────────────────────────────────────┤
│  持久化 app/models.py（SQLModel）+ app/core/db.py                   │
│  projects / resources / domains / env_vars / deployments            │
│  mcp_servers / skills / agents / agent_runs / events                │
└─────────────────────────────────────────────────────────────────────┘
        │                                              │
        ▼                                              ▼
   data/projects/<slug>              data/{storage,databases,dns,logs}
   （生成的代码工作区）               （模拟的云资源，与真实云同构）
```

**分层铁律**

1. API 层不写业务逻辑，只做协议转换。任何端点的函数体应 ≤ 8 行。
2. 服务层不感知 HTTP。`system_tools` 里的函数不知道调用者是人还是 Agent。
3. Provider 层不感知项目概念，只认 `bucket` / `database name` / `fqdn` 这类资源标识。
4. 模型层不写校验逻辑，校验在 `schemas.py`（Pydantic）。

---

## 3. 领域模型

```
Project ──1:N── ProjectResource   (storage | database)
        ──1:N── DomainBinding     (主域名 + 自定义域名)
        ──1:N── EnvVar            (scope: all|production|preview)
        ──1:N── Deployment        (version + log + 状态机)

McpServer ─┐
Skill ─────┼──N:M── Agent ──1:N── AgentRun
           │                        └── steps[] （每步一次工具调用的完整记录）
Event （全局事件流，project_id 可空）
```

设计要点：

- **ID 带前缀**（`proj_ab12cd`）：可安全暴露在 URL 与日志里，一眼看出类型。
- **JSON 列而非散列**：`Project.meta` / `ProjectResource.config` / `AgentRun.steps` /
  `Skill.manifest` 承载自由结构。新增模板选项或 Provider 参数**不需要迁移**。
- **时间统一 UTC**，序列化时才转本地；`created_at` 全部带索引，事件流与列表页靠它排序。
- **`template_version` 固化在项目上**：模板升级不追溯已创建的项目，避免"昨天还能跑今天跑不起来"。

---

## 4. Provider SPI —— 唯一的云接缝

### 4.1 契约

`app/providers/base.py` 定义四类能力，均为 `ABC` + `@dataclass` 值对象：

```python
class StorageProvider(Provider):
    capability = "storage"
    def create_bucket(self, bucket: str, *, project_id: str) -> BucketInfo: ...
    def put_object(self, bucket, key, data: bytes, *, content_type="...") -> dict: ...
    def get_object(self, bucket, key) -> bytes: ...
    def list_objects(self, bucket, *, prefix="") -> list[dict]: ...
    def presign(self, bucket, key, *, expires_in=3600) -> str: ...
    def delete_bucket(self, bucket) -> None: ...
```

| 能力 | 关键方法 | 本地实现 | 目标云实现 |
| --- | --- | --- | --- |
| storage | `create_bucket` / `put_object` / `presign` | 文件系统 + 静态 HTTP | COS / S3 / OSS |
| database | `create_database` / `execute(sql)` | 独立 SQLite 文件 | RDS / PostgreSQL |
| domain | `bind` / `verify` / `unbind` | DNS zone 文件 + `localtest.me` 泛解析 | DNSPod / Route53 |
| deploy | `deploy(spec, log_sink)` / `stop` | 子进程 + 端口分配（或 simulate） | Docker / K8s / 云托管 |

`DeployProvider.deploy` 是 `async` 且接收 `LogSink`（`Callable[[str], Awaitable|None]`），
这样构建日志可以边产边推，不需要为"实时日志"另造一套事件机制。

### 4.2 注册与解析

```python
@register_provider("storage", "s3")
class S3Storage(StorageProvider): ...

# 解析（app/providers/__init__.py）
def storage_provider() -> StorageProvider:
    return registry.get("storage", settings.storage_provider)
```

切换云 = 改一行环境变量：`WB_STORAGE_PROVIDER=s3`。服务层与 API 层零改动。

### 4.3 新增一个 Provider 的完整步骤

1. 建文件 `app/providers/<cloud>/<capability>.py`。
2. 用 `@register_provider("<capability>", "<name>")` 装饰类，实现全部抽象方法。
3. 在 `app/providers/__init__.py` 里 import 该模块（触发注册）。
4. 配置项加进 `Settings`，前缀 `WB_`。
5. 改 `.env` 里的 `WB_<CAP>_PROVIDER=<name>`。

不需要改任何 service / api / 前端。`GET /api/v1/system/providers` 会自动列出新 provider。

---

## 5. 模板系统 —— 声明式契约

### 5.1 模板对象

`app/services/templates.py` 中每个模板是一个 dataclass，声明：

| 字段 | 作用 |
| --- | --- |
| `key` / `name` / `description` / `tags` / `icon` | 目录展示 |
| `type` | `fullstack` / `frontend` / `backend` / `agent`，决定创建向导的默认分支 |
| `runtime` | `node` / `python` / `node+python` / `static` |
| `run_command` | 启动命令，部署时执行；`{{port}}` 占位 |
| `resources` | **声明需要哪些云资源**，创建时自动装配 |
| `variables` | 创建时向用户收集的变量（含默认值与必填标记） |
| `files` | 相对路径 → 文件内容，`{{slug}}` `{{port}}` `{{resource.xxx}}` 占位 |

### 5.2 现有四套

| key | 类型 | 装配资源 | 用途 |
| --- | --- | --- | --- |
| `fullstack-fastapi-nextjs` | fullstack | storage + database | 前后端分离标准骨架 |
| `frontend-vite-react` | frontend | storage | 纯前端，产物直传对象存储 |
| `backend-fastapi` | backend | storage + database | 纯后端 API + MCP |
| `agent-fastapi` | agent | storage + database | 内置 Agent Harness 的服务 |

### 5.3 渲染与装配顺序

`scaffold.create_project()` 严格按此顺序，任一步失败即回滚已分配资源：

```
1. 分配 slug（冲突时追加 -2 / -3）        → 唯一性由此保证
2. 分配端口（4200-4399 区间首个空闲）      → 部署时复用
3. 渲染文件（占位符替换）→ 写入 data/projects/<slug>
4. 按 template.resources 装配资源
     storage → Provider.create_bucket("<slug>-assets")
     database → Provider.create_database("<slug>")
5. 写 .env.local（含 DSN / 桶名 / 域名，约定由模板变量注入）
6. 绑定主域名 <slug>.<WB_DOMAIN_SUFFIX>
7. 落库 Project（status=ready）+ ProjectResource + DomainBinding
8. 可选：git init + 首次提交；可选：立即部署
```

### 5.4 约定大于配置

| 约定 | 推导规则 | 覆盖方式 |
| --- | --- | --- |
| 工作区路径 | `data/projects/<slug>` | `WB_DATA_DIR` |
| 对象存储桶 | `<slug>-assets` | 模板 `files` 里显式指定 |
| 数据库名 | `<slug>` | 同上 |
| 主域名 | `<slug>.<WB_DOMAIN_SUFFIX>` | 请求体 `slug` / 后续 `POST /domains` |
| 本地端口 | `WB_PORT_RANGE_START..END` 首个空闲 | — |
| 启动命令 | 模板 `run_command` | 项目 `meta.run_command` |
| 环境变量注入 | 部署时读 `EnvVar` → 进程环境 | `scope` 字段区分环境 |

---

## 6. 人机共用操作面：`system_tools`

**这是整个架构最关键的取舍**：不做两套实现。

`app/services/system_tools.py` 里的函数是纯 Python callable，签名即契约：

```python
def create_project(name: str, template_key: str, description: str = "", slug: str = "") -> dict
def write_project_file(project_id: str, path: str, content: str) -> dict
def deploy_project(project_id: str, environment: str = "production") -> dict
def set_env_var(project_id: str, key: str, value: str, is_secret: bool = False) -> dict
```

三个消费方，同一份代码：

```
                  ┌──────────────────────┐
   REST 端点 ─────▶│                      │
   MCP 工具  ─────▶│  system_tools.TOOLS  │──▶ services（scaffold/deployments/...）
   Agent 编排 ────▶│                      │
                  └──────────────────────┘
```

益处：Agent 能做的事与人能做的事**永远等价**，不存在"UI 有但 MCP 没有"的能力缺口；
工具行为的测试一次覆盖三条链路。

### 工具清单（11 个）

`list_templates` `list_projects` `get_project` `create_project` `deploy_project`
`deployment_status` `list_project_files` `read_project_file` `write_project_file`
`set_env_var` `list_agents`

### MCP 服务端

`app/mcp/server.py` 用 `fastmcp` 构建，与 REST **同进程同端口**挂载：

```python
mcp = FastMCP(name="ai-workbench", instructions="...")
for name, fn in TOOLS.items():
    mcp.tool(fn, name=name)

app.mount("/mcp", http_app())   # streamable HTTP
```

额外注册两个元工具：

- `run_workbench_agent(agent_key, prompt)` —— 把高层目标交给配置好的 Agent 去规划执行
- `workbench_capabilities()` —— 返回对象模型、工具清单与全部约定，让外部 Agent 能安全规划

客户端接入：

```json
{ "mcpServers": { "workbench": { "url": "http://127.0.0.1:8000/mcp" } } }
```

---

## 7. Agent 运行时

`app/services/agent_runtime.py` 是**可替换的 Harness 抽象**（当前内置轻量实现，
接口对齐 AgentScope 这类库的 `plan → act → observe` 循环）。

```
prompt ──▶ 意图解析（关键词 → 意图 + 槽位）
       ──▶ 计划生成（intent → [tool_call, ...]）
       ──▶ 逐步执行（每步独立捕获异常，单步失败不中断整体）
       ──▶ 观察聚合（把每步 result 提炼成人类可读结论）
       ──▶ 落库 AgentRun（steps 完整留痕，status + output）
```

**权限隔离**：`Agent.permission_scope` 三档——

| scope | 可用工具 |
| --- | --- |
| `readonly` | 只读类（list / get / read_file / deployment_status） |
| `project` | 读 + 改文件、改环境变量、部署（**不能删项目**） |
| `full` | 全部，含删除与跨项目操作 |

权限在 Harness 选工具前过滤，不是靠模型自觉。

**接入真实 LLM**：替换 `_plan()` 即可。工具定义已是 JSON-schema 友好的签名，
可直接转成 OpenAI / Anthropic 的 function calling 描述；执行层与落库层不用动。

---

## 8. 注册中心：MCP / Skill / Agent 可视化

三者共用同一套模式：**表存储 + REST CRUD + 探测/启停动作 + 前端管理页**。

| 实体 | 关键字段 | 特殊动作 |
| --- | --- | --- |
| `McpServer` | transport(stdio/sse/streamable_http)、command/args/env、tools[] | `POST /{id}/probe` 探活并抓取工具列表 |
| `Skill` | key、version、source(builtin/path/url)、enabled、manifest | `POST /scan` 扫描磁盘目录自动登记；`POST /{id}/toggle` |
| `Agent` | model、system_prompt、mcp_server_ids[]、skill_ids[]、permission_scope | `POST /{key}/run` 触发一次运行 |

`McpServer.tools` 与 `Skill.manifest` 用 JSON 列，因此第三方 MCP 返回什么结构都能原样存下，
前端只渲染已知字段，未知字段透传展示——**新增 MCP 不需要改代码**。

Agent 通过 `mcp_server_ids` / `skill_ids` 组合能力，是纯粹的数据关联，
不做代码生成，因此组合方式可以在 UI 上随时调整。

---

## 9. 部署流水线

```
POST /projects/{id}/deployments
   │
   ├─ 创建 Deployment(status=queued, version=v<n>)
   ├─ asyncio.create_task(...)  ← 立即返回 202，前端轮询
   ▼
building   → 解析 run_command、注入 EnvVar、Provider.deploy(spec, sink)
             sink 每次写入都 append 到 Deployment.log（带 flush）
deploying  → 健康检查（simulate 模式走确定性脚本，process 模式真探端口）
running    → 写 url（优先主域名，回退 <fqdn>:<port>），finished_at
失败       → status=failed + error 字段，日志保留
```

**两种模式**（`WB_DEPLOY_MODE`）：

- `simulate`：产出完整的、贴近真实的构建日志，不启进程。适合演示与 CI。
- `process`：真起子进程监听分配端口，`stop` 时终止。适合本地验证。

**为什么部署是异步任务而不是同步请求**：构建可能几十秒，同步会打爆浏览器与反向代理超时；
异步 + 轮询（前端 1.2s 间隔）实现简单、可断线重连、日志天然可增量。

---

## 10. 前端表现层：i18n 与布局偏好

表现层只有两条规则：**文案不进组件、偏好不进数据库**。

### 10.1 类型化字典

`lib/i18n.tsx` 用 `en` 对象作为唯一真相源：

```ts
const en = { "dash.title": "Workbench overview", ... } as const;
export type MessageKey = keyof typeof en;                 // 键由英文推导
const zh: Record<MessageKey, string> = { ... };           // 少一条即编译错误
```

于是三类错误都在编译期被拦住：拼错键、`zh` 缺条目、`t()` 传错变量名。

消费方式统一：

```tsx
const { t, locale } = useI18n();
t("dash.kpi.projectsFoot", { ready: 3 });   // "3 ready" / "3 个就绪"
relativeTime(project.updated_at, locale);   // "2h ago" / "2 小时前"
```

配套约定：

- **状态映射存键不存文案**。`format.ts` 的 `PROJECT_STATUS` 等是
  `{ key: MessageKey, tone }`，`StatusPill` 内部翻译。这样徽章与表格、
  卡片共用一份定义，不会出现"卡片翻译了、表格没翻译"。
- **日期格式化跟随 locale**，而不是靠 `toLocaleString()` 的默认值。
- **需要内嵌 `<code>` 的句子**用占位符切分（`t("mcp.desc").split("{path}")`），
  避免把 JSX 塞进字典。

### 10.2 布局偏好

`lib/prefs.tsx` 只持有影响 DOM 结构的偏好（当前是导航位置）：

```
PrefsProvider ──▶ Shell  ──▶ data-nav="rail" | "top"
                     │           ├─ rail：渲染 <Rail/>
                     └─ Topbar ──┴─ top ：渲染 <TopNav/>，隐藏 Rail
```

`components/NavLinks.tsx` 导出 `RailNav` / `TopNav` 两个变体，共享同一份
`lib/nav.ts` 配置与同一份计数请求——新增一个页面只改 `NAV` 一处。

主题走另一条路：它不需要改结构，所以直接写 `<html data-theme>`，并由
`themeScript` 在 hydration 前执行，避免首屏闪烁。语言同理有轻微的首屏
翻转（默认英文），这是"默认 en + localStorage"的固有代价，可接受。

---

## 11. 扩展点总览

| 想做的事 | 动哪里 | 要不要改前端 |
| --- | --- | --- |
| 换云存储 / 云数据库 / DNS / 部署目标 | 新增 `providers/<cloud>/*.py` + 改 `.env` 一行 | 否 |
| 加一套新模板 | `templates.py` 加一个 dataclass | 否（目录自动展示） |
| 加一个 Agent 可用的能力 | `system_tools.py` 加函数 + 注册进 `TOOLS` | 否（REST/MCP 自动同步） |
| 接入真实 LLM | 替换 `agent_runtime._plan()` | 否 |
| 接入 Web 版 VS Code | 设 `WB_IDE_URL_TEMPLATE` | 否（自动切 iframe） |
| 加一门语言 | `lib/i18n.tsx` 加一个对象 + `LOCALES` 一项 | 否 |
| 加一种资源类型（如 Redis） | `ResourceKind` 加枚举 + Provider 新能力 + 模板声明 | 是（资源表格渲染） |
| 加管理页面 | `app/api/` 新路由 + `frontend/app/` 新页面 | 是 |

**判断标准**：只要改动落在 Provider / Template / Tool 三个维度内，前端与已有服务零改动；
一旦触及"资源类型的种类"或"新的实体"，才需要动前端。

---

## 12. 已知限制与下一步

| 限制 | 现状 | 建议 |
| --- | --- | --- |
| 无鉴权 | 单用户本地运行，无登录 | 加 OIDC / 简单 token 中间件，`Project.owner_id` 已预留位置 |
| 端口分配非原子 | 单进程内顺序分配 | 多副本部署时改用数据库唯一约束 + 重试 |
| SQL 执行无沙箱 | 项目库独立，但可执行任意 SQL | 只读连接 + 语句白名单（`SELECT` 前缀） |
| Agent 为规则驱动 | 关键词意图解析，无真实 LLM | 换 `_plan()` 为 LLM function calling |
| 语言首屏翻转 | 默认 en，hydration 后切到存储的 zh | 改读 cookie 让 SSR 直接出正确语言 |
| 无并发部署限制 | 同项目可并行触发多个部署 | 项目级锁（`Deployment` 活跃状态检查） |

---

## 13. 目录速查

```
backend/
  app/
    main.py              应用装配（CORS、路由、MCP 挂载、种子数据）
    models.py            9 张表
    schemas.py           请求/响应契约
    core/                config(全部 WB_* 配置) / db / errors / ids
    api/                 projects / platform / registry / system
    services/            scaffold / templates / deployments / system_tools
                         agent_runtime / serializers
    providers/           base(SPI+registry) + local/{storage,database,domain,deploy}
    mcp/server.py        fastmcp 服务端，与 REST 同端口
frontend/
  app/                   10 个路由（含 /projects/[id] 五标签页）
  components/            Shell(导航布局) / Rail / Topbar / NavLinks
                         Preferences(语言·主题·导航) / CreateProjectDialog
                         LogViewer / ProjectCard / Icon(全 SVG) / ui
  lib/                   api / types / format(状态映射) / i18n(类型化字典)
                         prefs(布局偏好) / nav(导航单一真相源)
docs/architecture.md     本文档
data/                    运行时生成（已 gitignore）
```
