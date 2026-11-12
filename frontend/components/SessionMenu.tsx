"use client";

/**
 * Organization switcher + account menu for the top bar.
 *
 * The switcher does not filter anything client-side: choosing an organization
 * re-pins the *session* on the server, so every subsequent request — REST, SSE
 * and MCP alike — is evaluated against that tenant. Anything that caches data
 * per organization simply reloads.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Icon } from "@/components/Icon";
import { Pill } from "@/components/ui";
import { roleTone } from "@/lib/access";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export function SessionMenu() {
  const { t } = useI18n();
  const router = useRouter();
  const { me, authEnabled, activeOrg, switchOrg, logout } = useSession();
  const [openOrg, setOpenOrg] = useState(false);
  const [openUser, setOpenUser] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!authEnabled || !me || me.principal.anonymous) return null;

  const orgs = me.organizations;
  const identifier = me.user?.name || me.principal.email || me.principal.name;

  const pick = async (orgId: string) => {
    setOpenOrg(false);
    if (orgId === activeOrg?.id) return;
    setBusy(true);
    try {
      await switchOrg(orgId);
      router.push("/");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="session-menu">
      <div className="popover-anchor">
        <button
          className="btn btn-sm"
          onClick={() => {
            setOpenOrg((value) => !value);
            setOpenUser(false);
          }}
          disabled={busy}
          title={t("org.switch")}
        >
          <Icon name="building" size={13} />
          <span className="truncate" style={{ maxWidth: 130 }}>
            {activeOrg?.name ?? t("org.switch")}
          </span>
          <Icon name="chevron-down" size={12} />
        </button>

        {openOrg ? (
          <>
            <div className="popover-backdrop" onClick={() => setOpenOrg(false)} />
            <div className="popover popover-left">
              <div className="popover-head">{t("org.switch")}</div>
              {orgs.map((org) => (
                <button
                  key={org.id}
                  className="popover-item"
                  onClick={() => pick(org.id)}
                  disabled={busy}
                >
                  <span className="popover-item-body">
                    <b>{org.name}</b>
                    <span className="muted mono">{org.slug}</span>
                  </span>
                  <Pill tone={roleTone(org.role_key)}>{org.role_key}</Pill>
                  {org.id === activeOrg?.id ? (
                    <Icon name="check" size={13} />
                  ) : (
                    <span style={{ width: 13 }} />
                  )}
                </button>
              ))}
              <Link
                className="popover-item popover-foot"
                href="/access?tab=settings"
                onClick={() => setOpenOrg(false)}
              >
                <Icon name="plus" size={13} />
                {t("org.new")}
              </Link>
            </div>
          </>
        ) : null}
      </div>

      <div className="popover-anchor">
        <button
          className="avatar-btn"
          onClick={() => {
            setOpenUser((value) => !value);
            setOpenOrg(false);
          }}
          title={identifier}
        >
          <span className="access-avatar">{(identifier || "?").slice(0, 1).toUpperCase()}</span>
        </button>

        {openUser ? (
          <>
            <div className="popover-backdrop" onClick={() => setOpenUser(false)} />
            <div className="popover">
              <div className="popover-user">
                <b>{identifier}</b>
                <span className="muted mono">{me.principal.email || "—"}</span>
                <div className="row" style={{ gap: 6, marginTop: 4 }}>
                  <Pill tone={roleTone(me.principal.role)}>{me.principal.role}</Pill>
                  {me.principal.auth_method ? (
                    <span className="chip mono">{me.principal.auth_method}</span>
                  ) : null}
                </div>
              </div>
              <Link className="popover-item" href="/access" onClick={() => setOpenUser(false)}>
                <Icon name="shield" size={13} />
                {t("acc.title")}
              </Link>
              <Link className="popover-item" href="/settings" onClick={() => setOpenUser(false)}>
                <Icon name="settings" size={13} />
                {t("nav.settings")}
              </Link>
              <button
                className="popover-item popover-danger"
                onClick={async () => {
                  setOpenUser(false);
                  await logout();
                  router.push("/login");
                }}
              >
                <Icon name="logout" size={13} />
                {t("auth.switchAccount")}
              </button>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
