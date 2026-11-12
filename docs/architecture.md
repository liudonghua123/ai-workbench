# AI Workbench 架构设计

> 控制面（control plane）+ 数据面（data plane）分离的可扩展应用托管平台。
> 一个 FastAPI 进程同时提供 REST（`/api/v1`）、MCP（`/mcp`）与生成项目的运行时，
> 资源供应全部经 Provider SPI 抽象，换云只改一行配置。

---

## 1. 设计目标与取舍

| 目标 | 落地方式 | 代价 |
| --- | --- | --- |
| 可视化创建项目 | 模板目录 + 创建向导（`CreateProjectDialog`） | 模板需声明完整契约 |
| 约定大于配置 | 目录布局、桶名、域名、端口、环境变量全部由 slug 推导；类型规范写成 `TypeSpec` 数据 | 需要显式"逃生舱"覆盖默认值（`workbench.yaml`） |
| 系统可扩展 | 五个维度扩展点：Provider / Template / Tool / Agent / Identity | 每个扩展点要有稳定契约 |
| 一套 API 供人机共用 | REST 与 MCP 共用同一批 `system_tools` 函数 | 工具函数签名需同时满足两者 |
| 分层可替换 | 控制面绝不直接调用云 SDK | 每个云能力要写适配器 |
| 多租户 + 企业登录 | 组织是租户边界；权限是目录、角色是集合、路由是映射；五种 SSO 走同一个 IdP SPI | 每个请求多两次查询，换来权限即时生效 |
| 一台机器能跑通全部流程 | 没有 Key 有规则规划器，没有 IdP 有模拟 IdP，没有云有 local Provider | 每层都要有"诚实降级"的实现 |

**核心判断**：这个平台的复杂度不在"创建项目"，而在"创建项目之后要动它"。
所以真正的架构重心放在三处——资源供应的 Provider 接缝、模板的声明式契约、
以及 `system_tools` 这一层"人机共用"的操作面。

---

## 2. 分层结构

```
┌─────────────────────────────────────────────────────────────────────┐
│  Frontend (Next.js 15 / App Router)                                 │
│  /  /projects  /projects/[id]  /templates  /agents  /mcp  /skills   │
│  /access  /login  /auth/callback  /invite  /settings  /events       │
│  纯客户端组件 + fetch 封装（lib/api.ts）+ 会话状态机（lib/session）  │
└───────────────────────────┬─────────────────────────────────────────┘
                            │ REST /api/v1   Bearer wbs_…
┌───────────────────────────▼─────────────────────────────────────────┐
│  中间件  principal_middleware（身份解析 + 路由表判权，覆盖 /mcp）     │
├─────────────────────────────────────────────────────────────────────┤
│  API 层  app/api/*            （薄：校验 → 调 service → 序列化）      │
│  auth / orgs / projects / workspace / platform / registry / system  │
├─────────────────────────────────────────────────────────────────────┤
│  服务层 app/services/*        （厚：全部业务逻辑在这里）              │
│  identity      登录、JIT 建号、IdP 分组→角色映射、会话               │
│  audit         谁做了什么、谁被拦了（含来源 IP）                     │
│  scaffold      项目生命周期 + 文件树 + 装配                          │
│  conventions   三类项目规范，可执行的约定（见 §9.1）                  │
│  templates     模板目录与渲染                                        │
│  gitops        唯一的 git 接缝：status/log/diff/commit/tag           │
│  deployments   预览/运行/发布三动词 + 日志流 + 状态机                 │
│  system_tools  人机共用的操作面（REST 与 MCP 的唯一实现）             │
│  agent_runtime 意图解析 → 工具编排 → 步骤记录                         │
│  serializers   ORM → API 契约                                        │
├─────────────────────────────────────────────────────────────────────┤
│  Provider SPI  app/providers/base.py  （四类能力的抽象接口）          │
│  storage  database  domain  deploy                                  │
│  ↳ 实现：providers/local/{storage,database,domain,deploy}.py        │
│  ↳ 预览宿主：providers/local/static_host.py（无依赖也能托管）        │
│  ↳ 身份：providers/auth/{cas,oauth2(OIDC),wecom,dingtalk}.py        │
├─────────────────────────────────────────────────────────────────────┤
│  持久化 app/models.py（SQLModel）+ app/core/db.py                   │
│  业务：projects / resources / domains / env_vars / deployments      │
│        mcp_servers / skills / agents / agent_runs / events          │
│  身份：users / organizations / roles / memberships / role_bindings  │
│        identities / identity_providers / sessions / invitations     │
│        auth_requests / audit_logs                                   │
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

身份与租户是另一棵子树，与业务表只通过 `project.org_id` 相接：

```
Organization ──1:N── Membership ──N:1── User ──1:N── Session
      │                 └── role_key ──┐        └──1:N── Identity ──N:1── IdentityProvider
      │                                │                     （每个 IdP 一个 subject，可多个并存）
      ├──1:N── Role（含 5 个内置，key 唯一）
      ├──1:N── RoleBinding（project_id + user_id + role_key，项目级授权）
      ├──1:N── Invitation（email + role_key + token 摘要 + 过期）
      ├──1:N── IdentityProvider（kind + config + 状态机）
      ├──1:N── AuthRequest（一次性 state / nonce / PKCE verifier）
      └──1:N── AuditLog（actor + action + resource + 结果 + 来源 IP）
