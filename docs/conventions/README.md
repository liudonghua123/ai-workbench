# 项目规范：约定大于配置

AI Workbench 里**没有项目配置文件**。创建一个应用只需要一个名字和一套模板：工作区路径、端口、域名、对象存储桶、数据库名、环境变量、dev/build/run 命令全部由项目标识（slug）和项目类型推导出来。

这套做法的价值不在"少写一个 yaml"，而在于**可预测性**：人、CI、Agent 三方在动手之前就能知道文件在哪、服务在哪个地址、该跑哪条命令。规范写得越死，Agent 越不需要猜。

> 规范不是文档，是可执行的代码。
> `backend/app/services/conventions.py` 里的 `TypeSpec` 同时驱动：模板渲染、规范体检、预览命令、Agent 系统提示词。本文档与它一一对应，体检项的名字就是下面表格里的 key。

---

## 一、三层结构

| 层 | 定义位置 | 作用范围 | 能否修改 |
|---|---|---|---|
| **平台约定** | `conventions.py` 的 `_LAYOUT_DOC` | 所有项目 | 否 |
| **类型约定** | `FULLSTACK` / `FRONTEND` / `BACKEND` | 同类型的所有项目 | 否（改代码即改规范） |
| **项目覆盖** | 项目根目录的 `workbench.yaml` | 单个项目 | 是，唯一的配置文件 |

优先级：`workbench.yaml` > 项目记录里的字段 > 类型默认值。三者都没有时，取约定里写死的字符串。

平台约定（对三类项目都成立）：

| key | 约定 |
|---|---|
| `workspace` | 工作区固定为 `data/projects/<slug>` |
| `bucket` | 对象存储桶 `<slug>-assets` |
| `database` | 数据库名 = slug 里的 `-` 换成 `_` |
| `domain` | 生产 `<slug>.<WB_DOMAIN_SUFFIX>` |
| `preview` | 调试 `<slug><WB_PREVIEW_SUFFIX>.<WB_DOMAIN_SUFFIX>`，默认后缀 `-dev` |
| `ports` | 生产和调试各占一个端口，均从 `WB_PORT_RANGE_*` 里分配，互不复用 |
| `env` | 平台注入，应用只读：`DATABASE_URL` `STORAGE_BUCKET` `STORAGE_ENDPOINT` `PUBLIC_URL` `PORT` `PREVIEW_URL` `DEV_PORT` `ENVIRONMENT` |
| `git` | 项目从第 0 分钟就是 git 仓库，首次提交 `chore: initial import` |
| `publish` | 发布 = `commit` + `tag deploy/v<N>` + 部署该 commit |

## 二、三类项目对照

| | `fullstack` | `frontend` | `backend` |
|---|---|---|---|
| 入口 | `backend/app/main.py` + `frontend/package.json` | `index.html`（或 `app/page.tsx`） | `app/main.py` |
| 进程数 | 2（前端 + 后端） | 1（dev server / 静态宿主） | 1 |
| 云资源 | 对象存储 + 数据库 | 仅对象存储 | 对象存储 + 数据库 |
| 构建 | `npm --prefix frontend install` | `npm install` | 无 |
| 调试命令 | `npm --prefix frontend run dev -- --port {port}` | `npm run dev -- --port {port}` | `python -m uvicorn app.main:app --host 0.0.0.0 --port {port} --reload` |
| 健康路径 | `/health`（后端） | `/` | `/health` |
| 预览域名 | `<slug>-dev.<suffix>` | `<slug>-dev.<suffix>` | `<slug>-dev.<suffix>` |
| 典型场景 | 管理台、带接口的产品 | 官网、活动页、看板 | API、MCP 服务、Agent 服务 |

`agent` 类型是 `backend` 的特化（多一个 `app/agent.py` 作为 harness 接入点），体检与端口规则完全一致，因此不再单独成篇。

详细条款：

- [全栈（前后端分离）](fullstack.md)
- [纯前端（静态站点）](frontend.md)
- [纯后端（API 服务）](backend.md)

## 三、规范怎么被执行

约定如果只写在文档里，两天后就会失效。这里它出现在五个地方：

1. **创建时**（`scaffold.create_project`）
   按类型写文件、分配两个端口、绑定两个域名、写 `.env`、`git init` 并提交。
2. **体检接口**（`GET /api/v1/projects/{id}/conventions`）
   返回解析后的 spec + `pass` / `warn` / `fail` 清单。`fail` 说明项目已经不符合自己的类型（例如入口文件被删）。
3. **界面**（项目页「规范」标签）
   同一份体检结果直接渲染，不做二次加工。
4. **Agent**（`check_project_conventions` 工具）
   系统提示词要求 Agent 动手前先读体检结果，避免它按记忆里的路径乱猜。
5. **发布闸门**
   `publish_project` 只接受 git commit 作为部署输入，因此"代码没提交就发布"这种漂移不可能发生。

## 四、覆盖：唯一的配置文件

`workbench.yaml` 放在项目根目录，**所有字段可选**，只有确实需要偏离约定时才出现：

```yaml
# 只有这个项目需要：dev server 走 3000 而不是约定端口
dev_command: npm run dev -- --port 3000
# 前端用 Next 而不是 Vite，健康检查在 /api/ping
health_path: /api/ping
# 这个项目的预览域名用 -preview 而不是 -dev
preview_suffix: -preview
```

可覆盖字段：`type` `dev_command` `build_command` `run_command` `health_path` `preview_suffix`。

体检页会明确显示「没有 workbench.yaml —— 所有值都来自约定」或「workbench.yaml 覆盖了 N 项」，让偏离本身也是可见的。

## 五、新增一种项目类型

1. 在 `conventions.py` 定义 `TypeSpec`（布局、命令、健康路径、资源、契约条款）。
2. 在 `_CHECKERS` 里加一个体检函数，返回 `Check` 列表。
3. 在 `templates.py` 里加模板，`type` 指向新类型。
4. 在 `docs/conventions/` 下补一篇文档，章节与本文其他类型一致。
5. 用 `GET /projects/{id}/conventions` 自查一遍新模板是否 `conformant`。

不需要改数据库、不需要改 API 路由、不需要改前端——体检结果的渲染是数据驱动的。
