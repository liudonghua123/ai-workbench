"use client";

/**
 * The access console: members, roles, identity providers, audit trail and the
 * organization itself — one tab each, all scoped to the organization the session
 * is currently pinned to.
 *
 * Two deliberate choices:
 *
 * * Every tab is *readable* by any member that holds the matching `:read`
 *   permission, and every mutating control is hidden behind `can(...)`. Hiding a
 *   button is a courtesy, not a boundary — the API re-derives authority per
 *   request and logs the denials.
 * * Data is loaded once per organization and handed to the panels, so the tab
 *   counts and the tables can never disagree.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { AuditPanel } from "@/components/access/AuditPanel";
import { MembersPanel } from "@/components/access/MembersPanel";
import { RolesPanel } from "@/components/access/RolesPanel";
import { SettingsPanel } from "@/components/access/SettingsPanel";
import { SsoPanel } from "@/components/access/SsoPanel";
import { Empty, Pill, Skeleton, Tabs } from "@/components/ui";
import { roleTone } from "@/lib/access";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import type {
  AuditRow,
  Invitation,
  IdentityProvider,
  OrgMember,
  OrgRole,
  PermissionCatalog,
  Project,
  RoleBinding,
  SsoSchema,
} from "@/lib/types";

type Tab = "members" | "roles" | "sso" | "audit" | "settings";

const TABS: { key: Tab; labelKey: "acc.tabMembers" | "acc.tabRoles" | "acc.tabSso" | "acc.tabAudit" | "acc.tabSettings" }[] = [
  { key: "members", labelKey: "acc.tabMembers" },
  { key: "roles", labelKey: "acc.tabRoles" },
  { key: "sso", labelKey: "acc.tabSso" },
  { key: "audit", labelKey: "acc.tabAudit" },
  { key: "settings", labelKey: "acc.tabSettings" },
];

/** Read permission that gates each tab; used for the "why can't I see this" hint. */
const TAB_PERMISSION: Record<Tab, string> = {
  members: "member:read",
  roles: "role:read",
  sso: "sso:read",
  audit: "audit:read",
  settings: "org:read",
};

