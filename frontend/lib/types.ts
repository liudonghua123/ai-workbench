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
  url: string;
  provider: string;
  error: string;
  log?: string;
  started_at: string | null;
  finished_at: string | null;
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
  run_command: string;
  port: number | null;
  url: string;
  git_initialized: boolean;
  created_at: string | null;
  updated_at: string | null;
  meta: Record<string, unknown>;
  resources: Resource[];
  domains: DomainBinding[];
  deployment: Deployment | null;
  deployment_count: number;
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
}
