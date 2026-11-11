# 全栈（前后端分离）项目规范 `fullstack`

适用模板：`fullstack-fastapi-nextjs`（FastAPI 后端 + Vite/React 前端）

一句话：**两个目录、两个进程、一个入口域名**。前端是唯一对外的界面，后端只暴露 API 与 MCP，跨域与地址都从环境变量来。

## 一、目录布局

```
<workspace>/
├── backend/
│   ├── app/main.py            # 入口，必须导出 app = FastAPI(...)
│   ├── pyproject.toml         # 依赖（uv 管理），不要 requirements.txt
│   └── ...
├── frontend/
│   ├── package.json           # 必须有 dev / build 脚本
│   ├── index.html             # Vite 入口（Next 项目为 app/page.tsx）
│   ├── src/                   # 前端源码根
│   └── ...
├── .env.example               # 平台注入变量的说明
├── .gitignore
└── README.md
```

体检项：`backend_entry` `backend_deps` `frontend_entry` `frontend_scripts` `health` `env_doc` `readme` `gitignore`

## 二、命令

| 用途 | 命令 | 说明 |
|---|---|---|
| 构建 | `npm --prefix frontend install` | 安装前端依赖；后端依赖由宿主机镜像提供 |
| 调试 | `npm --prefix frontend run dev -- --port {dev_port}` | Vite 热更新，跑在调试端口 |
| 运行 | `npm --prefix frontend run dev` | 生产端口；真正上线时替换为生产服务器命令 |
| 健康检查 | `GET /health` | 后端返回 `{"status": "ok"}` |

`{port}` / `{dev_port}` 由平台在创建时替换成实际端口，写进项目记录的 `dev_command` / `build_command` / `run_command` 字段。

## 三、地址

| 环境 | 域名 | 端口 |
|---|---|---|
| 生产 | `<slug>.localtest.me` | `project.port`（如 4200） |
| 调试 | `<slug>-dev.localtest.me` | `project.dev_port`（如 4204） |

两个端口、两个域名、两条进程链，因此**跑调试永远不会打断生产**。

## 四、环境变量契约

| 变量 | 含义 | 谁写 |
|---|---|---|
| `DATABASE_URL` | 项目数据库 DSN | 平台 |
| `STORAGE_BUCKET` | 对象存储桶名 | 平台 |
| `STORAGE_ENDPOINT` | 桶的本地/远端访问地址 | 平台 |
| `PUBLIC_URL` | 当前环境的公网地址（调试时是预览域名） | 平台，按环境切换 |
| `PORT` | 当前环境应监听的端口 | 平台，按环境切换 |
| `PREVIEW_URL` / `DEV_PORT` | 调试地址与端口 | 平台 |
| `ENVIRONMENT` | `production` \| `preview` | 平台 |

应用**只读不写**这些变量；需要新增变量走项目的「设置 → 环境变量」，会同步写进 `.env` 并在部署时注入。

## 五、契约条款

1. **前后端分目录**：后端只在 `backend/`，前端只在 `frontend/`；共享契约放 `docs/` 或 OpenAPI 产物。
2. **后端必须有 `/health`**：部署闸门与预览探测只认它，必须是 200。
3. **MCP 与 REST 同端口**：`app.mount("/mcp", mcp.http_app(path="/"))`，不要为 MCP 再开端口。
4. **跨域由平台注入**：允许来源从 `CORS_ORIGINS` 读，包含预览域与生产域。
5. **前端不硬编码后端地址**：从 `VITE_API_BASE` / `API_BASE` 读。
6. **不提交依赖与产物**：`.gitignore` 必须包含 `.env` `node_modules/` `dist/` `__pycache__/` `.venv/`。

## 六、调试与发布

```bash
# 调试：起预览环境（<slug>-dev），看日志
POST /api/v1/projects/{id}/preview/start
GET  /api/v1/projects/{id}/preview

# 发布：提交 + 打 deploy/v<N> 标签 + 部署该 commit
POST /api/v1/projects/{id}/publish   {"message": "feat: ..."}
```

预览默认托管策略：能起 dev server 就起；依赖没装时回退到静态预览宿主，并渲染一页说明（缺什么、跑什么命令），而不是给你一个死端口。

## 七、常见违规

| 症状 | 违反的条款 | 体检结果 |
|---|---|---|
| 前端代码里写死 `http://...:4200` | 5 | 无对应检查，但会随预览域名失效 |
| 后端没有 `/health` | 2 | `health` = warn |
| `.env` 被提交 | 6 | `gitignore` = warn |
| 把 MCP 挂在 8001 端口 | 3 | 无对应检查，跨域与网关会多一层 |
| 前端构建产物提交进仓库 | 6 | `ignore` = warn |

## 八、覆盖示例

Next.js 前端 + `/api/ping` 健康检查：

```yaml
dev_command: npm --prefix frontend run dev -- --port 3000
build_command: npm --prefix frontend install && npm --prefix frontend run build
health_path: /api/ping
```
