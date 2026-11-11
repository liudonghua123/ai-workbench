# AI Workbench

**A visual control plane for applications that AI agents can build, modify and deploy on their own.**

[中文](README.zh-CN.md) · [Architecture](docs/architecture.md) · MIT

![Workbench dashboard](docs/screenshots/01-dashboard.png)

---

## Why this exists

Most "AI builds your app" demos stop at code generation. The hard part starts
after: the app needs a bucket, a database, a domain, environment variables, a
deploy target — and the agent needs a *stable programmatic surface* to touch all
of that later without a human clicking around.

AI Workbench is that surface. You create a project visually, the platform
conventionally provisions everything it needs, and the exact same operations are
exposed over MCP so an agent can keep working on it afterwards. **There is no
"UI capability" that an agent can't reach.**

---

## What you get

| | |
| --- | --- |
| **Visual project creation** | Pick a template, get storage + database + `<slug>.<suffix>` domain provisioned automatically |
| **Four templates, four shapes** | Fullstack (FastAPI + Next.js), pure frontend (Vite + React), pure backend (FastAPI + MCP), agent service (FastAPI + MCP + harness) |
| **Online editing** | Built-in file editor out of the box; point `WB_IDE_URL_TEMPLATE` at code-server / openvscode-server to embed real VS Code in an iframe |
| **One-click deploy** | Versioned deployments with streaming build logs and health checks |
| **Live resources** | SQL console against the project's own database, object browser for its bucket |
| **MCP / Skills / Agents** | All three are first-class, visually managed entities stored as data |
| **Conventions over configuration** | Workspace path, bucket name, database name, primary domain and port all derive from the slug |
| **Swappable cloud** | Storage / database / DNS / deploy sit behind one Provider SPI — swapping a cloud is one env var |

---

## Quick start

Requirements: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), Node.js ≥ 20.

```bash
git clone <your-repo> ai-workbench && cd ai-workbench

# 1 — backend (REST + MCP on :8000)
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000

# 2 — frontend (:3000), in another terminal
cd frontend
npm install
npm run dev
```

Open <http://localhost:3000>. Two demo projects are seeded on first boot
(`WB_BOOTSTRAP_DEMO_DATA=true`).

No cloud credentials are needed. `localtest.me` resolves `*.localtest.me` to
`127.0.0.1`, so `customer-success.localtest.me` works in your browser with zero
DNS setup.

**Generated projects land in `data/projects/<slug>/`.** Delete `data/` to reset
the whole platform.

---

## Walkthrough

### Create a project

Template → name → optional subdomain prefix. The checkbox at the bottom triggers
the first deployment immediately.

![Create dialog](docs/screenshots/03-create.png)

### A project has five tabs

| Tab | What it does |
| --- | --- |
| 概览 / Overview | Runtime, provisioned cloud resources, domain bindings, env vars, recent deploys |
| 代码 / IDE | File tree + editor, or an embedded VS Code if `WB_IDE_URL_TEMPLATE` is set |
| 部署 / Deploy | Deployment history, live build log, stop / redeploy |
| 数据 / Data | SQL console against the project database, object storage browser |
| 设置 / Settings | Env vars, custom domains, destructive delete |

![Project overview](docs/screenshots/04-project-overview.png)

### Editing code

Without an external IDE the built-in editor reads and writes the workspace
directly — the same `write_project_file` call an agent would make.

![IDE tab](docs/screenshots/05-project-ide.png)

### Deploying

Deployments are async: the request returns immediately, the log streams as the
build progresses, and a health check decides `running` vs `failed`.

![Deploy tab](docs/screenshots/06-project-deploy.png)

![Data tab](docs/screenshots/07-project-data.png)

### Managing the capability layer

MCP servers, Skills and Agents are stored as rows, tied together by id — so an
agent's capabilities can be recomposed from the UI at any time without touching
code.

![MCP management](docs/screenshots/10-mcp.png)

![Agents](docs/screenshots/09-agents.png)

Every mutation lands in a global event stream, which is also what the dashboard
counter is built from.

![Events](docs/screenshots/12-events.png)

---

## The MCP surface

The workbench mounts its own MCP server **on the same port** as the REST API
(`/mcp`, streamable HTTP). Tools are the same Python callables the REST layer
uses — no parallel implementation to drift.

