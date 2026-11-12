"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { isActive, NAV, NAV_GROUPS } from "@/lib/nav";
import { useSession } from "@/lib/session";

/** Sidebar variant: grouped, labelled, with per-section counts. */
export function RailNav() {
  const { t } = useI18n();
  const pathname = usePathname();
  const counts = useCounts();
  const entries = useVisibleNav();

  return (
    <nav className="nav">
      {NAV_GROUPS.map((groupKey) => {
        const group = entries.filter((entry) => entry.groupKey === groupKey);
        if (!group.length) return null;
        return (
          <div key={groupKey}>
            <div className="nav-label">{t(groupKey)}</div>
            {group.map((entry) => {
              const active = isActive(pathname, entry.href);
              return (
                <Link
                  key={entry.href}
                  href={entry.href}
                  className={cx("nav-item", active && "active")}
                  title={t(entry.labelKey)}
                >
                  <Icon name={entry.icon} size={16} />
                  <span>{t(entry.labelKey)}</span>
                  {counts[entry.href] ? (
                    <span className="nav-count">{counts[entry.href]}</span>
                  ) : null}
                </Link>
              );
            })}
          </div>
        );
      })}
    </nav>
  );
}

/** Top-bar variant: flat, icon + label, horizontally scrollable on narrow screens. */
export function TopNav() {
  const { t } = useI18n();
  const pathname = usePathname();
  const counts = useCounts();
  const entries = useVisibleNav();

  return (
    <nav className="topnav" aria-label={t("prefs.navigation")}>
      {entries.map((entry) => {
        const active = isActive(pathname, entry.href);
        return (
          <Link
            key={entry.href}
            href={entry.href}
            className={cx("topnav-item", active && "active")}
            title={t(entry.labelKey)}
          >
            <Icon name={entry.icon} size={14} />
            <span>{t(entry.labelKey)}</span>
            {counts[entry.href] ? <span className="nav-count">{counts[entry.href]}</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}

/**
 * Drops entries the session may not use. Purely cosmetic: the API rejects the
 * request anyway, and the refusal is written to the audit log.
 */
function useVisibleNav() {
  const { can, loading, me } = useSession();
  if (loading || !me) return NAV;
  return NAV.filter((entry) => !entry.permission || can(entry.permission));
}

/** Counts come from one overview call and are shared by both nav layouts. */
function useCounts(): Record<string, number> {
  const [counts, setCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    let alive = true;
    api
      .overview()
      .then((data) => {
        if (!alive) return;
        setCounts({
          "/projects": data.counts.projects,
          "/templates": 4,
          "/agents": data.counts.agents,
          "/mcp": data.counts.mcp_servers,
          "/skills": data.counts.skills,
        });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  return counts;
}