```

设计要点：

- **ID 带前缀**（`proj_ab12cd`、`org_9372f4d09ee5`、`wbs_…`）：可安全暴露在 URL
  与日志里，一眼看出类型。
- **JSON 列而非散列**：`Project.meta` / `ProjectResource.config` / `AgentRun.steps` /
  `Skill.manifest` / `IdentityProvider.config` 承载自由结构。新增模板选项、Provider
  参数或一种 SSO 的字段**都不需要迁移**。
- **时间统一 UTC**，序列化时才转本地；`created_at` 全部带索引，事件流与列表页靠它排序。
- **`template_version` 固化在项目上**：模板升级不追溯已创建的项目，避免"昨天还能跑今天跑不起来"。
- **组织是租户边界，用户不是**：同一个 `User` 可以属于多个组织，身份（`Identity`）挂在
  用户上而非组织上 —— 换了组织、换了 IdP，人还是同一个人。项目通过 `org_id` 归属，
  角色通过 `Membership` 授予，两者不在同一行上，删组织不会连坐删用户。

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
| 预览域名 | `<slug><WB_PREVIEW_SUFFIX>.<WB_DOMAIN_SUFFIX>` | 项目 `workbench.yaml` 的 `preview_suffix` |
| 端口 | `WB_PORT_RANGE_START..END` 内两个空闲 | — |
| 调试/运行/构建命令 | 类型规范的 `commands` | 项目 `workbench.yaml` |
| 健康检查路径 | 类型规范的 `health_path` | 同上 |
| 环境变量注入 | 部署时读 `EnvVar` → 进程环境 | `scope` 字段区分环境 |
| 发布版本 | `git tag deploy/v<N>` | — |

这套约定在本轮被收敛成可执行数据（`TypeSpec`），完整表与逐类型细节见
**[docs/conventions/](conventions/README.md)**；实现与取舍见 §9.1。

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

### 工具清单（34 个）

按用途分七组，同一份 `TOOLS` 字典同时喂给 REST、MCP 与 Agent：

| 组 | 工具 |
| --- | --- |
| 元信息 | `describe_workbench` `list_templates` `list_agents` |
| 项目 | `list_projects` `get_project` `create_project` `add_domain` |
| 代码 | `list_project_files` `read_project_file` `write_project_file` `delete_project_file` `search_project` `check_project_conventions` |
| 运行 | `run_project` `deploy_project` `deployment_status` `start_preview` `stop_preview` `preview_status` `publish_project` |
| 版本 | `git_status` `git_log` `git_diff` `git_commit` `git_restore_file` `git_revert_commit` |
| 资源 | `list_env_vars` `set_env_var` `query_database` |
| 身份 | `describe_access` `check_permission` `list_organizations` `list_org_members` `list_sso_providers` |

`describe_workbench` 是给模型用的"说明书"：约定、Provider、模板、规划器、工具清单
一次给全，省掉三轮试探。`start_preview` / `publish_project` 这类动词之所以也在
`TOOLS` 里，是因为"改完自己看一眼再发布"是 Agent 闭环的必要一步，不能只留在界面上。

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

`app/services/agent_runtime.py` 是**可替换的 Harness 抽象**，内置两个实现，按配置选择：

```
WB_AGENT_HARNESS=auto ──┬─ 有 WB_LLM_API_KEY ──▶ LlmHarness
                        └─ 无 key ───────────▶ LocalHarness
```

### 7.1 LlmHarness —— 真实 function calling

```
prompt ──▶ 组 system（约定 + describe_workbench 结果）
       ──▶ llm.chat(messages, tools=schemas)
       ──▶ 有 tool_calls？ 执行 → 追加 role=tool 消息 → 回到上一步
       └─ 无 tool_calls  ──▶ 模型的口头结论 + 轨迹渲染 → 落库
```

关键设计：

- **工具 schema 由签名生成**。`llm.tool_schema(fn)` 读 `inspect.signature` 与
  `get_type_hints`，把 `Optional[X]` 降级为"非必填"、`list[dict]` 变成
  `array<object>`、有默认值的参数带 `default`。因此**加一个 Agent 能力 = 加一个
  带类型注解和 docstring 的函数**，不写一行 JSON schema。
- **客户端只有 20 行**：`httpx.post("{base_url}/chat/completions")`。OpenAI /
  Azure / vLLM / Ollama / 通义兼容模式共用同一个实现，不绑定任何厂商 SDK。
- **单步失败不炸整轮**：工具异常被转成 `{ok:false, error}` 回灌给模型，让它自己
  换参数重试；只有模型端点本身不可达才抛 `HarnessError`。
- **降级而非报错**：`HarnessError` 带上已完成的 steps，`run()` 捕获后跑一遍
  LocalHarness，把失败点作为 `__harness__` 步骤插入轨迹，`status` 仍按真实工具
  成败判定，并置 `fallback=true`。用户拿到答案，同时知道这次不是模型想的。

### 7.2 LocalHarness —— 确定性路由器

关键词 → 意图 + 槽位 → 固定工具序列。没有 key、没有网络、没有随机性，
因此可离线演示、可断言、可作为降级底座。

```
prompt ──▶ 意图解析（关键词）
       ──▶ 计划生成（intent → [tool_call, ...]）
       ──▶ 逐步执行（每步独立捕获异常，单步失败不中断整体）
       ──▶ 观察聚合（把每步 result 提炼成人类可读结论）
       ──▶ 落库 AgentRun（steps 完整留痕，status + output）
```

**权限隔离**：`Agent.permission_scope` 三档——

| scope | 可用工具 |
| --- | --- |
| `readonly` | 只读类（describe / list / get / read_file / query_database / deployment_status） |
| `project` | 读 + 改文件、改环境变量、部署（**不能删项目**） |
| `full` | 全部，含删除与跨项目操作 |

权限在 Harness 拿到 `tools` 字典之前就过滤掉了，模型根本看不见越权工具 —— 不是靠
提示词自觉。

**换框架**（agentscope / langgraph / 自研 ReAct）：继承 `Harness`、实现
`execute(task) -> (output, steps)`、调 `register_harness()`。执行层与落库层零改动。

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

## 9. 工作区：编辑 · 调试 · 版本 · 发布

前八节解决"怎么把项目造出来、怎么供应资源"。这一节解决另一半：**造出来之后怎么改**。
四个动作（改代码、调试、版本、发布）在无人值守的场景里必须能被 Agent 独立完成，
所以它们全部落在 `system_tools` 上，界面只是其中一个调用者。

```
        ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
        │  界面（人）  │  │  MCP（外部） │  │ Agent（内建）│
        └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
               └─────────────────┴─────────────────┘
                                 ▼
                       system_tools（29 个工具）
        /            │            │            │            \
   write_project   start_       publish_     git_*       check_project
     _file        preview       project                  _conventions
        │            │            │            │            │
        ▼            ▼            ▼            ▼            ▼
   scaffold     deployments   deployments   gitops     conventions
        └────────────┴────────────┴────────────┴────────────┘
                                 ▼
                    data/projects/<slug>（一个 git 仓库）
