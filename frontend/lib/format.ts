import type { MessageKey, Locale } from "./i18n";
import { translate } from "./i18n";

export function cx(...values: (string | false | null | undefined)[]): string {
  return values.filter(Boolean).join(" ");
}

const DATE_LOCALE: Record<Locale, string> = { en: "en-US", zh: "zh-CN" };

export function relativeTime(input?: string | null, locale: Locale = "en"): string {
  if (!input) return "—";
  const then = new Date(input).getTime();
  if (Number.isNaN(then)) return "—";
  const diff = Date.now() - then;
  const abs = Math.abs(diff);
  const min = 60_000;
  const t = (key: MessageKey, vars?: Record<string, string | number>) =>
    translate(locale, key, vars);

  if (abs < min) return t("time.justNow");
  if (abs < 60 * min) return t("time.minutesAgo", { n: Math.round(abs / min) });
  if (abs < 24 * 60 * min) return t("time.hoursAgo", { n: Math.round(abs / (60 * min)) });
  if (abs < 30 * 24 * 60 * min) return t("time.daysAgo", { n: Math.round(abs / (24 * 60 * min)) });
  return new Date(input).toLocaleDateString(DATE_LOCALE[locale]);
}

export function clockTime(input?: string | null, locale: Locale = "en"): string {
  if (!input) return "";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString(DATE_LOCALE[locale], { hour12: false });
}

export function fmtBytes(size: number): string {
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

export type Tone = "ok" | "warn" | "err" | "idle" | "accent";

/** Status maps carry a message key instead of a label so rendering stays locale-aware. */
export type StatusMap = Record<string, { key: MessageKey; tone: Tone }>;

export const PROJECT_STATUS: StatusMap = {
  creating: { key: "status.project.creating", tone: "warn" },
  ready: { key: "status.project.ready", tone: "ok" },
  failed: { key: "status.project.failed", tone: "err" },
  archived: { key: "status.project.archived", tone: "idle" },
};

export const DEPLOY_STATUS: StatusMap = {
  queued: { key: "status.deploy.queued", tone: "warn" },
  building: { key: "status.deploy.building", tone: "warn" },
  deploying: { key: "status.deploy.deploying", tone: "warn" },
  running: { key: "status.deploy.running", tone: "ok" },
  failed: { key: "status.deploy.failed", tone: "err" },
  stopped: { key: "status.deploy.stopped", tone: "idle" },
};

export const DOMAIN_STATUS: StatusMap = {
  active: { key: "status.domain.active", tone: "ok" },
  pending: { key: "status.domain.pending", tone: "warn" },
  error: { key: "status.domain.error", tone: "err" },
};

export const MCP_STATUS: StatusMap = {
  healthy: { key: "status.mcp.healthy", tone: "ok" },
  error: { key: "status.mcp.error", tone: "err" },
  unknown: { key: "status.mcp.unknown", tone: "idle" },
};

export const TYPE_LABEL: Record<string, MessageKey> = {
  fullstack: "type.fullstack",
  frontend: "type.frontend",
  backend: "type.backend",
  agent: "type.agent",
};

export const TYPE_ICON: Record<string, "layers" | "grid" | "cpu" | "terminal"> = {
  fullstack: "layers",
  frontend: "grid",
  backend: "terminal",
  agent: "cpu",
};

/** Resource kinds are shared by the overview table, template cards and dialogs. */
export const RESOURCE_LABEL: Record<string, MessageKey> = {
  storage: "common.storage",
  database: "common.database",
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
