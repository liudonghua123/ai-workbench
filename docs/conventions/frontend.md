# 纯前端（静态站点）项目规范 `frontend`

适用模板：`frontend-vite-react`

一句话：**没有服务端进程**。构建产物就是部署物，直接托管；需要密钥的调用一律走后端或 MCP。

## 一、目录布局

```
<workspace>/
├── package.json        # 必须有 build 脚本
├── index.html          # Vite 入口（Next 项目为 app/page.tsx）
├── src/
│   ├── main.tsx        # 挂载点
│   ├── App.tsx         # 根组件
│   └── styles.css
├── .gitignore          # 必须含 .env、node_modules/、dist/
└── README.md
```

体检项：`entry` `scripts` `source` `secret_hygiene` `ignore` `readme` `gitignore`

## 二、命令

| 用途 | 命令 | 说明 |
|---|---|---|
| 构建 | `npm install` | 装依赖；`build` 脚本负责产出 `dist/` |
| 调试 | `npm run dev -- --port {dev_port}` | Vite 热更新 |
| 运行 | `npm run dev` | 生产端口；静态托管时直接伺服 `dist/` |
| 健康检查 | `/` | 首页可访问即视为健康 |

## 三、地址

| 环境 | 域名 | 端口 |
|---|---|---|
| 生产 | `<slug>.localtest.me` | `project.port` |
| 调试 | `<slug>-dev.localtest.me` | `project.dev_port` |

预览宿主按以下顺序选择内容：`dist/` → `build/` → `out/` → 可直出的 `index.html` → 说明页。最后一种情况会明确告诉你"源码需要构建、依赖未安装"，并给出应当执行的命令。

## 四、环境变量契约

| 变量 | 含义 | 注意 |
|---|---|---|
| `STORAGE_BUCKET` / `STORAGE_ENDPOINT` | 静态资源桶 | 前端可用，属于公开信息 |
| `PUBLIC_URL` / `PORT` | 当前环境地址与端口 | 构建期注入 |
| `PREVIEW_URL` / `DEV_PORT` | 调试地址与端口 | 用于分享链接 |
| `API_BASE` / `VITE_API_BASE` | 后端地址 | **必须从变量读，不能硬编码** |

`DATABASE_URL` 也会被注入（平台统一行为），但纯前端项目**不应读取它**——静态产物里的任何变量都是公开的。

## 五、契约条款

1. **产物即部署物**：构建输出必须落在 `dist/`（或 `out/`、`build/`），托管层直接伺服该目录。
2. **前端不含密钥**：写进源码或 `.env` 的 key 会随产物公开；需要保密就放到后端或 MCP 工具里。
3. **后端地址可配置**：从 `API_BASE` / `VITE_API_BASE` 读，禁止写死域名与端口。
4. **`.env` 不进版本库**：`.gitignore` 必须包含 `.env`。
5. **`dist/` 不进版本库**：产物由构建产生，不提交。
6. **单页路由要有 fallback**：客户端路由（history 模式）需要服务端把未知路径回落到 `index.html`。

## 六、调试与发布

```bash
POST /api/v1/projects/{id}/preview/start   # 起预览（静态宿主 / dev server）
POST /api/v1/projects/{id}/publish         # 提交 + tag deploy/v<N> + 部署
```

预览的 `mode` 会写在部署记录的 `image` 字段里：`static:dist`、`static:workspace`、`static:explainer`、`process` 之一，界面直接展示，不猜。

## 七、常见违规

| 症状 | 违反的条款 | 体检结果 |
|---|---|---|
| 把 `OPENAI_API_KEY` 放进 `.env` 并被打包 | 2 | 无对应检查，但属于安全事故 |
| 没有 `build` 脚本 | 1 | `scripts` = fail |
| 继续用 CRA 的 `build/` 却没写进托管配置 | 1 | 托管层会自动识别 `build/` |
| `.env` 被提交 | 4 | `secret_hygiene` = fail |
| 提交 `dist/` | 5 | `ignore` = warn |

## 八、覆盖示例

部署到子路径，健康检查改为 `/app/`：

```yaml
health_path: /app/
build_command: npm install && npm run build -- --base=/app/
```