```

### 9.1 约定即数据：`conventions.py`

这是本轮的核心判断：**"约定大于配置"如果不写成可执行的数据，就只是一句口号**。
所以每个项目类型对应一个 `TypeSpec`：

```python
TypeSpec(type="fullstack", layout=[...], commands={"dev": "...{port}", "run": "..."},
         health_path="/health", resources=["storage", "database"], conventions=[...])
```

一份定义同时被五处消费：

| 消费者 | 用法 |
| --- | --- |
| `scaffold.create_project` | 决定模板渲染上下文、分配端口数量、绑定几个域名 |
| `GET /projects/{id}/conventions` | 生成"通过/告警/失败"报告（`Check(key,title,status,detail)`） |
| 规范标签页 | 直接渲染同一份报告，不重复实现判定 |
| `check_project_conventions` | Agent 在改码前先自检 |
| `resolve(project)` | 解析出真正要执行的 `dev/build/run` 命令与 `health_path` |

覆盖只有一个入口：项目根的 `workbench.yaml`。`load_manifest()` 读它，
`Resolved.overrides` 记录哪些字段被覆盖过，报告里会标出来 —— 逃生舱要有痕迹。

**为什么不用配置文件而是推导**：配置会腐烂（改了目录忘了改配置），而 slug 不会。
代价是"想不一样"时必须走唯一那个覆盖文件，这反而是好事：差异集中且可见。

### 9.2 调试环境：`<slug>-dev` 是第一等公民

调试不是一个模式开关，而是一整套平行环境：

| | 生产 | 预览 |
| --- | --- | --- |
| 域名 | `<slug>.<suffix>` | `<slug><WB_PREVIEW_SUFFIX>.<suffix>` |
| 端口 | `project.port` | `project.dev_port`（创建时就分配好） |
| 命令 | `run_command` | `dev_command`（带热更新） |
| 部署锁 | `project:production` | `project:preview` |
| Env | `PORT` / `PUBLIC_URL` | 额外 `WB_PREVIEW=1`、`PREVIEW_URL` |

两条环境各自持有端口、进程与锁，所以重启预览永远压不到生产 —— 反之亦然。

**降级只朝一个方向，且必须自报家门**：

```
1. dev server       依赖齐备 → 真跑 dev_command，端口探活成功才算 running
2. static host      没有 node_modules/.venv → 按 dist/ → build/ → out/ → index.html
3. explainer page   什么都托管不了 → 渲染文件树 + README + 该跑的命令
```

实际用了哪一级记在 `Deployment.image`（`process` / `static:dist` / `static:explainer`），
界面显示为标签。**宁可承认"这是说明页"，也不能把死端口塞进 iframe** ——
这正是本轮修掉的一个真问题。

### 9.3 版本控制：git 是事实来源

`gitops.py` 是进程里唯一碰 git 的地方（全部命令带 `-c user.name=...` 以免依赖全局配置）。
它提供 `status / log / diff / commit_diff / commit / restore / revert / tag`，
读操作容忍空仓库，写操作在"没东西可提交"时返回 `{"committed": false}` 而不是抛错 ——
没变化是正常结果，不是异常。

界面里刻意暴露的是**读**（status/log/diff/show）与**三个安全写**（commit / restore 单文件 /
revert 提交），没有 `reset --hard`、没有 `push --force`：历史只追加。

### 9.4 发布 = 提交 → 打标签 → 部署那个提交

```
publish(project)
  ├─ gitops.commit()                     工作区落一个提交（无变化则复用 HEAD）
  ├─ version = v<n>                      按 environment 计数
  ├─ git tag deploy/v<n> HEAD
  ├─ Deployment(commit_sha=HEAD)         把"跑的是哪一版"写进部署行
  └─ run_deployment()
```

`Deployment.commit_sha` 是"线上跑的东西"与"仓库里的东西"之间的唯一连接。
没有它，"回滚"只能靠猜；有了它，回滚就是 `git_revert_commit` + 再发布一次，
而 `deploy/v<n>` 标签是可指向、可 diff 的锚点。

### 9.5 编辑器：零依赖的语法高亮

不做 Monaco 集成（体积与依赖都不划算），而是透明 `<textarea>` 叠在与它**字体度量完全一致**
的已高亮 `<pre>` 上：输入交给 textarea，显示交给 pre，滚动同步到行号槽。
高亮器是按语言的粘性正则分词（`js/py/json/css/html/md/sh`），超过 16 万字符自动降级为纯文本。
快捷键：`Ctrl/⌘+S` 保存、`Tab`/`Shift+Tab` 缩进、`Enter` 自动缩进。

关键约束：它写盘调用的就是 `write_project_file`，**不存在只有界面能走的特权路径**。

---

## 10. 部署流水线

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
异步 + 流式输出实现简单、可断线重连、日志天然可增量。

### 10.1 并发控制：三道闸

部署是最容易出竞态的地方，所以设了三层：

1. **状态闸**：`create_deployment` 先查同项目是否存在 `queued|building|deploying`
   的记录，有则 `409 Conflict` 并回带在跑那个的 id —— 前端能直接跳过去看。
2. **进程内锁**：`_RUN_LOCKS[project_id]` 的 `asyncio.Lock`，防止 stop/retry 与
   后台任务在同一进程里交错。
3. **版本号**：`v<n>` 在创建时按已有条数计算，配合状态闸不会出现两个 v3。

### 10.2 日志传输：SSE 而不是轮询

```
GET /deployments/{id}/stream        text/event-stream
  event: log     {"lines": [...]}     ← 只推增量（按已发行数切片）
  event: status  {"status": "...", ...}
  event: done    {...}                ← 终态即关闭
