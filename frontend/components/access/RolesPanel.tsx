"use client";

/**
 * Roles tab: the permission sets a member can hold.
 *
 * A role is a name and a list of permission keys — nothing inherits from
 * anything, which is why this editor is a checkbox grid and not a tree. The
 * builtin five can be renamed or re-scoped, but only a custom role can be
 * deleted, and only while nobody holds it.
 */

import { useState } from "react";

import { Icon } from "@/components/Icon";
import { Empty, Pill, useToast } from "@/components/ui";
import { PermissionChips, PermissionPicker } from "@/components/access/PermissionPicker";
import { isWildcard, roleDescription, roleName, roleTone } from "@/lib/access";
import { api } from "@/lib/api";
import { errorMessage } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { OrgRole, Organization, PermissionCatalog } from "@/lib/types";

type Draft = {
  id?: string;
  key?: string;
  name: string;
  description: string;
  rank: number;
  permissions: string[];
  builtin: boolean;
  members: number;
};

const EMPTY_DRAFT: Draft = {
  name: "",
  description: "",
  rank: 10,
  permissions: [],
  builtin: false,
  members: 0,
};

export function RolesPanel({
  org,
  roles,
  catalog,
  can,
  reload,
}: {
  org: Organization;
  roles: OrgRole[];
  catalog: PermissionCatalog | null;
  can: (permission: string) => boolean;
  reload: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const { push } = useToast();
  const manage = can("role:manage");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);

  const edit = (role: OrgRole) =>
    setDraft({
      id: role.id,
      key: role.key,
      name: role.name,
      description: role.description,
      rank: role.rank,
      permissions: role.permissions,
      builtin: role.builtin,
      members: role.members ?? 0,
    });

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    try {
      if (draft.id) {
        await api.updateRole(org.id, draft.id, {
          name: draft.name,
          description: draft.description,
          permissions: draft.permissions,
          rank: draft.rank,
        });
      } else {
        await api.createRole(org.id, {
          name: draft.name,
          description: draft.description,
          permissions: draft.permissions,
          rank: draft.rank,
        });
      }
      push(t("role.saved"), "ok");
      setDraft(null);
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (role: OrgRole) => {
    if (!role.id) return;
    if (!window.confirm(t("role.deleteConfirm", { name: role.name }))) return;
    setBusy(true);
    try {
      await api.removeRole(org.id, role.id);
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card">
        <div className="card-head">
          <Icon name="shield" size={15} />
          <span className="card-title">{t("role.title")}</span>
          <span className="count-badge">{roles.length}</span>
          <span className="spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            {t("role.subtitle")}
          </span>
          {manage ? (
            <button className="btn btn-sm" onClick={() => setDraft({ ...EMPTY_DRAFT })}>
              <Icon name="plus" size={13} />
              {t("role.new")}
            </button>
          ) : null}
        </div>

        {draft ? (
          <div className="card-body" style={{ borderBottom: "1px solid var(--line)" }}>
            <div className="access-form-row">
              <div className="field" style={{ flex: "1 1 200px" }}>
                <label>{t("role.name")}</label>
                <input
                  className="input"
                  value={draft.name}
                  placeholder="Release manager"
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                />
              </div>
              <div className="field" style={{ flex: "2 1 260px" }}>
                <label>{t("role.description")}</label>
                <input
                  className="input"
                  value={draft.description}
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                />
              </div>
              <div className="field" style={{ flex: "0 0 96px" }}>
                <label>{t("role.rank")}</label>
                <input
                  className="input"
                  type="number"
                  value={draft.rank}
                  onChange={(event) => setDraft({ ...draft, rank: Number(event.target.value) })}
                />
              </div>
            </div>
            <p className="hint" style={{ margin: "6px 0 12px" }}>
              {t("role.rankHint")}
            </p>

            <PermissionPicker
              catalog={catalog}
              value={draft.permissions}
              onChange={(permissions) => setDraft({ ...draft, permissions })}
            />

            <div className="row" style={{ justifyContent: "flex-end", marginTop: 14 }}>
              <button className="btn btn-ghost" onClick={() => setDraft(null)} disabled={busy}>
                {t("common.cancel")}
              </button>
              <button
                className="btn btn-primary"
                onClick={save}
                disabled={busy || !draft.name.trim() || draft.permissions.length === 0}
              >
                {draft.id ? t("role.save") : t("role.create")}
              </button>
            </div>
          </div>
        ) : null}

        {roles.length === 0 ? (
          <Empty icon="shield" title={t("role.title")} hint={t("common.loading")} />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t("role.name")}</th>
                <th>{t("role.permissions")}</th>
                <th>{t("role.members")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.key}>
                  <td style={{ minWidth: 180 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <Pill tone={roleTone(role.key)}>{role.key}</Pill>
                      <b>{roleName(role, locale)}</b>
                    </div>
                    {roleDescription(role, locale) ? (
                      <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>
                        {roleDescription(role, locale)}
                      </div>
                    ) : null}
                  </td>
                  <td>
                    {isWildcard(role) ? (
                      <span className="chip chip-you">{t("role.ownerFixed")}</span>
                    ) : (
                      <PermissionChips catalog={catalog} grants={role.permissions} />
                    )}
                    <div className="muted mono" style={{ fontSize: 10.5, marginTop: 4 }}>
                      {t("role.count", { n: role.count })}
                    </div>
                  </td>
                  <td className="muted">{role.members ?? 0}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {manage && role.key !== "owner" ? (
                      <>
                        <button className="btn btn-sm btn-ghost" onClick={() => edit(role)}>
                          {t("role.edit")}
                        </button>
                        {!role.builtin ? (
                          <button
                            className="btn btn-sm btn-ghost"
                            disabled={busy || (role.members ?? 0) > 0}
                            title={
                              (role.members ?? 0) > 0
                                ? t("role.inUse", { n: role.members ?? 0 })
                                : t("role.delete")
                            }
                            onClick={() => remove(role)}
                          >
                            <Icon name="trash" size={13} />
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <span className="muted" style={{ fontSize: 11 }}>
                        {role.builtin ? t("role.builtin") : t("role.custom")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
