"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { CreateProjectDialog } from "./CreateProjectDialog";
import { Preferences } from "./Preferences";
import { SessionMenu } from "./SessionMenu";
import { TopNav } from "./NavLinks";
import { api, API_BASE } from "@/lib/api";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { NAV } from "@/lib/nav";
import { usePrefs } from "@/lib/prefs";
import { useSession } from "@/lib/session";

export function Topbar() {
  const { t } = useI18n();
  const { nav } = usePrefs();
  const { can, loading, me } = useSession();
  const [open, setOpen] = useState(false);

  // authority is unknown until the session resolves; showing the button and
  // hiding it a moment later is worse than showing it late
  const mayCreate = !loading && (can("project:create") || !me);

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
        <SessionMenu />
        <Preferences />
        {mayCreate ? (
          <button
            className="btn btn-primary"
            onClick={() => setOpen(true)}
            title={t("topbar.newApp")}
            aria-label={t("topbar.newApp")}
          >
            <Icon name="plus" size={14} />
            <span className="btn-label">{t("topbar.newApp")}</span>
          </button>
        ) : null}
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
      <Link href="/" className="crumb-home">
        <Icon name="dashboard" size={14} />
        {t("topbar.home")}
      </Link>
      {root ? (
        <>
          <span className="sep">/</span>
          <Link
            href={`/${root}`}
            className={cx("crumb-mid", segments.length === 1 ? undefined : "muted")}
          >
            {entry ? t(entry.labelKey) : root}
          </Link>
        </>
      ) : null}
      {segments.length > 1 ? (
        <>
          <span className="sep">/</span>
          <b className="truncate crumb-leaf" style={{ maxWidth: 260 }}>
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
    <span className="topbar-api mono muted" title={API_BASE}>
      <i
        className="topbar-dot"
        style={{
          background: online === null ? "var(--ink-4)" : online ? "var(--ok)" : "var(--err)",
          boxShadow: online ? "0 0 0 3px var(--ok-soft)" : "none",
        }}
      />
      {online === null ? t("topbar.connecting") : online ? t("topbar.online") : t("topbar.offline")}
    </span>
  );
}