```

选择理由：日志是单向、高频、无历史回放需求的流，SSE 用原生 `EventSource`
即可，不需要 WebSocket 的双向与心跳；`EventSource` 不能带 header，所以鉴权额外
支持 `?token=`（`security.extract_token` 唯一一处为此让步）。

前端不再有 `setInterval`：拿到初始快照后，若状态是活跃的才开流，`done` 时关流并
刷新列表。轮询间隔这个"魔法数字"彻底消失。

---

## 11. 安全与护栏

鉴权、SSO、SQL、迁移、回滚五件事彼此独立，但共用一条原则：**边界写在数据里，
判定点只有一个。**

### 11.1 组织、角色与权限内核

传统做法是把角色写进代码（`if role == "admin"`），代价是"再加一种人"必须发版。
这里反过来：**权限是目录，角色是权限集合，路由是映射表，全部是数据。**

```
core/permissions.py
  PERMISSIONS     35 项 domain:action —— 唯一声明处
  BUILTIN_ROLES   5 个内置角色 = 一组 key，没有继承图
  ROUTE_RULES     正则 → 权限，按序首个命中
```

一次请求的判权链路：

```
request ──▶ principal_middleware
              ├─ WB_AUTH_ENABLED=false → 匿名 admin，放行（本地默认）
              ├─ PUBLIC_API_PREFIXES（health / settings / auth/*）→ 放行
              ├─ Bearer wbs_… → SHA-256 查 Session → 未命中即 401
              ├─ 装配 principal: user + 钉住的组织 + 有效权限集合
              │     = expand(Membership.role_key) ∪ expand(RoleBinding.role_key)
              ├─ permission_for(method, path) → (所需权限, project_id)
              │     未登记的读只需登录，未登记的写一律回落 system:manage（fail closed）
              ├─ 缺权限 → 先写审计，再 403
              └─ set_principal / set_client_ip 到 ContextVar，交给处理器
```

处理器再判一次（`_require`），因为路径里的组织可能不是会话钉住的那个：

```python
org, membership = _require(session, principal, org_id_from_url, "member:manage")
```

**为什么是两层而不是一层。** 中间件看得到路径，看不到语义：`POST
/orgs/{id}/members` 的 `{id}` 是不是我的组织，只有处理器知道。中间件负责"这套
API 该不该给这个权限"，处理器负责"这个资源是不是你的"。少任何一层都会出现漏判。

**为什么中间件是唯一可行的挂载点。** `/mcp` 是 `app.mount("/")` 挂进来的独立
ASGI 应用，依赖注入管不到它；中间件在挂载之前执行，于是 REST 与 MCP 共用同一套
身份解析 —— 不会出现"REST 要登录、MCP 裸奔"的经典漏洞。但 MCP 是一条长连接，
一个 `POST /mcp` 里可能连发十个工具调用，路径判权看不出这次到底想干什么，所以
MCP 侧在**工具层**再判一次（§6）：`TOOL_PERMISSIONS` 把每个工具映射到一项权限，
`_guard()` 用 ContextVar 里的调用者主体展开判定。

> 「An agent may do exactly what the human who invoked it may do.」
> —— Agent 不是超级用户，它是调用者的一个代理。同一个 `write_project_file`，
> 开发者调得通、只读用户调不通，与 REST 上的结论完全一致。

**404 而不是 403。** 处理器发现组织或项目不属于主体时返回 404 —— 403 等于承认
"这个 id 存在"，可以拿来枚举别家组织。

**拒绝也要留痕。** 审计写入发生在 403 抛出之前，所以"谁在什么时候被什么规则挡住"
是能查的，而不只是"成功操作列表"。

**权限集是每次请求重算的**，不塞进 JWT、不缓存在会话行上。代价是每个请求多两次
查询，换来的是"收回权限立刻生效"——对一个能部署生产的环境，这个交换永远划算。

**内置角色用通配符表达**，`owner` 就是一个 `*`：目录从 35 项长到 80 项时它自动
跟上，不会像手写列表那样腐化。自定义角色存的是展开后的具体 key，导出/审阅时一眼
能读完。

前端不重复实现这套逻辑：`/permissions` 返回目录与角色，`lib/access.ts` 负责把
grant 展开成集合，`can()` 只问"我的集合里有没有这一项"。导航栏、按钮、标签页
的显隐与后端是同一条判定 —— **藏起来的按钮背后，接口也真的会拒。**

#### 数据范围：权限管"做什么"，范围管"对谁的"

权限回答了"能不能"，没回答"对哪一行"。再加一层 RBAC 也解决不了这个问题 ——
真正想要的是"开发者只能碰自己的项目"，而项目是**数据**，不是权限。

于是范围挂在角色上（`roles.data_scope`：`all` | `own`），判定收敛成一个谓词：

```python
def reaches(self, *, owner_id="", org_id="", project_id="") -> bool:
    if org_id and self.org_id and org_id != self.org_id and not self.is_platform_admin:
        return False                      # ① 租户：别人的组织一律不通，超级管理员也不通
    if self.sees_all_projects():
        return True                       # ② 范围：all 直接短路
    if project_id and project_id in self.grants:
        return True                       # ③ 授权：RoleBinding 点名的项目
    return bool(owner_id) and owner_id in {self.user_id, self.email, self.name}
```

三个判断的顺序是刻意的：**租户先于范围**（A 组织的 owner 不是 B 组织的 owner），
**范围先于归属**（`all` 不该为每一行都去查 owner），**归属兼容历史列**（早期
`owner_id` 存的是邮箱或姓名，升级后仍要能匹配上）。

四个调用点共用它，所以不会出现"列表过滤了、详情没过滤"这种最典型的越权：

| 调用点 | 行为 |
| --- | --- |
| `visible_projects()` | 列表逐行过滤 |
| `require_project()` | 详情/子资源，够不着就是 404 |
| `system_tools._guard()` | MCP 工具先解析 id/slug，再判范围，最后判权限 |
| `agent_runtime.run()` | 入参 `project_id` 够不着时，**在写 run 记录之前**就拒 |

**403 与 404 的分工**：中间件先跑，缺权限是 403（"你的角色不能改代码"）；处理器
后跑，够不着是 404（"没有这个项目"）。两种拒绝都不确认对方的行是否存在，但把
"权限不足"和"不是你的"分开，日志和用户提示才有意义。

**计数也是数据。** `/system/overview` 的徽标与信息流同样过谓词，超范围项目的
运行记录与事件直接丢弃 —— 否则"16 个项目"对一个只能打开 1 个的人就是泄漏。

**升级不能让人丢项目。** 旧库里的项目可能没有 `owner_id`、或存着邮箱/姓名。
`migrate.py` 每次启动跑三条幂等回填：组织认领最早那个 `owner` 成员 → 邮箱/姓名
归一成用户 id → 孤儿项目挂到组织所有者名下。回填的 WHERE 条件只匹配仍是空值的
行，所以运维手工改过的数据不会被覆盖。

**为什么不做成 SQL 层的 WHERE。** 单文件 SQLite 上逐行过滤比多一次往返更便宜，
而且能把"判断"留在一个地方；换成 Postgres 多副本时，`visible_projects()` 就是
唯一需要改成 `WHERE owner_id = :uid OR id IN :grants` 的函数。

验证：`backend/tools/auth_smoke.py` 里 9 条断言覆盖了这些分支 —— 他人项目列表不
可见、详情/读文件/写文件 404、被授权项目可开、`all` 范围看得到全部、开发者建完
自己的项目后仍看不见别人的、以及概览计数按范围收敛。

### 11.2 SSO：五条流程，一条代码路径

CAS / OAuth2 / OIDC / 企微 / 钉钉的握手细节完全不同，但形状一样：跳到对方、回来
带个 code、换出"你是谁"。

```python
class IdP(ABC):
    kind: str
    SCHEMA: dict          # 字段声明 —— 界面表单由它渲染
    def authorize_url(self, ctx) -> str: ...
    def exchange(self, code, ctx) -> ExternalProfile: ...
```

`SCHEMA` 是这里最关键的设计：`label/help` 中英双语、`type` 是
`text|password|select|json|switch|list`、`required`、`secret`（回显时打码，空值
表示"保留原值"）。加一种 IdP = 一个类 + 一个 SCHEMA，**前端一行都不用改**。

```
GET  /auth/providers/{key}/start   → 建 AuthRequest(state, nonce, verifier)，302 到 IdP
GET  /auth/{kind}/callback?code&state
        ├─ state 一次性消费（CAS 除外：它藏在 service URL 里）
        ├─ exchange → ExternalProfile(subject, email, name, groups, raw)
        ├─ 找 Identity(subject) → 有则登录
        ├─ 无则 JIT 建号：email 命中既有用户就绑定，否则新建 User
        ├─ 角色 = config.role_map 命中 groups 中 rank 最高的那个，否则 default_role
        └─ 302 到 {WB_FRONTEND_URL}/auth/callback#token=wbs_…（放进 fragment，不上服务器）
```

各家的坑，都在各自的 provider 里处理完了：

| 类型 | 必须处理的事 |
| --- | --- |
| OIDC | 走 discovery 拿 `jwks_uri`，只认 RS256/ES256；`alg=none` 直接拒；校验 `iss`/`aud`/`nonce`；userinfo 只是加分项，身份以 `id_token` 为准 |
| OAuth2 | 授权串用 `urlencode` 拼接而非 `params=`（后者会整串替换掉 `redirect_uri`）；只有对方声明支持时才带 PKCE |
| CAS | state 塞进 `service` 的 query —— CAS 协议本身没有 state 参数；`p3/` 前缀按版本切换；用户字段若是 "proxy ticket"/"service ticket" 字样一律拒，防票据注入 |
| 企微 | 扫码用 `login.work.weixin.qq.com/wwlogin/sso/login`，企微内打开用 `open.weixin.qq.com/connect/oauth2/authorize`，按 UA 选；`auth/getuserinfo` 拿到 `userid` 后再 `user/get` 补全资料 |
| 钉钉 | `api.dingtalk.com/v1.0/oauth2/userAccessToken` 换 token，再带 `x-acs-dingtalk-access-token` 请求 `contact/users/me`；主体取 `unionId` 而不是 `userId`（换应用不换人） |

**连接是有状态的**：`unchecked`（存过但没验过）→ `ready`（探测通过）/
`error`（探测失败），另有 `enabled` 开关。只有真发过一次请求的连接才允许显示
`ready` —— 界面上"绿色的 SSO"必须意味着"现在能登"，否则这个绿灯毫无意义。

**探测不写库外的东西**：它只做 discovery / authorize URL 构造 / app token 获取，
不会真的登一个用户进来，所以可以在生产上随手点。

纯 SSO 部署怎么起第一个管理员？`WB_AUTH_BOOTSTRAP_TOKEN`：带着它可以直接调
`/orgs` 建组织并成为 owner，用完即撤。见 [docs/access.md](access.md)。

### 11.3 SQL 护栏：白名单 + 逃生舱黑名单

数据库控制台连的是真实 sqlite 文件，所以"用户写什么就执行什么"是明显的坑。护栏
不写 SQL 解析器，用两步：

```
1) 去注释 → 去尾分号 → 检测残留 ';'（多语句直接拒）
2) 首关键字白名单：readonly 只放 SELECT/WITH/EXPLAIN/PRAGMA/SHOW/DESCRIBE/ANALYZE
3) 逃生舱黑名单：ATTACH / DETACH / load_extension( / writefile( / readfile( /
                 writable_schema / PRAGMA x=y / VACUUM INTO
4) PRAGMA 再按名字白名单（table_info、index_list、foreign_key_list…）
```

取舍：**比正则沙箱严，比真解析器简单**。首关键字方案会误拒 `WITH ... DELETE` 这类
写法，但那是"少一个便利"，而漏放 `ATTACH` 是"整台机器失守"—— 不对等，所以选前者。

`WB_SQL_MODE=write` 放开语句类型限制，但黑名单永远生效。

### 11.4 前向兼容迁移

`SQLModel.metadata.create_all` 不会 ALTER 已存在的表，于是"给 `Project` 加一列"
在已有库上会静默炸掉所有 select。`core/migrate.py` 在启动时补齐：

```
inspect(engine) → 对比 COLUMNS 声明 → ALTER TABLE ... ADD COLUMN → 建幂等索引
```

规则只有一条：**只加，不改，不删**。需要重命名就新增一列 + 双写 + 后续清理，
真正的迁移工具（Alembic）留到多副本部署时再上。

唯一的例外是 `BACKFILLS`：新增列时必须给旧行一个合理默认值（`data_scope`、
`owner_id` 都是这样）。每条 UPDATE 都写成"只匹配仍为空值的行"，因此可重复执行，
且永远不会覆盖运维已经手工调整过的数据。

### 11.5 创建回滚

项目创建横跨四个 Provider，任何一步失败都不能留下半套云资源：

```
写工作区 → 建桶 → 建库 → 写 env → 绑域名 → git init
              │
              └─ 任一步抛错 → _rollback():
                   反向逐项撤销（每个动作单独 try，撤销失败不影响其它）
                   + 删工作区 + 删资源/域名/env 行
                   + 项目置 failed 并记录 rolled_back 清单
```

保留 `failed` 的项目行（而不是删掉）是刻意的：UI 上能看到"哪个项目失败了、为什么"，
事件流里也有记录；但外部资源一定已经清干净，不会占用桶名或域名。

---

## 12. 前端表现层：i18n 与布局偏好

表现层只有两条规则：**文案不进组件、偏好不进数据库**。

### 12.1 类型化字典

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

### 12.2 布局偏好

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

### 12.3 响应式：断点属于 CSS，不属于组件

导航位置是"用户偏好"，窄屏形态是"视口事实"，两者不能混：`data-nav` 由用户
决定，`@media (max-width: 640px)` 由 CSS 决定，后者始终覆盖前者。

三个断点，职责单一：

| 断点 | 变化 |
| --- | --- |
| ≤1100px | 四列 KPI 降两列，`/agents` 的列表+对话并排改上下 |
| ≤860px | 侧栏收成 68px 图标条；双栏 grid 全部塌单列；表格获得横向滚动与可见滚动条 |
| ≤640px | 侧栏变底部标签栏；面包屑去掉项目 id；顶栏只留必要控件；点按目标放大到 34px；toast 上移避让底栏 |

**分栏比例不能写在内联样式里。** 这是本轮唯一的结构性修复：页面上原本是

```tsx
<div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.35fr) minmax(0,1fr)" }}>
```

内联样式媒体查询够不着，于是窄屏上两列仍是 1.35fr/1fr，文字被压成一字一行。
现在比例通过自定义属性传递，断点统一在 `globals.css` 里拥有：

```tsx
<div className="split" style={{ "--split": "minmax(0,1.35fr) minmax(0,1fr)" } as CSSProperties}>
```

```css
.split { grid-template-columns: var(--split, minmax(0,1.5fr) minmax(0,1fr)); }
@media (max-width: 1000px) { .split { grid-template-columns: minmax(0, 1fr); } }
```

同理，`.card > .table` 统一包一层 `.table-wrap`：**让表格在自己卡片里滚动，
而不是把整页撑宽**。`scripts/explore.mjs` 会遍历所有页面与标签页，把
`scrollWidth > clientWidth` 的页面连同越界元素一起报出来——响应式改动靠它守。

---

## 13. 内置反向代理：一个监听，按 Host 分流

### 13.1 为什么不是一个端口一个网址

每个项目都会分到端口（`4200–4399`），因为进程就是按端口起的。但端口不该出现在
用户看到的地址里：它既没法发给别人，也让"预览"和"生产"看起来像两个不同的东西。
于是地址被拆成两件事：

| 谁决定 | 什么 |
| --- | --- |
| `projects` 表 | 项目占哪个端口、生产域名、预览域名 |
| 网关 | 这两个域名都指向同一个监听（`:80`/`:443`），按 `Host` 转发 |

```python
# conventions.py —— 三种寻址形态，一个函数
def origin_for(fqdn, port, *, via_gateway=None):
    if settings.domain_base_url:          # 已有外部反代/隧道
        return settings.domain_base_url
    if via_gateway:                        # 内置网关：端口消失
        scheme, gport = settings.gateway_scheme, settings.gateway_port
        default = 443 if scheme == "https" else 80
        return f"{scheme}://{fqdn}" if gport == default else f"{scheme}://{fqdn}:{gport}"
    return f"http://{fqdn}:{port}"         # 没网关：老实写端口
```

`gport != 默认端口` 时依然把端口写出来——给一个真正的 `:8080` 监听发无端口网址，
只会得到一个连不上的链接。

### 13.2 路由表从哪来

网关是**独立进程**（`python -m app.gateway`），因为它要在控制面重启时继续服务，
而且整套栈里只有它需要特权端口。它不订阅事件、不共享内存，只有一个带 TTL
（默认 2s）的缓存：

```python
def _load(self) -> dict[str, Route]:
    with Session(engine) as session:
        projects = session.exec(select(Project)).all()
        bindings = session.exec(select(DomainBinding)).all()
    # <slug>.<suffix>       -> project.port       (production)
    # <preview_fqdn>        -> project.dev_port   (preview)
    # 自定义 DomainBinding  -> 生产端口（除非它就是预览域名）
```

`Route` 只可能指向 `WB_GATEWAY_UPSTREAM_HOST`（默认 `127.0.0.1`）上的**项目行里
的端口**。没有任何路径能让请求头影响上游地址——这是"自建反代"最容易变成
"开放代理 / SSRF 跳板"的地方，所以这一层不做任何用户输入拼接。

### 13.3 三个容易写错的细节

| 细节 | 做法 | 不这么做会怎样 |
| --- | --- | --- |
| 上游的 `Host` | 改成 `localhost:<port>`，真实域名放 `X-Forwarded-Host` | Vite 6 会直接 `403 Blocked request`，Next 会告警；现象像鉴权 bug |
| 请求体 | ≤1MiB 先缓冲，附 `Content-Length`；更大才流式 | WSGI 栈读不了 chunked 请求体，表单提交"永远是空的" |
| 响应 | `aiter_raw()` 逐块转发，不缓冲 | SSE 与日志尾随全部失效，等首字节等到超时 |

另外：`Location` 指向上游自身 origin 时改写为公开域名（否则浏览器会跳到
`localhost:4200`，外面根本连不上）；WebSocket 升级按帧双向转发（HMR 靠它），
只带 `cookie/authorization/origin/user-agent`，握手头由 websockets 客户端重新生成
（转发 `Sec-WebSocket-Key` 会因重复头拿到 400）。

### 13.4 失败页是产品的一部分

项目没在运行时，上游端口不会应答。这时网关自己出页面——项目名、环境、上游端口、
一句"点部署/启动预览"，而不是裸 `502`。状态页在 `http://localhost:80/__gateway/`，
列出全部路由与上游。这两个页面不读任何用户数据，也不转发到项目。

### 13.5 寻址形态变了，旧行怎么办

URL 是**存下来的**（部署行指着它）。把网关打开，历史项目还挂着带端口的旧地址，
所以 `seed._heal_endpoints()` 每次启动重算一遍：

```python
def normalize_endpoints(project) -> bool:
    # 预览地址永远按当前形态推导
    # 生产地址只在「空」或「本来就是我们自己发的域名」时才改写，自定义域名不动
```

`owns_host()` 用域名后缀判断归属，所以绑过自定义域名的项目不会被"治愈"掉。

### 13.6 TLS：通配符优先，http-01 兜底

同一件事有两种做法，优先级是写死的：

| 方式 | 覆盖范围 | 代价 | 用在哪 |
| --- | --- | --- | --- |
| 通配符证书（`*.example.com`） | 所有项目域名 + 所有预览域名 | 一对文件，人工续期 | 首选：`WB_GATEWAY_TLS_CERT/KEY`，或把 `<域名>.pem` 丢进 `data/tls/` |
| ACME http-01（`app.gateway.acme`） | 逐个域名 | 需要 80 端口对公网可达、A 记录指着这台机器 | 通配符覆盖不到的：客户自带域名、后缀外的域名 |

通配符优先不是偏好问题：它一次覆盖将来才会创建的项目，而 http-01 只能给**已经
存在**的域名签，且每个域名一次订单，还要承受 CA 的速率限制。所以通配符在场时
续期器只补它覆盖不到的名字。

**一个监听，SNI 选证书。** 证书不是"配给网关"的，是"配给名字"的：
`CertStore` 扫描目录，按 `精确 → 通配 → 默认` 解析每个 SNI，再切换 `SSLContext`。
手写 nginx 配置时为每个 `server_name` 加一段 `ssl_certificate`，这里是同一件事，
只是数据来自文件而不是配置。

```text
<项目>.example.com      -> *.example.com        (通配符，一个文件覆盖全部)
custom.customer.com     -> custom.customer.com  (单独签发，精确匹配优先)
(没有匹配)              -> 默认证书              (浏览器报名字不匹配，而不是连不上)
```

**为什么要自签 bootstrap。** 首次签发时还没有任何证书，但 443 得先能监听，
否则 `acme` 的验证永远等不到 80 端口的 challenge。所以没有证书时用一个 30 天的
自签 `*.后缀` 绑定 443，签发完成后下一次握手自动换成真证书——**不需要重启**。
证书与上下文按 mtime 缓存 3 秒，所以人工换证书、ACME 续期都走同一条热加载路径。

**http-01 的 challenge 在 proxy 里直答。** 路径前缀 `/.well-known/acme-challenge/`
在路由查表**之前**处理：它必须对不在路由表里的域名生效（这正是"域名第一次变成
可信"的过程），而且不能被 301/308 到 https（CA 只会用明文 HTTP 访问）。
80 端口同时承担"其余请求 308 到 https"。

**控制面不下发证书，只下需求。** `POST /system/tls/issue` 不跑订单，而是往
`data/tls/queue/<host>.json` 写一条请求，网关的续期循环来执行。理由是：challenge
必须由持有 80 端口的进程回答，且证书只应有一个写者——两个进程同时写一对
pem/key，结果就是"证书与私钥不匹配，443 握手失败"。代价是两边必须共享
`data/tls`（单机部署天然满足）；队列文件本身就是可读的状态，UI 直接展示
`pending / running / done / error` 与失败原因。

---

## 14. 扩展点总览

| 想做的事 | 动哪里 | 要不要改前端 |
| --- | --- | --- |
| 换云存储 / 云数据库 / DNS / 部署目标 | 新增 `providers/<cloud>/*.py` + 改 `.env` 一行 | 否 |
| 加一套新模板 | `templates.py` 加一个 dataclass | 否（目录自动展示） |
| 加一类项目（改变目录/命令/健康检查） | `conventions.py` 加一个 `TypeSpec` | 否（规范页/预览/Agent 自动同步） |
| 加一个 Agent 可用的能力 | `system_tools.py` 加函数 + 注册进 `TOOLS` | 否（REST/MCP/Agent 自动同步） |
| 换模型供应商 | 设 `WB_LLM_BASE_URL` / `WB_LLM_MODEL` | 否 |
| 换 Agent 框架 | 继承 `Harness` + `register_harness` | 否 |
| 放开 SQL 写入 | `WB_SQL_MODE=write` | 否 |
| 加角色 | `/access` 的角色编辑器，写库即生效 | 否 |
| 加一项权限 | `core/permissions.py` 的 `_RAW` 加一行 + 需要它的路由改 `ROUTE_RULES` | 否（勾选表、`can()`、Agent 的 `check_permission` 同步） |
| 接入一种 SSO | `providers/auth/` 加一个 `IdP` 子类 + `SCHEMA`，在 `__init__.py` 注册 | 否（表单按 SCHEMA 渲染） |
| 改判权策略 | 只动 `ROUTE_RULES`，中间件与处理器都不必改 | 否 |
| 接入 Web 版 VS Code | 设 `WB_IDE_URL_TEMPLATE` | 否（自动切 iframe） |
| 加一门语言 | `lib/i18n.tsx` 加一个对象 + `LOCALES` 一项 | 否 |
| 加一种资源类型（如 Redis） | `ResourceKind` 加枚举 + Provider 新能力 + 模板声明 | 是（资源表格渲染） |
| 加管理页面 | `app/api/` 新路由 + `frontend/app/` 新页面 | 是 |

**判断标准**：只要改动落在 Provider / Template / Tool 三个维度内，前端与已有服务零改动；
一旦触及"资源类型的种类"或"新的实体"，才需要动前端。

---

## 15. 已知限制与下一步

| 限制 | 现状 | 建议 |
| --- | --- | --- |
| 没有单资源 ACL | 授权只到组织级与项目级，再往下（某个文件、某个环境变量）没有独立主体 | 需要时加资源级 `Binding(resource_type, resource_id)`，复用同一套 `expand()` |
| 没有 SCIM / 离职同步 | IdP 里删人，这里不知道；要等会话过期或人工停用 | 接 SCIM 2.0 的 `/Users` 与 `/Groups`，或周期性拉取 IdP 目录做对账 |
| 分组映射依赖 IdP 发 groups | `role_map` 只在 IdP 真的返回分组声明时生效 | 对不发的 IdP（如部分企微配置）改用部门映射；或在登录后额外调一次目录接口补全 |
| 会话不可跨实例撤销 | 撤销靠删 `Session` 行，多副本共享库才生效 | 已经是库表，多副本天然可用；单机场景无需额外处理 |
| SQL 护栏非解析器 | 首关键字白名单，`WITH ... DELETE` 会被误拒 | 引入 sqlglot 做 AST 级判定；只读连接（`PRAGMA query_only`）双保险 |
| 部署串行化仅进程内 | 多副本部署时 `_RUN_LOCKS` 各管各的 | 活跃部署改为数据库唯一约束（partial unique index） |
| 项目创建非事务 | 已加补偿回滚，但非原子 | Provider 侧提供两阶段（prepare/commit）能力 |
| MCP 鉴权依赖客户端 | 需客户端主动带 header | 网关层做 mTLS 或统一反代鉴权 |
| Agent 写入无影子副本 | 直接落盘，覆盖即覆盖 | 已具备 `git_restore_file` / `git_revert_commit`，可再在 `write_project_file` 前自动 commit 形成每步快照 |
| 预览宿主在控制面进程内 | 重启后端即丢掉预览进程 | 状态接口用端口探活把死行降为 `stopped`；彻底方案是把预览交给独立 supervisor 进程 |
| 编辑器无多文件搜索替换 | 仅有编辑器内的查找交互与 `search_project` 接口 | 在 Code 标签页左侧搜索面板接 `search_project` 结果 |
| 发布不推送远端 | `git tag deploy/v<N>` 只在本地 | 接 `git push --tags` 或改成调用远端 CI |
| 端口分配非原子 | 单进程内顺序分配 | 多副本部署时改用数据库唯一约束 + 重试 |
| 网关是单进程 | 一个 uvicorn，按 Host 路由，路由表 2 秒缓存 | 多副本时每份都读同一个库，路由天然一致；连接池各自独立。要水平扩展就用 nginx / Caddy 前置，`WB_DOMAIN_BASE_URL` 已预留 |
| 网关只做 HTTP/1.1 | uvicorn 单协议，不自带 ACME | 公网场景交给 Caddy / cloudflared 终止 TLS；`WB_GATEWAY_TLS_CERT/KEY` 只接受已有证书 |
| 语言首屏翻转 | 默认 en，hydration 后切到存储的 zh | 改读 cookie 让 SSR 直接出正确语言 |

---

## 16. 目录速查

```
backend/
  app/
    main.py              应用装配（CORS、鉴权中间件、路由、MCP 挂载、种子数据）
    models.py            21 张表
    schemas.py           请求/响应契约
    core/                config(全部 WB_* 配置) / db / security(主体解析+会话)
                         permissions(目录+内置角色+路由表) / passwords(scrypt)
                         context(主体与来源 IP 的 ContextVar) / migrate / errors / ids
    api/                 auth / orgs / projects / workspace / platform / registry / system
    services/            identity(登录·JIT·角色映射) / audit(含拒绝记录)
                         conventions(类型规范) / scaffold / templates
                         gitops(唯一 git 接缝) / deployments / system_tools
                         agent_runtime(local+llm harness) / llm / sql_guard
                         serializers
    providers/           base(SPI+registry) + local/{storage,database,domain,deploy}
                         local/static_host(无依赖也能托管的预览宿主)
                         auth/{base,cas,oauth2(OIDC),wecom,dingtalk}
    mcp/server.py        fastmcp 服务端，与 REST 同端口
    gateway/             内置反向代理（独立进程：python -m app.gateway）
                         routes(Host→上游路由表) / proxy(HTTP+WS 转发) / pages(失败页)
  tools/                 mock_llm(假模型端点) / auth_smoke(41 条判权断言)
                         gateway_smoke(21 条反代断言，含 WS 与流式)
                         access_demo(用公开 API 造一个像样的组织)
frontend/
  app/                   13 个路由（含 /projects/[id] 八标签页、/access、/login）
  components/            Shell(导航布局) / Rail / Topbar / NavLinks / SessionMenu
                         Preferences(语言·主题·导航) / AuthGate(未登录重定向)
                         access/*Panel(成员·角色·SSO·审计·设置)
                         CreateProjectDialog / LogViewer / ProjectCard
                         CodeEditor(textarea+高亮 pre，零依赖) / Icon(全 SVG) / ui
  lib/                   api / session(主体状态机) / access(权限展开与文案)
                         auth(令牌存取) / types / format(状态映射) / i18n(类型化字典)
                         prefs(布局偏好) / nav(单一真相源)
docs/architecture.md     本文档
docs/access.md           RBAC / 组织 / SSO 运维手册
docs/conventions/        三类项目规范 + 约定大于配置的设计说明
scripts/shoot.mjs        端到端走查 + 截图脚本（含 390×844 手机视口）
scripts/explore.mjs      逐页扫描横向溢出与点按目标（--mobile 切手机视口）
data/                    运行时生成（已 gitignore）
```
