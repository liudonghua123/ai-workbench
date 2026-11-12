/**
 * Access-control helpers shared by the access console and the top bar.
 *
 * Everything here reads the *catalog the server returned*. There is no second
 * copy of the permission list in the bundle, so the role editor cannot drift
 * from the API that enforces it — the only hard-coded knowledge is cosmetic
 * (which tone a role badge gets, how an audit action reads in English).
 */

import type { Locale, MessageKey } from "./i18n";
import type { AuditRow, OrgRole, PermissionCatalog, PermissionDef } from "./types";

/** Resolve `*` and `domain:*` the same way the backend's `expand()` does. */
export function expandGrants(
  grants: string[],
  catalog: PermissionCatalog | null,
): Set<string> {
  const resolved = new Set<string>();
  const keys = catalog?.permissions.map((item) => item.key) ?? [];
  for (const grant of grants ?? []) {
    if (!grant) continue;
    if (grant === "*") keys.forEach((key) => resolved.add(key));
    else if (grant.endsWith(":*")) {
      const domain = grant.slice(0, -2);
      keys.filter((key) => key.startsWith(`${domain}:`)).forEach((key) => resolved.add(key));
    } else resolved.add(grant);
  }
  // with no catalog loaded, at least keep the literal grants visible
  if (keys.length === 0) (grants ?? []).forEach((grant) => resolved.add(grant));
  return resolved;
}

const DEFINITIONS = new WeakMap<PermissionCatalog, Map<string, PermissionDef>>();

export function definitionOf(
  catalog: PermissionCatalog | null,
  key: string,
): PermissionDef | undefined {
  if (!catalog) return undefined;
  let index = DEFINITIONS.get(catalog);
  if (!index) {
    index = new Map(catalog.permissions.map((item) => [item.key, item]));
    DEFINITIONS.set(catalog, index);
  }
  return index.get(key);
}

export function permissionLabel(
  catalog: PermissionCatalog | null,
  key: string,
  locale: Locale,
): string {
  const def = definitionOf(catalog, key);
  if (!def) return key;
  return locale === "zh" ? def.label_zh : def.label;
}

/* ---------------------------------------------------------------- roles */

/** Badge tone per builtin role; custom roles stay neutral. */
const ROLE_TONE: Record<string, "accent" | "ok" | "warn" | "idle"> = {
  owner: "accent",
  admin: "ok",
  developer: "idle",
  operator: "warn",
  viewer: "idle",
};

export function roleTone(key: string): "accent" | "ok" | "warn" | "idle" {
  return ROLE_TONE[key] ?? "idle";
}

/** Builtin roles are translated server-side; custom roles keep what was typed. */
export function roleName(
  role: Pick<OrgRole, "key" | "name" | "name_zh">,
  locale: Locale,
): string {
  if (locale === "zh") return role.name_zh || role.name || role.key;
  return role.name || role.key;
}

export function roleDescription(
  role: Pick<OrgRole, "description" | "description_zh">,
  locale: Locale,
): string {
  if (locale === "zh") return role.description_zh || role.description || "";
  return role.description || "";
}

/** Owner is the only role that answers with a wildcard rather than a list. */
export function isWildcard(role: Pick<OrgRole, "permissions">): boolean {
  return (role.permissions ?? []).includes("*");
}

/** A role with more permissions than this gets a collapsed summary. */
export const PERMISSION_PREVIEW = 6;

/* ---------------------------------------------------------------- audit */

const ACTION_LABELS: Record<string, { en: string; zh: string }> = {
  "auth.login": { en: "Signed in", zh: "登录" },
  "auth.login_failed": { en: "Sign-in failed", zh: "登录失败" },
  "auth.logout": { en: "Signed out", zh: "登出" },
  "auth.sso_start": { en: "SSO started", zh: "发起 SSO" },
  "auth.sso_failed": { en: "SSO failed", zh: "SSO 失败" },
  "auth.session_revoked": { en: "Session revoked", zh: "吊销会话" },
  "org.created": { en: "Organization created", zh: "创建组织" },
  "org.updated": { en: "Organization updated", zh: "修改组织" },
  "org.deleted": { en: "Organization deleted", zh: "删除组织" },
  "member.invited": { en: "Member invited", zh: "邀请成员" },
  "member.joined": { en: "Member joined", zh: "成员加入" },
  "member.role_changed": { en: "Member role changed", zh: "变更成员角色" },
  "member.removed": { en: "Member removed", zh: "移除成员" },
  "role.created": { en: "Role created", zh: "创建角色" },
  "role.updated": { en: "Role updated", zh: "修改角色" },
  "role.deleted": { en: "Role deleted", zh: "删除角色" },
  "binding.created": { en: "Project grant added", zh: "授予项目权限" },
  "binding.removed": { en: "Project grant removed", zh: "回收项目权限" },
  "sso.created": { en: "Identity provider added", zh: "新增身份源" },
  "sso.updated": { en: "Identity provider updated", zh: "修改身份源" },
  "sso.deleted": { en: "Identity provider deleted", zh: "删除身份源" },
  "sso.probed": { en: "Identity provider tested", zh: "探测身份源" },
  "project.created": { en: "Project created", zh: "创建项目" },
  "project.deleted": { en: "Project deleted", zh: "删除项目" },
  "project.published": { en: "Project published", zh: "发布项目" },
  "access.denied": { en: "Access denied", zh: "权限被拒绝" },
};

/**
 * The backend ships a Chinese `label`; translate the known actions so an
 * English UI does not mix languages, and fall back to the server label.
 */
export function auditActionLabel(row: Pick<AuditRow, "action" | "label">, locale: Locale): string {
  const known = ACTION_LABELS[row.action];
  if (known) return locale === "zh" ? known.zh : known.en;
  return row.label || row.action;
}

export const AUDIT_STATUS: Record<string, { key: MessageKey; tone: "ok" | "err" | "warn" }> = {
  ok: { key: "audit.ok", tone: "ok" },
  denied: { key: "audit.denied", tone: "err" },
  error: { key: "audit.error", tone: "warn" },
};

/* ---------------------------------------------------------------- sso */

const SSO_STATUS: Record<string, { key: MessageKey; tone: "ok" | "warn" | "err" | "idle" }> = {
  ready: { key: "sso.statusReady", tone: "ok" },
  unchecked: { key: "sso.statusUnchecked", tone: "warn" },
  unconfigured: { key: "sso.statusUnconfigured", tone: "idle" },
  error: { key: "sso.statusError", tone: "err" },
};

export function ssoStatus(status: string): { key: MessageKey; tone: "ok" | "warn" | "err" | "idle" } {
  return SSO_STATUS[status] ?? { key: "sso.statusUnconfigured", tone: "idle" };
}