export default function AccessPage() {
  const { t } = useI18n();
  const { me, loading: sessionLoading, activeOrg, can, refresh, switchOrg } = useSession();

  const [tab, setTab] = useState<Tab>("members");
  const [catalog, setCatalog] = useState<PermissionCatalog | null>(null);
  const [roles, setRoles] = useState<OrgRole[]>([]);
  const [members, setMembers] = useState<OrgMember[]>([]);
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [providers, setProviders] = useState<IdentityProvider[]>([]);
  const [schema, setSchema] = useState<SsoSchema | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [bindings, setBindings] = useState<RoleBinding[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [denied, setDenied] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const org = activeOrg;

  // deep links: /access?tab=roles
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("tab");
    if (wanted && TABS.some((item) => item.key === wanted)) setTab(wanted as Tab);
  }, []);

  const reload = useCallback(async () => {
    if (!org) return;
    const orgId = org.id;
    setLoading(true);
    setError("");
    const nextDenied: Record<string, boolean> = {};
    const attempt = async <T,>(label: string, task: () => Promise<T>, fallback: T): Promise<T> => {
      try {
        return await task();
      } catch (err) {
        // a missing read permission is a state, not an error page
        if (err instanceof Error && /403|权限|forbidden/i.test(err.message)) {
          nextDenied[label] = true;
          return fallback;
        }
        throw err;
      }
    };

    try {
      const [nextCatalog, nextRoles, nextMembers, nextInvites, nextSso, nextSchema, nextAudit, nextBindings, nextProjects] =
        await Promise.all([
          api.permissionCatalog(),
          attempt<OrgRole[]>("roles", () => api.roles(orgId), []),
          attempt<OrgMember[]>("members", () => api.members(orgId), []),
          attempt<Invitation[]>("invitations", () => api.invitations(orgId), []),
          attempt<IdentityProvider[]>("sso", () => api.ssoProviders(orgId), []),
          attempt<SsoSchema | null>("sso", () => api.ssoSchema(orgId), null),
          attempt<AuditRow[]>("audit", () => api.orgAudit(orgId, 200), []),
          attempt<RoleBinding[]>("bindings", () => api.bindings(orgId), []),
          api.projects().catch(() => [] as Project[]),
        ]);
      setCatalog(nextCatalog);
      setRoles(nextRoles);
      setMembers(nextMembers);
      setInvitations(nextInvites);
      setProviders(nextSso);
      setSchema(nextSchema);
      setAudit(nextAudit);
      setBindings(nextBindings);
      setProjects(nextProjects);
      setDenied(nextDenied);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [org]);

  useEffect(() => {
    reload();
  }, [reload]);

  const tabs = useMemo(
    () =>
      TABS.map((item) => ({
        key: item.key,
        label: t(item.labelKey),
        count:
          item.key === "members"
            ? members.length
            : item.key === "roles"
              ? roles.length
              : item.key === "sso"
                ? providers.length
                : undefined,
      })),
    [t, members.length, roles.length, providers.length],
  );

  if (sessionLoading || (loading && !catalog && !error)) {
    return (
      <main className="content">
        <Skeleton height={44} />
        <div style={{ height: 14 }} />
        <Skeleton height={260} />
      </main>
    );
  }

  if (!me || !org) {
    return (
      <main className="content">
        <div className="card">
          <Empty
            icon="shield"
            title={t("acc.forbidden")}
            hint={t("acc.forbiddenHint")}
            action={
              <a className="btn btn-primary btn-sm" href="/login">
                {t("auth.title")}
              </a>
            }
          />
        </div>
      </main>
    );
  }

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("acc.title")}</h1>
          <p className="page-desc">{t("acc.subtitle")}</p>
        </div>
        <div className="spacer" />
        <div className="access-org-chip">
          <span className="access-avatar" style={{ background: "var(--surface-3)", color: "var(--ink-2)" }}>
            <Icon name="building" size={14} />
          </span>
          <div>
            <b>{org.name}</b>
            <span className="mono muted">{org.slug}</span>
          </div>
          <Pill tone={roleTone(me.principal.role)}>{me.principal.role}</Pill>
        </div>
        <button className="btn" onClick={reload} disabled={loading}>
          <Icon name="refresh" size={14} />
          {t("common.refresh")}
        </button>
      </div>

      {me.principal.anonymous ? (
        <div className="access-banner">
          <Icon name="shield" size={14} />
          <span>{t("auth.disabledHint")}</span>
        </div>
      ) : null}

      {error ? (
        <div className="card" style={{ marginBottom: 16 }}>
          <Empty
            icon="close"
            title={t("common.failed")}
            hint={error}
            action={
              <button className="btn btn-sm" onClick={reload}>
                {t("common.refresh")}
              </button>
            }
          />
        </div>
      ) : null}

      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {denied[tab] ? (
        <div className="card">
          <Empty
            icon="lock"
            title={t("acc.forbidden")}
            hint={t("acc.readonlyHint", { org: org.name, permission: TAB_PERMISSION[tab] })}
          />
        </div>
      ) : tab === "members" ? (
        <MembersPanel
          org={org}
          members={members}
          invitations={invitations}
          roles={roles}
          bindings={bindings}
          projects={projects}
          can={can}
          currentUserId={me.principal.user_id}
          currentEmail={me.principal.email}
          reload={reload}
        />
      ) : tab === "roles" ? (
        <RolesPanel org={org} roles={roles} catalog={catalog} can={can} reload={reload} />
      ) : tab === "sso" ? (
        <SsoPanel
          org={org}
          providers={providers}
          schema={schema}
          roles={roles}
          can={can}
          reload={reload}
        />
      ) : tab === "audit" ? (
        <AuditPanel rows={audit} />
      ) : (
        <SettingsPanel
          org={org}
          me={me}
          can={can}
          reload={reload}
          switchOrg={switchOrg}
          refresh={refresh}
        />
      )}
    </main>
  );
}
