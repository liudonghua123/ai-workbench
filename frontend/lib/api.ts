import type {
  Agent,
  AgentRun,
  Deployment,
  DomainBinding,
  EnvVar,
  EventItem,
  McpServer,
  Overview,
  Project,
  Settings,
  Skill,
  Template,
  TreeNode,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000/api/v1";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(message: string, status: number, code = "error") {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const err = body?.error;
    throw new ApiError(
      err?.message ?? `请求失败 (${res.status})`,
      res.status,
      err?.code ?? "error",
    );
  }
  return body as T;
}

const post = <T>(path: string, payload?: unknown) =>
  request<T>(path, { method: "POST", body: payload ? JSON.stringify(payload) : "{}" });
const put = <T>(path: string, payload: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(payload) });
const patch = <T>(path: string, payload: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(payload) });
const del = (path: string) => request<void>(path, { method: "DELETE" });

export const api = {
  overview: () => request<Overview>("/system/overview"),
  settings: () => request<Settings>("/system/settings"),
  providers: () => request<Record<string, unknown>>("/system/providers"),
  events: (limit = 60) => request<EventItem[]>(`/system/events?limit=${limit}`),

  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (payload: {
    name: string;
    template_key: string;
    description?: string;
    slug?: string;
    variables?: Record<string, string>;
    deploy?: boolean;
  }) => post<Project>("/projects", payload),
  deleteProject: (id: string) => del(`/projects/${id}?purge_files=true`),
  archiveProject: (id: string) => post<Project>(`/projects/${id}/archive`),

  tree: (id: string) => request<{ root: string; nodes: TreeNode[] }>(`/projects/${id}/tree`),
  readFile: (id: string, path: string) =>
    request<{ path: string; content: string }>(
      `/projects/${id}/file?path=${encodeURIComponent(path)}`,
    ),
  writeFile: (id: string, path: string, content: string) =>
    put<{ path: string; size: number }>(`/projects/${id}/file`, { path, content }),

  env: (id: string) => request<EnvVar[]>(`/projects/${id}/env`),
  upsertEnv: (id: string, key: string, value: string, isSecret = false) =>
    put<EnvVar>(`/projects/${id}/env`, { key, value, is_secret: isSecret }),

  resources: (id: string) =>
    request<{ id: string; kind: string; name: string; endpoint: string; config: Record<string, unknown> }[]>(
      `/projects/${id}/resources`,
    ),
  dbQuery: (id: string, sql: string) =>
    post<{ rows: Record<string, unknown>[]; row_count: number; database: string }>(
      `/projects/${id}/database/query`,
      { sql },
    ),
  objects: (id: string) =>
    request<{ bucket: string; objects: { key: string; size: number }[] }>(
      `/projects/${id}/storage/objects`,
    ),

  deploy: (id: string, environment = "production") =>
    post<Deployment>(`/projects/${id}/deployments`, { environment }),
  deployments: (id: string) => request<Deployment[]>(`/projects/${id}/deployments`),
  deployment: (deploymentId: string) => request<Deployment>(`/deployments/${deploymentId}`),
  stopDeployment: (deploymentId: string) =>
    post<Deployment>(`/deployments/${deploymentId}/stop`),

  domains: (id: string) => request<DomainBinding[]>(`/projects/${id}/domains`),
  addDomain: (id: string, subdomain: string, suffix = "") =>
    post<DomainBinding>(`/projects/${id}/domains`, { subdomain, suffix }),
  verifyDomain: (id: string, domainId: string) =>
    post<DomainBinding>(`/projects/${id}/domains/${domainId}/verify`),

  ide: (id: string) =>
    request<{ vscode_url: string; available: boolean; repo_path: string; note: string }>(
      `/projects/${id}/ide`,
    ),

  templates: () => request<Template[]>("/templates"),
  reloadTemplates: () => post<{ count: number }>("/templates/reload"),

  mcpServers: () => request<McpServer[]>("/mcp-servers"),
  probeMcp: (id: string) => post<McpServer>(`/mcp-servers/${id}/probe`),
  createMcp: (payload: Partial<McpServer>) => post<McpServer>("/mcp-servers", payload),
  deleteMcp: (id: string) => del(`/mcp-servers/${id}`),

  skills: () => request<Skill[]>("/skills"),
  toggleSkill: (id: string) => post<Skill>(`/skills/${id}/toggle`),
  scanSkills: () => post<{ scanned: number; root: string }>("/skills/scan"),
  createSkill: (payload: Partial<Skill>) => post<Skill>("/skills", payload),

  agents: () => request<Agent[]>("/agents"),
  createAgent: (payload: Partial<Agent>) => post<Agent>("/agents", payload),
  updateAgent: (id: string, payload: Partial<Agent>) => patch<Agent>(`/agents/${id}`, payload),
  deleteAgent: (id: string) => del(`/agents/${id}`),
  runAgent: (key: string, prompt: string, projectId?: string) =>
    post<AgentRun>(`/agents/${key}/run`, { prompt, project_id: projectId ?? null }),
  agentRuns: () => request<AgentRun[]>("/agent-runs"),
};
