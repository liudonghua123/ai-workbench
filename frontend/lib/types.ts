export type ProjectType = "fullstack" | "frontend" | "backend" | "agent";
export type ProjectStatus = "creating" | "ready" | "failed" | "archived";

export interface Resource {
  id: string;
  kind: "storage" | "database";
  provider: string;
  name: string;
  endpoint: string;
  status: string;
  config: Record<string, unknown>;
}

export interface DomainBinding {
  id: string;
  fqdn: string;
  suffix: string;
  provider: string;
  status: "pending" | "active" | "error";
  target: string;
  is_primary: boolean;
  auto_generated: boolean;
  url: string;
}

export interface Deployment {
  id: string;
  project_id: string;
  environment: string;
  status: "queued" | "building" | "deploying" | "running" | "failed" | "stopped";
  version: string;
  commit_sha?: string;
  image?: string;
  url: string;
  provider: string;
  error: string;
  log?: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ProjectPreview {
  fqdn: string;
  url: string;
  port: number | null;
  command: string;
  status: string;
  deployment: Deployment | null;
  last_deployed: Deployment | null;
}

export interface Project {
  id: string;
  slug: string;
  name: string;
  description: string;
  type: ProjectType;
  status: ProjectStatus;
  template_key: string;
  template_version: string;
  repo_path: string;
  runtime: string;
  build_command: string;
  run_command: string;
  dev_command: string;
  dev_port: number | null;
  health_path: string;
  port: number | null;
  url: string;
  preview: ProjectPreview;
  git_initialized: boolean;
  owner_id: string;
  /** display name / email of the owner, resolved by the API for the UI */
  owner_name?: string;
  owner_email?: string;
  org_id?: string;
  created_at: string | null;
  updated_at: string | null;
  meta: Record<string, unknown>;
  resources: Resource[];
  domains: DomainBinding[];
  deployment: Deployment | null;
  deployment_count: number;
}

export interface PreviewState {
  project_id: string;
  environment: string;
  fqdn: string;
  url: string;
  port: number | null;
  command: string;
  status: string;
  latest: Deployment | null;
}

export interface GitFile {
  path: string;
  status: "modified" | "added" | "deleted" | "renamed" | "untracked";
  staged: boolean;
  additions: number;
  deletions: number;
}

export interface GitCommit {
  sha: string;
  short: string;
  author: string;
  date: string;
  message: string;
  tags: string[];
  refs: string[];
}

export interface GitStatus {
  is_repo: boolean;
  branch: string;
  files: GitFile[];
  clean: boolean;
  ahead: number;
  behind: number;
  head: GitCommit | null;
  tags: string[];
  staged_count: number;
}

export interface GitDiff {
  path: string;
  ref: string;
  diff: string;
  additions?: number;
  deletions?: number;
  error?: string;
}

export interface ConventionCheck {
  key: string;
  title: string;
  status: "pass" | "warn" | "fail";
  detail: string;
}

export interface ConventionReport {
  type: string;
  label: string;
  summary: string;
  spec: {
    type: string;
    label: string;
    summary: string;
    layout: { path: string; meaning: string }[];
    commands: { dev: string; build: string; run: string };
    health_path: string;
    resources: string[];
    conventions: { key: string; title: string; detail: string }[];
  };
  commands: { dev: string; build: string; run: string; health_path: string };
  preview: { fqdn: string; suffix: string; port: number | null; url: string };
  manifest: { path: string; present: boolean; overrides: Record<string, string> };
  checks: ConventionCheck[];
  score: { pass: number; warn: number; fail: number };
  conformant: boolean;
}

export interface PublishResult {
  project_id: string;
  version: string;
  commit: { committed: boolean; sha: string; short: string };
  tag: { tag?: string; error?: string };
  deployment: Deployment;
  summary: string;
}

export interface TemplateVariable {
  key: string;
  label: string;
  default: string;
  required: boolean;
  help: string;
}

export interface Template {
  key: string;
  name: string;
  type: ProjectType;
  description: string;
  version: string;
  icon: string;
  tags: string[];
  runtime: string;
  run_command: string;
  resources: string[];
  variables: TemplateVariable[];
  file_count: number;
  files: string[];
  notes: string;
  source: string;
}

export interface EnvVar {
  id: string;
  key: string;
  value: string;
  is_secret: boolean;
  scope: string;
}

export interface McpServer {
  id: string;
  name: string;
  description: string;
  transport: string;
  endpoint: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  status: "healthy" | "error" | "unknown";
  is_system: boolean;
  tools: { name: string; description: string }[];
  tool_count: number;
  last_checked_at: string | null;
  probe_error?: string;
}

export interface Skill {
  id: string;
  key: string;
  name: string;
  version: string;
  description: string;
  source: string;
  source_path: string;
  enabled: boolean;
  manifest: Record<string, unknown>;
  tools: string[];
}

export interface AgentStep {
  index: number;
  thought: string;
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  error: string;
  result_preview: string;
}

export interface AgentRun {
  run_id?: string;
  id?: string;
  agent?: { id: string; key: string; name: string; model: string; permission_scope: string };
  harness?: string;
  status: string;
  prompt?: string;
  output: string;
  steps: AgentStep[];
  created_at?: string;
  fallback?: boolean;
}

export interface Agent {
  id: string;
  key: string;
  name: string;
  description: string;
  model: string;
  system_prompt: string;
  mcp_server_ids: string[];
  skill_ids: string[];
  permission_scope: string;
  status: string;
  mcp_servers?: string[];
  skills?: string[];
}

export interface Overview {
  counts: {
    projects: number;
    projects_ready: number;
    deployments: number;
    deployments_running: number;
    deployments_failed: number;
    agents: number;
    mcp_servers: number;
    skills: number;
    skills_enabled: number;
  };
  domains: { suffix: string; base_url: string };
  providers: {
    storage: string;
    database: string;
    domain: string;
    deploy: string;
    registered: Record<string, string[]>;
  };
  deploy_mode: string;
  mcp_endpoint: string;
  recent_events: EventItem[];
  recent_runs: { id: string; prompt: string; status: string; created_at: string }[];
}

export interface EventItem {
  id: string;
  kind: string;
  message: string;
  project_id: string | null;
  created_at: string;
  payload?: Record<string, unknown>;
}

export interface TreeNode {
  path: string;
  name: string;
  type: "file" | "dir";
  size: number;
}

export interface Settings {
  app_name: string;
  api_prefix: string;
  domain_suffix: string;
  domain_base_url: string;
  deploy_mode: string;
  ide_url_template: string;
  mcp_path: string;
  data_dir: string;
  port_range: [number, number];
  default_agent_model: string;
  agent_harness: "llm" | "local";
  llm_configured: boolean;
  auth_enabled: boolean;
  sql: SqlMode;
  providers: { storage: string; database: string; domain: string; deploy: string };
  gateway: GatewayConfig;
}

/** How published apps are addressed: one listener, routed by Host. */
export interface GatewayConfig {
  enabled: boolean;
  active: boolean;
  scheme: string;
  port: number;
  bind_host: string;
  upstream_host: string;
  preview_suffix: string;
  route_ttl: number;
}

export interface GatewayRoute {
  host: string;
  url: string;
  project_id: string;
  slug: string;
  name: string;
  environment: "production" | "preview";
  upstream: string;
  custom: boolean;
  reachable: boolean;
}

export interface GatewayStatus {
  enabled: boolean;
  active: boolean;
  listening: boolean;
  scheme: string;
  port: number;
  bind_host: string;
  upstream_host: string;
  domain_suffix: string;
  preview_suffix: string;
  status_page: string;
  routes: GatewayRoute[];
  counts: {
    total: number;
    /** every route the listener serves, across tenants */
    all: number;
    production: number;
    preview: number;
    reachable: number;
  };
}

export interface SqlMode {
  mode: "readonly" | "write";
  readonly: boolean;
  row_limit: number;
  allowed_read: string[];
  allowed_write: string[];
}

export interface LlmStatus {
  configured: boolean;
  base_url: string;
  model: string;
  harness: "llm" | "local" | "local (fallback)";
  requested_harness: string;
  timeout_s: number;
  max_steps: number;
  note: string;
}

export interface LlmTest {
  ok: boolean;
  configured: boolean;
  model?: string;
  reply?: string;
  error?: string;
}

export interface HarnessReport {
  active: string;
  items: { name: string; available: boolean; active: boolean; kind: string }[];
}

export interface Me {
  principal: { name: string; role: "admin" | "editor" | "viewer"; anonymous: boolean };
  auth_enabled: boolean;
  roles: Record<string, string>;
}

/* ------------------------------------------------------- access control */

export interface PermissionDef {
  key: string;
  group: string;
  domain: string;
  action: string;
  label: string;
  label_zh: string;
  description: string;
  description_zh: string;
}

export interface PermissionGroup {
  key: string;
  label: string;
  label_zh: string;
  items: PermissionDef[];
}

export interface DataScope {
  key: string;
  label: string;
  label_zh?: string;
  description: string;
  description_zh?: string;
}

export interface PermissionCatalog {
  groups: PermissionGroup[];
  permissions: PermissionDef[];
  total: number;
  data_scopes?: DataScope[];
}

export interface Organization {
  id: string;
  slug: string;
  name: string;
  description: string;
  plan: string;
  status: string;
  owner_id: string;
  role_key: string;
  counts: { members?: number; projects?: number };
  settings?: Record<string, unknown>;
  created_at: string;
}

export interface OrgRole {
  id?: string;
  key: string;
  name: string;
  /** builtins ship a translated label; custom roles repeat `name` */
  name_zh?: string;
  description: string;
  description_zh?: string;
  permissions: string[];
  expanded?: string[];
  count: number;
  builtin: boolean;
  rank: number;
  members?: number;
  /** "all" reaches every project in the org, "own" only what the holder owns */
  data_scope?: string;
}

export interface OrgMember {
  id: string;
  email: string;
  name: string;
  avatar: string;
  status: string;
  role_key: string;
  membership_id?: string;
  membership_status?: string;
  title?: string;
  last_login_at: string | null;
  has_password?: boolean;
  bindings?: { id: string; scope_type: string; scope_id: string; role_key: string; permissions: string[] }[];
}

export interface Principal {
  name: string;
  role: string;
  anonymous: boolean;
  user_id: string;
  email: string;
  org_id: string;
  org_slug: string;
  is_platform_admin: boolean;
  auth_method: string;
  permissions: string[];
  organizations: { id: string; slug: string; name: string; role_key: string }[];
  /** data scope of the role this session is pinned to */
  data_scope?: string;
  /** projects where a RoleBinding gives extra permissions */
  projects_with_grants?: string[];
}

export interface SessionInfo {
  id: string;
  ip: string;
  user_agent: string;
  created_at: string;
  expires_at: string;
  current: boolean;
}

/** Full session view from /auth/me (the settings page uses the leaner /system/me). */
export interface SessionMe {
  principal: Principal;
  user: OrgMember | null;
  auth_enabled: boolean;
  organizations: Organization[];
  active_org: Organization | null;
  sessions: SessionInfo[];
}

export interface LoginProvider {
  key: string;
  kind: string;
  name: string;
  icon: string;
  mode: string;
  org_id: string;
  /** which tenant the connection belongs to — two orgs can point at one IdP */
  org_name?: string;
  org_slug?: string;
  ready: boolean;
  missing: string[];
}

export interface ProviderDirectory {
  auth_enabled: boolean;
  local_login: boolean;
  signup_enabled: boolean;
  providers: LoginProvider[];
  count: number;
}

export interface IdentityProvider {
  id: string;
  org_id: string;
  key: string;
  kind: string;
  name: string;
  icon: string;
  enabled: boolean;
  config: Record<string, unknown>;
  has_secret: boolean;
  default_role_key: string;
  auto_create_users: boolean;
  auto_join: boolean;
  allowed_domains: string[];
  status: "unconfigured" | "unchecked" | "ready" | "error" | string;
  missing: string[];
  last_error: string;
  last_check_at: string | null;
  schema?: SsoKindSchema | null;
}

export interface SsoField {
  key: string;
  label: string;
  label_zh?: string;
  placeholder?: string;
  help?: string;
  help_en?: string;
  type?: "text" | "password" | "select" | "json" | "switch" | "list" | "role";
  options?: string[];
  default?: string | boolean;
  required?: boolean;
}

export interface SsoKindSchema {
  kind?: string;
  label: string;
  label_zh?: string;
  docs?: string;
  blurb: string;
  blurb_en?: string;
  fields: SsoField[];
  secrets: SsoField[];
}

export interface SsoSchema {
  kinds: SsoKindSchema[];
  common: SsoField[];
}

export interface ProbeResult {
  ok: boolean;
  status: string;
  missing?: string[];
  message?: string;
  provider?: IdentityProvider;
  [key: string]: unknown;
}

export interface Invitation {
  id: string;
  org_id: string;
  email: string;
  role_key: string;
  status: string;
  invited_by: string;
  expires_at: string | null;
  created_at: string;
  accept_token?: string;
  accept_url?: string;
}

/** Extra grant that only applies inside one scope (today: one project). */
export interface RoleBinding {
  id: string;
  user_id: string;
  role_key: string;
  permissions: string[];
  scope_type: string;
  scope_id: string;
  created_by?: string;
  created_at: string;
}

/** An external subject linked to a local account (one row per IdP login). */
export interface LinkedIdentity {
  id: string;
  provider_key: string;
  kind: string;
  subject: string;
  email: string;
  display_name: string;
  last_login_at: string | null;
}

export interface AuditRow {
  id: string;
  org_id: string;
  actor_id: string;
  actor_name: string;
  action: string;
  label: string;
  resource_type: string;
  resource_id: string;
  status: "ok" | "denied" | "error";
  ip: string;
  detail: Record<string, unknown>;
  created_at: string;
}