```json
{ "mcpServers": { "workbench": { "url": "http://127.0.0.1:8000/mcp" } } }
```

| Tool | Purpose |
| --- | --- |
| `list_templates` | Available templates and the resources each one provisions |
| `create_project` | Scaffold + provision storage, database, domain |
| `list_projects` / `get_project` | Inspect state, resources, domains, latest deployment |
| `list_project_files` / `read_project_file` / `write_project_file` | Read and modify application code |
| `set_env_var` | Change environment, injected on next deploy |
| `deploy_project` / `deployment_status` | Trigger a build, then poll it |
| `list_agents` / `run_workbench_agent` | Delegate a high-level goal to a configured agent |
| `workbench_capabilities` | Object model + conventions, so an external agent can plan safely |

Typical agent loop:

```
list_templates → create_project → write_project_file → deploy_project → deployment_status
```

REST equivalent: 45 endpoints under `/api/v1`, browsable at
<http://localhost:8000/docs>.

---

## Conventions

Everything derives from the project slug — the "configuration" is just the name
you gave it.

| Thing | Rule |
| --- | --- |
| Workspace | `data/projects/<slug>` |
| Bucket | `<slug>-assets` |
| Database | `<slug>` |
| Primary domain | `<slug>.<WB_DOMAIN_SUFFIX>` |
| Local port | first free in `4200–4399` |
| Run command | from the template, overridable per project |

---

## Making it yours

| Goal | Where | Frontend changes |
| --- | --- | --- |
| Different cloud (COS / S3 / Postgres / DNSPod / Docker) | Add a provider module, set `WB_*_PROVIDER` | none |
| New template | One dataclass in `backend/app/services/templates.py` | none |
| New agent capability | One function + entry in `TOOLS` | none |
| Real LLM instead of the rule-based planner | Replace `_plan()` in `agent_runtime.py` | none |
| Embed real VS Code | Set `WB_IDE_URL_TEMPLATE` | none |
| New resource kind (e.g. Redis) | New provider capability + template declaration | yes |

The rule of thumb: anything inside the **Provider / Template / Tool** triangle
needs no frontend work. Only adding a *new kind of entity* does.

Providers are registered by decorator and resolved by name at runtime:

```python
@register_provider("storage", "s3")
class S3Storage(StorageProvider):
    def create_bucket(self, bucket, *, project_id): ...
```

`GET /api/v1/system/providers` lists whatever is registered.

---

## Configuration

Copy `.env.example` to `.env`. Every key is optional and prefixed `WB_`.

```bash
WB_DOMAIN_SUFFIX=localtest.me    # *.localtest.me -> 127.0.0.1
WB_DEPLOY_MODE=simulate          # simulate | process
WB_STORAGE_PROVIDER=local        # swap for s3 / cos / oss ...
WB_IDE_URL_TEMPLATE=             # code-server entry, {path} {project} placeholders
WB_DATA_DIR=./data               # projects, buckets, databases, dns
```

`WB_DEPLOY_MODE=process` actually spawns the project's run command on its
allocated port; `simulate` (default) produces a realistic build log without
starting anything.

---

## Project layout

```
backend/
  app/
    api/           projects · platform · registry · system
    services/      scaffold · templates · deployments · system_tools · agent_runtime
    providers/     base (SPI + registry) + local/{storage,database,domain,deploy}
    mcp/server.py  fastmcp server, mounted on the same port
    models.py      9 tables
frontend/
  app/             10 routes, five tabs under /projects/[id]
  components/      Rail · Topbar · CreateProjectDialog · LogViewer · Icon (all SVG)
  lib/             api · types · format
docs/architecture.md   design rationale, data model, extension points
scripts/shoot.mjs      smoke walk-through that produces the screenshots above
```

---

## Known limitations

Single-user, no auth · SQL console has no sandbox · the agent planner is
rule-based, not LLM-driven · frontend copy is Chinese-only (no i18n yet) ·
concurrent deploys on one project aren't locked · project creation isn't
atomic across providers. See
[§11 of the architecture doc](docs/architecture.md#11-已知限制与下一步) for the
recommended fix for each.

---

## License

MIT — see [LICENSE](LICENSE).
