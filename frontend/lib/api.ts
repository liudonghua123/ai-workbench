import type {
  Agent,
  AgentRun,
  AuditRow,
  ConventionReport,
  Deployment,
  DomainBinding,
  EnvVar,
  EventItem,
  GatewayStatus,
  GitCommit,
  GitDiff,
  GitStatus,
  HarnessReport,
  IdentityProvider,
  Invitation,
  LlmStatus,
  LlmTest,
  LinkedIdentity,
  McpServer,
  Me,
  OrgMember,
  Organization,
  OrgRole,
  Overview,
  PermissionCatalog,
  PreviewState,
  ProbeResult,
  Project,
  ProviderDirectory,
  PublishResult,
  RoleBinding,
  SessionMe,
  Settings,
  Skill,
  SqlMode,
  SsoSchema,
  Template,
  TlsQueueItem,
  TlsStatus,
  TreeNode,
} from "./types";
import { getToken, notifyUnauthorized, tokenQuery } from "./auth";

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
  const token = getToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const err = body?.error;
    // one prompt for the whole app, not one broken page per request
    if (res.status === 401) notifyUnauthorized();
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
  gateway: () => request<GatewayStatus>("/system/gateway"),
  tls: () => request<TlsStatus>("/system/tls"),
  issueCert: (hosts?: string[]) =>
    post<{ queued: TlsQueueItem[]; errors: { host: string; message: string }[] }>(
      "/system/tls/issue",
      hosts ? { hosts } : {},
    ),
  cancelCert: (host: string) => del(`/system/tls/issue/${encodeURIComponent(host)}`),
  providers: () => request<Record<string, unknown>>("/system/providers"),
  events: (limit = 60) => request<EventItem[]>(`/system/events?limit=${limit}`),
  llm: () => request<LlmStatus>("/system/llm"),
  testLlm: () => post<LlmTest>("/system/llm/test"),
  harnesses: () => request<HarnessReport>("/system/harnesses"),
  me: () => request<Me>("/system/me"),
  sqlMode: () => request<SqlMode>("/system/sql-mode"),

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
  deleteFile: (id: string, path: string) =>
    del(`/projects/${id}/file?path=${encodeURIComponent(path)}`),

  // --- conventions ---------------------------------------------------------
  conventions: (id: string) => request<ConventionReport>(`/projects/${id}/conventions`),

  // --- preview / debug -----------------------------------------------------
  preview: (id: string) => request<PreviewState>(`/projects/${id}/preview`),
  startPreview: (id: string) => post<Deployment>(`/projects/${id}/preview/start`),
  stopPreview: (id: string) => post<Deployment>(`/projects/${id}/preview/stop`),
  runProject: (id: string) => post<Deployment>(`/projects/${id}/run`),
  publish: (id: string, message = "") =>
    post<PublishResult>(`/projects/${id}/publish`, { message }),

  // --- version control -----------------------------------------------------
  gitStatus: (id: string) => request<GitStatus>(`/projects/${id}/git`),
  gitLog: (id: string, limit = 30) =>
    request<{ commits: GitCommit[]; tags: string[] }>(
      `/projects/${id}/git/log?limit=${limit}`,
    ),
  gitDiff: (id: string, path = "", ref = "") =>
    request<GitDiff>(
      `/projects/${id}/git/diff?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`,
    ),
  gitShow: (id: string, sha: string) =>
    request<GitDiff>(`/projects/${id}/git/show?sha=${encodeURIComponent(sha)}`),
  gitCommit: (id: string, message: string, paths: string[] = []) =>
    post<{ committed: boolean; sha?: string; short?: string; reason?: string }>(
      `/projects/${id}/git/commit`,
      { message, paths },
    ),
  gitRestore: (id: string, path: string, ref = "HEAD") =>
    post<{ path: string; restored: boolean }>(`/projects/${id}/git/restore`, { path, ref }),
  gitRevert: (id: string, sha: string) =>
    post<{ reverted: string; committed: boolean; short?: string }>(`/projects/${id}/git/revert`, {
      ref: sha,
    }),

  env: (id: string) => request<EnvVar[]>(`/projects/${id}/env`),
  upsertEnv: (id: string, key: string, value: string, isSecret = false) =>
    put<EnvVar>(`/projects/${id}/env`, { key, value, is_secret: isSecret }),

  resources: (id: string) =>
    request<{ id: string; kind: string; name: string; endpoint: string; config: Record<string, unknown> }[]>(
      `/projects/${id}/resources`,
    ),
  dbQuery: (id: string, sql: string) =>
    post<{
      rows: Record<string, unknown>[];
      row_count: number;
      database: string;
      statement?: string;
      mode?: string;
    }>(`/projects/${id}/database/query`, { sql }),
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
  deploymentStreamUrl: (deploymentId: string) =>
    tokenQuery(`${API_BASE}/deployments/${deploymentId}/stream`),

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

  // --- authentication ------------------------------------------------------
  loginProviders: () => request<ProviderDirectory>("/auth/providers"),
  startSso: (key: string, returnTo = "/") =>
    request<{ authorize_url: string; state: string; redirect_uri: string }>(
      `/auth/${key}/start?return_to=${encodeURIComponent(returnTo)}`,
    ),
  localLogin: (email: string, password: string) =>
    post<{ token: string; user: OrgMember; org: Organization; expires_in: number }>(
      "/auth/local/login",
      { email, password },
    ),
  signup: (payload: { email: string; password: string; name?: string; org_name?: string }) =>
    post<{ token: string; user: OrgMember; org: Organization }>("/auth/signup", payload),
  sessionMe: () => request<SessionMe>("/auth/me"),
  logout: () => post<{ ok: boolean }>("/auth/logout"),
  switchOrg: (orgId: string) =>
    post<{ ok: boolean; org_id: string; slug: string; role_key: string }>("/auth/switch-org", {
      org_id: orgId,
    }),
  revokeSession: (id: string) => del(`/auth/sessions/${id}`),
  changePassword: (currentPassword: string, newPassword: string) =>
    post<{ ok: boolean; note: string }>("/auth/password", {
      current_password: currentPassword,
      new_password: newPassword,
    }),

  // --- organizations & access control --------------------------------------
  orgs: () => request<Organization[]>("/orgs"),
  org: (id: string) => request<Organization>(`/orgs/${id}`),
  createOrg: (payload: { name: string; slug?: string; description?: string; plan?: string }) =>
    post<Organization>("/orgs", payload),
  updateOrg: (id: string, payload: Partial<Organization>) => patch<Organization>(`/orgs/${id}`, payload),
  removeOrg: (id: string, purge = false) => del(`/orgs/${id}?purge_projects=${purge}`),

  members: (orgId: string) => request<OrgMember[]>(`/orgs/${orgId}/members`),
  addMember: (orgId: string, payload: { email?: string; user_id?: string; role_key: string; title?: string }) =>
    post<OrgMember & { invited?: boolean; accept_token?: string; invitation?: Invitation }>(
      `/orgs/${orgId}/members`,
      payload,
    ),
  updateMember: (orgId: string, memberId: string, payload: { role_key?: string; status?: string; title?: string }) =>
    patch<OrgMember>(`/orgs/${orgId}/members/${memberId}`, payload),
  removeMember: (orgId: string, memberId: string) => del(`/orgs/${orgId}/members/${memberId}`),
  resetMemberPassword: (orgId: string, memberId: string) =>
    post<{ ok: boolean; temporary_password: string }>(
      `/orgs/${orgId}/members/${memberId}/reset-password`,
    ),

  invitations: (orgId: string) => request<Invitation[]>(`/orgs/${orgId}/invitations`),
  invite: (orgId: string, payload: { email: string; role_key: string; ttl_days?: number }) =>
    post<Invitation>(`/orgs/${orgId}/invitations`, payload),
  revokeInvitation: (orgId: string, id: string) => del(`/orgs/${orgId}/invitations/${id}`),
  acceptInvitation: (token: string) =>
    post<{ ok: boolean; org: Organization | null; role_key: string }>("/invitations/accept", { token }),

  roles: (orgId: string) => request<OrgRole[]>(`/orgs/${orgId}/roles`),
  createRole: (orgId: string, payload: { name: string; key?: string; description?: string; permissions: string[]; rank?: number; data_scope?: string }) =>
    post<OrgRole>(`/orgs/${orgId}/roles`, payload),
  updateRole: (orgId: string, roleId: string, payload: { name: string; description?: string; permissions: string[]; rank?: number; data_scope?: string }) =>
    patch<OrgRole>(`/orgs/${orgId}/roles/${roleId}`, payload),
  removeRole: (orgId: string, roleId: string) => del(`/orgs/${orgId}/roles/${roleId}`),
  permissionCatalog: () => request<PermissionCatalog>("/permissions"),

  bindings: (orgId: string) => request<RoleBinding[]>(`/orgs/${orgId}/bindings`),
  createBinding: (orgId: string, payload: { user_id: string; role_key?: string; permissions: string[]; scope_type?: string; scope_id: string }) =>
    post<{ id: string }>(`/orgs/${orgId}/bindings`, payload),
  removeBinding: (orgId: string, id: string) => del(`/orgs/${orgId}/bindings/${id}`),

  ssoProviders: (orgId: string) => request<IdentityProvider[]>(`/orgs/${orgId}/sso`),
  ssoSchema: (orgId: string) => request<SsoSchema>(`/orgs/${orgId}/sso/schema`),
  createSso: (orgId: string, payload: Record<string, unknown>) =>
    post<IdentityProvider>(`/orgs/${orgId}/sso`, payload),
  updateSso: (orgId: string, id: string, payload: Record<string, unknown>) =>
    patch<IdentityProvider>(`/orgs/${orgId}/sso/${id}`, payload),
  removeSso: (orgId: string, id: string) => del(`/orgs/${orgId}/sso/${id}`),
  probeSso: (orgId: string, id: string) => post<ProbeResult>(`/orgs/${orgId}/sso/${id}/probe`),

  orgAudit: (orgId: string, limit = 100) =>
    request<AuditRow[]>(`/orgs/${orgId}/audit?limit=${limit}`),
  platformAudit: (limit = 100) => request<AuditRow[]>(`/audit?limit=${limit}`),
  searchUsers: (q = "") => request<OrgMember[]>(`/users?q=${encodeURIComponent(q)}`),
  userIdentities: (userId: string) => request<LinkedIdentity[]>(`/users/${userId}/identities`),
};
