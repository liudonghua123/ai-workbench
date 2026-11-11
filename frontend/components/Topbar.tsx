"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon } from "./Icon";
import { ThemeToggle } from "./ThemeToggle";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { api, API_BASE } from "@/lib/api";

const LABELS: Record<string, string> = {
  projects: "项目",
  templates: "模板",
  agents: "Agents",
  mcp: "MCP 服务",
  skills: "Skills",
  events: "事件流",
};

export function Topbar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [online, setOnline] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    const ping = () =>
      api
        .settings()
        .then(() => alive && setOnline(true))
        .catch(() => alive && setOnline(false));
    ping();
    const timer = setInterval(ping, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  const segments = pathname.split("/").filter(Boolean);
  const root = segments[0] ?? "";

  return (
    <header className="topbar">
      <nav className="crumbs" aria-label="breadcrumb">
        <Link href="/" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Icon name="dashboard" size={14} />
          工作台
        </Link>
        {root ? (
          <>
            <span className="sep">/</span>
            <Link href={`/${root}`} className={segments.length === 1 ? undefined : "muted"}>
              {LABELS[root] ?? root}
            </Link>
          </>
        ) : null}
        {segments.length > 1 ? (
          <>
            <span className="sep">/</span>
            <b className="truncate" style={{ maxWidth: 260 }}>
              {segments[1]}
            </b>
          </>
        ) : null}
      </nav>

      <div className="topbar-actions">
        <span
          className="mono muted"
          title={API_BASE}
          style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}
        >
          <i
            style={{
              width: 6,
              height: 6,
              borderRadius: 999,
              background:
                online === null ? "var(--ink-4)" : online ? "var(--ok)" : "var(--err)",
              boxShadow: online ? "0 0 0 3px var(--ok-soft)" : "none",
            }}
          />
          {online === null ? "连接中" : online ? "API 正常" : "API 离线"}
        </span>
        <ThemeToggle />
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          <Icon name="plus" size={14} />
          新建应用
        </button>
      </div>

      <CreateProjectDialog open={open} onClose={() => setOpen(false)} />
    </header>
  );
}
