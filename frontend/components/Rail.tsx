"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon, type IconName } from "./Icon";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";

type NavEntry = { href: string; label: string; icon: IconName; group: string };

const NAV: NavEntry[] = [
  { href: "/", label: "总览", icon: "dashboard", group: "工作台" },
  { href: "/projects", label: "项目", icon: "layers", group: "工作台" },
  { href: "/templates", label: "模板", icon: "grid", group: "工作台" },
  { href: "/agents", label: "Agents", icon: "cpu", group: "能力" },
  { href: "/mcp", label: "MCP 服务", icon: "plug", group: "能力" },
  { href: "/skills", label: "Skills", icon: "puzzle", group: "能力" },
  { href: "/events", label: "事件流", icon: "pulse", group: "能力" },
];

export function Rail() {
  const pathname = usePathname();
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

  const groups = ["工作台", "能力"];

  return (
    <aside className="rail">
      <Link href="/" className="brand">
        <span className="brand-mark">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 15.5 9 6l4 7 3-4 4 6.5" />
            <path d="M3.5 19.5h17" opacity="0.55" />
          </svg>
        </span>
        <span className="brand-text">
          <span className="brand-name">AI Workbench</span>
          <span className="brand-sub">control plane</span>
        </span>
      </Link>

      <nav className="nav">
        {groups.map((group) => (
          <div key={group}>
            <div className="nav-label">{group}</div>
            {NAV.filter((entry) => entry.group === group).map((entry) => {
              const active =
                entry.href === "/" ? pathname === "/" : pathname.startsWith(entry.href);
              return (
                <Link
                  key={entry.href}
                  href={entry.href}
                  className={cx("nav-item", active && "active")}
                  title={entry.label}
                >
                  <Icon name={entry.icon} size={16} />
                  <span>{entry.label}</span>
                  {counts[entry.href] ? <span className="nav-count">{counts[entry.href]}</span> : null}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>

      <div className="rail-foot">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>约定</span>
          <b>&lt;slug&gt;.localtest.me</b>
        </div>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>MCP</span>
          <b>/mcp</b>
        </div>
      </div>
    </aside>
  );
}
