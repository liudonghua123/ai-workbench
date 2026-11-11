# 纯后端（API 服务）项目规范 `backend`

适用模板：`backend-fastapi`、`agent-fastapi`（`agent` 是本类型的特化）

一句话：**一个进程，两种协议**。同一个端口既提供 REST，也在 `/mcp` 挂载 streamable HTTP MCP；没有界面。

## 一、目录布局

```
<workspace>/
├── app/
│   ├── main.py         # 入口，必须导出 app = FastAPI(...)
│   └── agent.py        # 仅 agent 类型：harness 接入点
├── pyproject.toml      # 依赖（uv 管理）
├── Dockerfile          # 可选，容器化部署用
├── .env.example
├── .gitignore
└── README.md
```

体检项：`entry` `deps` `health` `mcp` `docker` `readme` `gitignore`

## 二、命令

| 用途 | 命令 | 说明 |
|---|---|---|
| 调试 | `python -m uvicorn app.main:app --host 0.0.0.0 --port {port} --reload` | 改动即重启 |
| 运行 | `python -m uvicorn app.main:app --host 0.0.0.0 --port {port}` | 生产端口 |
| 构建 | 无 | Python 依赖由宿主机镜像提供；容器化时在镜像内安装 |
| 健康检查 | `GET /health` | 必须返回 200 |

## 三、地址

| 环境 | 域名 | 端口 |
|---|---|---|
| 生产 | `<slug>.localtest.me` | `project.port` |
| 调试 | `<slug>-dev.localtest.me` | `project.dev_port` |
| MCP | `<当前环境地址>/mcp` | 与 REST 同端口 |

## 四、环境变量契约

| 变量 | 含义 |
|---|---|
| `DATABASE_URL` | 项目数据库 DSN |
| `STORAGE_BUCKET` / `STORAGE_ENDPOINT` | 对象存储桶与访问地址 |
| `PUBLIC_URL` | 当前环境公网地址（调试时为预览地址） |
| `PORT` | 当前环境监听端口 |
| `PREVIEW_URL` / `DEV_PORT` | 调试地址与端口 |
| `ENVIRONMENT` | `production` \| `preview` |

## 五、契约条款

1. **必须有 `/health`**：部署闸门与预览探测只认它；返回非 200 视为启动失败。
2. **MCP 与 REST 同端口**：

   ```python
   from fastmcp import FastMCP
   mcp = FastMCP("<slug>")
   app.mount("/mcp", mcp.http_app(path="/"))
   ```

   一个进程、一个端口、两种协议；MCP 客户端连 `<PUBLIC_URL>/mcp`。
3. **保留 `/docs`**：OpenAPI 是调用方与 Agent 的契约来源，不要关闭。
4. **工具面单一实现**：同一份函数同时被 REST、MCP、Agent 调用，不要在 MCP 里另写一套逻辑。
5. **只用平台注入的变量**：不自行读取 `.env` 之外的位置，不硬编码域名与端口。
6. **依赖用 uv 管理**：`pyproject.toml` + `uv.lock`，不提交 `requirements.txt`。

## 六、Agent 服务（`agent` 类型）的额外约定

- 规划循环放在 `app/agent.py` 的 `run()` 里，harness 可替换（agentscope / 自研 ReAct）。
- 工具通过 MCP 暴露，harness 只负责决策，不直接碰云资源。
- `MODEL` 等配置从环境变量读，便于同一镜像切换模型。

## 七、调试与发布

```bash
POST /api/v1/projects/{id}/preview/start   # 起预览（uvicorn --reload）
GET  /api/v1/projects/{id}/preview         # 状态 + 日志尾部
POST /api/v1/projects/{id}/publish         # commit + tag deploy/v<N> + 部署
```

后端项目的预览需要虚拟环境（`.venv`）。没有依赖时静态宿主会渲染说明页——不要误以为是服务起不来。

## 八、常见违规

| 症状 | 违反的条款 | 体检结果 |
|---|---|---|
| `main.py` 在根目录且没有 `app/` 包 | 1（布局） | `entry` 会回退识别根目录 `main.py`，但仍建议迁到 `app/` |
| 没有 `/health` | 1 | `health` = fail |
| 把 MCP 挂到 `/sse` 且单独开端口 | 2 | `mcp` = warn |
| 关闭 `/docs` | 3 | 无对应检查，但会削弱可观测性 |
| MCP 工具里复制了一份业务逻辑 | 4 | 无对应检查，属于维护性问题 |

## 九、覆盖示例

把健康检查换成 `/healthz`，并让调试不开 reload（排查启动竞态时有用）：

```yaml
health_path: /healthz
dev_command: python -m uvicorn app.main:app --host 0.0.0.0 --port 4211
```
