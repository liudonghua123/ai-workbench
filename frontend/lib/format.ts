export function cx(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

export function relativeTime(input?: string | null): string {
  if (!input) return "—";
  const then = new Date(input).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const abs = Math.abs(diff);
  const min = 60_000;
  if (abs < min) return "刚刚";
  if (abs < 60 * min) return `${Math.round(abs / min)} 分钟前`;
  if (abs < 24 * 60 * min) return `${Math.round(abs / (60 * min))} 小时前`;
  if (abs < 30 * 24 * 60 * min) return `${Math.round(abs / (24 * 60 * min))} 天前`;
  return new Date(input).toLocaleDateString("zh-CN");
}

export function clockTime(input?: string | null): string {
  if (!input) return "";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour12: false });
}

export function fmtBytes(size: number): string {
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

type Tone = "ok" | "warn" | "err" | "idle" | "accent";

export const PROJECT_STATUS: Record<string, { label: string; tone: Tone }> = {
  creating: { label: "创建中", tone: "warn" },
  ready: { label: "就绪", tone: "ok" },
  failed: { label: "失败", tone: "err" },
  archived: { label: "已归档", tone: "idle" },
};

export const DEPLOY_STATUS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "排队中", tone: "warn" },
  building: { label: "构建中", tone: "warn" },
  deploying: { label: "部署中", tone: "warn" },
  running: { label: "运行中", tone: "ok" },
  failed: { label: "失败", tone: "err" },
  stopped: { label: "已停止", tone: "idle" },
};

export const DOMAIN_STATUS: Record<string, { label: string; tone: Tone }> = {
  active: { label: "已生效", tone: "ok" },
  pending: { label: "待解析", tone: "warn" },
  error: { label: "异常", tone: "err" },
};

export const MCP_STATUS: Record<string, { label: string; tone: Tone }> = {
  healthy: { label: "健康", tone: "ok" },
  error: { label: "不可达", tone: "err" },
  unknown: { label: "未探测", tone: "idle" },
};

export const TYPE_LABEL: Record<string, string> = {
  fullstack: "全栈",
  frontend: "纯前端",
  backend: "纯后端",
  agent: "Agent 服务",
};

export const TYPE_ICON: Record<string, "layers" | "grid" | "cpu" | "terminal"> = {
  fullstack: "layers",
  frontend: "grid",
  backend: "terminal",
  agent: "cpu",
};

export function toneClass(tone: Tone | undefined): string {
  switch (tone) {
    case "ok":
      return "pill-ok";
    case "warn":
      return "pill-warn";
    case "err":
      return "pill-err";
    case "accent":
      return "pill-accent";
    default:
      return "";
  }
}

export function eventTone(kind: string): string {
  if (kind.includes("fail") || kind.includes("error")) return "var(--err)";
  if (kind.includes("ready") || kind.includes("succeeded") || kind.includes("success"))
    return "var(--ok)";
  if (kind.includes("agent")) return "var(--accent)";
  if (kind.includes("queued") || kind.includes("creating")) return "var(--warn)";
  return "var(--ink-4)";
}
