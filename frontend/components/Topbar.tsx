"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { Preferences } from "./Preferences";
import { TopNav } from "./NavLinks";
import { api, API_BASE } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { NAV } from "@/lib/nav";
import { usePrefs } from "@/lib/prefs";

export function Topbar() {
  const { t } = useI18n();
  const { nav } = usePrefs();
  const [open, setOpen] = useState(false);

  return (
    <header className="topbar">
      {nav === "top" ? (
        <>
          <Link href="/" className="brand-inline">
            <span className="brand-mark" style={{ width: 24, height: 24, borderRadius: 7 }}>
              <Icon name="route" size={13} />
            </span>
            <span className="brand-name">AI Workbench</span>
          </Link>
          <TopNav />
        </>
      ) : (
        <Crumbs />
      )}

      <div className="topbar-actions">
        <ApiStatus />
        <Preferences />
        <button className="btn btn-primary" onClick={() => setOpen(true)}>
          <Icon name="plus" size={14} />
          {t("topbar.newApp")}
        </button>
      </div>

      <CreateProjectDialog open={open} onClose={() => setOpen(false)} />
    </header>
  );
}

function Crumbs() {
  const { t } = useI18n();
  const pathname = usePathname() ?? "/";
  const segments = pathname.split("/").filter(Boolean);
  const root = segments[0] ?? "";
  const entry = NAV.find((item) => item.href === `/${root}`);

  return (
    <nav className="crumbs" aria-label="breadcrumb">
      <Link href="/" style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <Icon name="dashboard" size={14} />
        {t("topbar.home")}
      </Link>
      {root ? (
        <>
          <span className="sep">/</span>
          <Link href={`/${root}`} className={segments.length === 1 ? undefined : "muted"}>
            {entry ? t(entry.labelKey) : root}
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
  );
}

/** Polls the control plane so the header reflects real connectivity. */
function ApiStatus() {
  const { t } = useI18n();
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

  return (
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
          background: online === null ? "var(--ink-4)" : online ? "var(--ok)" : "var(--err)",
          boxShadow: online ? "0 0 0 3px var(--ok-soft)" : "none",
        }}
      />
      {online === null ? t("topbar.connecting") : online ? t("topbar.online") : t("topbar.offline")}
    </span>
  );
}
