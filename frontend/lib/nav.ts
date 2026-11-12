import type { IconName } from "@/components/Icon";
import type { MessageKey } from "./i18n";

export type NavEntry = {
  href: string;
  labelKey: MessageKey;
  icon: IconName;
  groupKey: MessageKey;
  /** hide the entry when the session lacks this permission (server still enforces) */
  permission?: string;
};

/** Single source of truth for both the sidebar and the top-bar nav. */
export const NAV: NavEntry[] = [
  { href: "/", labelKey: "nav.dashboard", icon: "dashboard", groupKey: "group.workspace" },
  { href: "/projects", labelKey: "nav.projects", icon: "layers", groupKey: "group.workspace" },
  { href: "/templates", labelKey: "nav.templates", icon: "grid", groupKey: "group.workspace" },
  { href: "/agents", labelKey: "nav.agents", icon: "cpu", groupKey: "group.capabilities" },
  { href: "/mcp", labelKey: "nav.mcp", icon: "plug", groupKey: "group.capabilities" },
  { href: "/skills", labelKey: "nav.skills", icon: "puzzle", groupKey: "group.capabilities" },
  { href: "/events", labelKey: "nav.events", icon: "pulse", groupKey: "group.capabilities" },
  {
    href: "/access",
    labelKey: "nav.access",
    icon: "shield",
    groupKey: "group.access",
    permission: "member:read",
  },
  { href: "/settings", labelKey: "nav.settings", icon: "settings", groupKey: "group.system" },
];

export const NAV_GROUPS: MessageKey[] = [
  "group.workspace",
  "group.capabilities",
  "group.access",
  "group.system",
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}
