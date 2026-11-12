# 反向代理运维手册

内置网关把 `<项目>.<后缀>` 与 `<项目>-dev.<后缀>` 都指向同一个监听，再按 `Host`
转发到项目端口 —— 所以发布出来的地址不带端口。设计取舍见
[architecture.md §13](architecture.md#13-内置反向代理一个监听按-host-分流)，
这里只讲怎么跑、怎么排障。

## 启动

```bash
cd backend
python -m app.gateway            # 默认 0.0.0.0:80
python -m app.gateway --port 8080
python -m app.gateway --tls-cert ./cert.pem --tls-key ./key.pem   # 443 + https 网址
```

启动时打印全部路由：

```
gateway  http://0.0.0.0:80  routes=24  suffix=localtest.me  preview=-dev
  acme-storefront-2-dev.localtest.me  -> http://127.0.0.1:4209  (preview)
  acme-storefront-2.localtest.me      -> http://127.0.0.1:4208  (production)
  status page: http://localhost:80/__gateway/
```

三个进程互不依赖启动顺序：网关随时启停，路由每 2 秒从库里重读一次。

## 权限与端口

| 端口 | 谁 | 是否需要特权 |
| --- | --- | --- |
| 80 / 443 | 网关 | 是（Linux 需 root 或 `CAP_NET_BIND_SERVICE`；Windows 若被 http.sys 占用，`netsh http show urlacl`） |
| 8000 | 控制面（REST + MCP） | 否 |
| 3000 | 前端 | 否 |
| 4200–4399 | 各项目的生产/预览进程 | 否 |

如果只能跑非特权端口，就 `WB_GATEWAY_HTTP_PORT=8080`：此时地址**会带 `:8080`**，
因为给一个 8080 的监听发无端口网址只会得到连不上的链接。这个行为写在
`conventions.origin_for()` 里，是有意为之。

## 公网域名

两条路，选一条：

1. **网关自己暴露**（云主机 + 安全组放行 80/443）：配 `WB_GATEWAY_TLS_CERT/KEY`，
   域名泛解析 `*.<后缀>` 到这台机器的 IP。
2. **前面再套一层**（推荐）：`cloudflared` 隧道或 Caddy 负责 TLS 与公网入口，
   回源到 `127.0.0.1:80`，保留 `Host` 头；此时把 `WB_DOMAIN_BASE_URL` 设成公网基址，
   网址生成会用它，网关仍可继续按 Host 分流内部流量。

```yaml
# cloudflared ingress 示例：通配子域全部回源到网关
ingress:
  - hostname: "*.apps.example.com"
    service: http://127.0.0.1:80
  - service: http_status:404
```

## HTTPS 与证书

两种方式，**通配符优先**：

| 方式 | 需要什么 | 适合 |
| --- | --- | --- |
| 通配符证书 | `*.你的域名` 的 fullchain + 私钥 | 首选。一次覆盖全部项目与预览域名，将来新建的项目也自动包含 |
| http-01 自动签发 | 80 端口对公网可达、`*.<后缀>` 的 A 记录指向本机、一个邮箱 | 通配符覆盖不到的域名（客户自带域名、后缀外的域名） |

两者可以同时开：通配符负责后缀内的一切，自动签发只补它覆盖不到的名字。

### 通配符证书（推荐）

```bash
python -m app.gateway --tls-cert /etc/ssl/apps/fullchain.pem --tls-key /etc/ssl/apps/key.pem
# 或者写进 .env，重启即生效
WB_GATEWAY_TLS_CERT=/etc/ssl/apps/fullchain.pem
WB_GATEWAY_TLS_KEY=/etc/ssl/apps/key.pem
```

也可以把 `<域名>.pem` + `<域名>.key` 直接放进 `WB_GATEWAY_TLS_DIR`（默认
`data/tls/`）——目录里的证书按域名被 SNI 选中，适合"一个域名一张证书"的场景。

证书解析出来的域名、签发者、到期天数会出现在启动横幅、`GET /system/tls` 和设置页：

```
certificate  *.apps.example.com  issuer=Let's Encrypt  71d left  key_ok=True
```

`key_ok=False` 表示 pem 与私钥不是一对，网关不会用它（状态页会写出来）。
这条检查值一次上线：把旧证书配新私钥，握手失败的报错在浏览器里完全没有信息量。

证书热加载，**不需要重启**：文件 mtime 变化后 3 秒内自动生效，换证书只要覆盖文件。

### 自动签发（http-01）

```bash
WB_GATEWAY_ACME_ENABLED=true
WB_GATEWAY_ACME_EMAIL=ops@example.com
# 先对 staging 跑一遍，避免把正式环境的速率限制打掉
WB_GATEWAY_ACME_DIRECTORY=https://acme-staging-v02.api.letsencrypt.org/directory
```

启动后：

```bash
python -m app.gateway acme --status      # 已有哪些证书、几天后到期、队列里有什么
python -m app.gateway acme --all         # 为所有缺失/即将过期的项目域名签发
python -m app.gateway acme --host shop.example.com
```

网关在跑的时候不用手动敲命令：续期循环每 `WB_GATEWAY_ACME_INTERVAL`（默认 300 秒）
检查一次，剩余天数低于 `WB_GATEWAY_ACME_RENEW_DAYS`（默认 30）就重新签；
设置页的「签发」按钮写的是同一条队列（`data/tls/queue/`），由网关执行。

**通配符不能走 http-01**：`*.example.com` 只能用 DNS-01 验证，需要 DNS 服务商的
凭据。本仓库不实现 DNS-01，遇到 `*.` 会直接说明并让你改用通配符证书，而不是
发一个注定失败的订单。

排障要点：

- 80 端口必须能从**公网**访问：`curl -v http://<域名>/.well-known/acme-challenge/test`
  应该看到 404（网关答的 404，不是运营商/防火墙的）。CDN 或 WAF 挡在前面时，
  challenge 可能被拦掉。
- 只签"网关知道"的域名：后缀内的项目域名会自动纳入，自定义域名需要在设置页点
  「签发」——先确认它的 DNS 已经指着这台机器。
- 每张证书约 90 天，续期阈值 30 天；失败不会中断服务，旧的（可能刚过期的）证书
  还挂在 443 上，错误写在队列条目里。
- 两边进程共享 `data/tls`。多机部署时网关只有一台，证书目录需要挂同一个卷。

## 排障

| 现象 | 先看什么 |
| --- | --- |
| 浏览器 `连接被拒绝` | 网关没起，或端口不是 80：`curl -H "Host: <域名>" http://127.0.0.1/__gateway/health` |
| 网关 404「这个域名没有绑定任何项目」 | `Host` 不在路由表里。确认后缀（`<项目>.<后缀>`，预览多一个 `-dev`），或项目已归档 |
| 网关 502「没有在运行」 | 项目端口没有监听。生产：点部署；预览：点启动预览（控制面重启会带走预览进程） |
| 页面 403 `Blocked request`（Vite） | 说明请求没走网关而是直连了端口，或上游 `Host` 校验失败；网关转发时上游看到的是 `localhost:<port>` |
| 表单提交到后端是空 body | 上游是 WSGI 且请求体超过 `WB_GATEWAY_BODY_BUFFER`（默认 1MiB）被流式转发；调大缓冲或让上游支持 chunked |
| SSE / 日志不动 | 检查是否有中间层（公司代理）在缓冲；网关本身是逐块转发的 |
| 改了域名后缀，旧项目还指着老地址 | 重启控制面，`seed._heal_endpoints()` 会按当前形态重算；自定义域名不会被改写 |

诊断接口（需要 `system:read`）：

```bash
GET /api/v1/system/gateway     # 监听状态、全部路由、每条上游是否可达
```

## 已验证行为

`backend/tools/gateway_smoke.py` 真起一个上游（HTTP + WebSocket）和一个 uvicorn
监听，断言 21 项：无端口网址形态、生产/预览分流、上游看到的 `Host`、请求体、
流式响应、`Location` 改写、未知域名 404、停止项目的说明页、WebSocket 双向转发。

```bash
cd backend && ./.venv/Scripts/python.exe tools/gateway_smoke.py
```
