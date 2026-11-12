"use client";

/**
 * Members tab: who belongs to the organization, what role they hold, and the
 * project-scoped grants layered on top of it.
 *
 * The owner row is deliberately immutable here — transferring ownership is a
 * separate, explicit act, so the UI does not offer a demote button that the API
 * would reject anyway.
 */

import { Fragment, useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { CopyButton, Empty, Pill, useToast } from "@/components/ui";
import { roleName, roleTone } from "@/lib/access";
import { api } from "@/lib/api";
import { cx, errorMessage, relativeTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type {
  Invitation,
  LinkedIdentity,
  OrgMember,
  OrgRole,
  Organization,
  Project,
  RoleBinding,
} from "@/lib/types";

type Props = {
  org: Organization;
  members: OrgMember[];
  invitations: Invitation[];
  roles: OrgRole[];
  bindings: RoleBinding[];
  projects: Project[];
  can: (permission: string) => boolean;
  currentUserId: string;
  currentEmail: string;
  reload: () => Promise<void>;
};

export function MembersPanel({
  org,
  members,
  invitations,
  roles,
  bindings,
  projects,
  can,
  currentUserId,
  currentEmail,
  reload,
}: Props) {
  const { t, locale } = useI18n();
  const { push } = useToast();
  const manage = can("member:manage");

  const [email, setEmail] = useState("");
  const [roleKey, setRoleKey] = useState("developer");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [inviteLink, setInviteLink] = useState("");
  const [temp, setTemp] = useState<{ name: string; password: string } | null>(null);
  const [expanded, setExpanded] = useState<string>("");
  const [identities, setIdentities] = useState<Record<string, LinkedIdentity[]>>({});

  const roleOptions = useMemo(
    () => roles.map((role) => ({ key: role.key, name: roleName(role, locale) })),
    [roles, locale],
  );

  const run = async (task: () => Promise<void>) => {
    setBusy(true);
    try {
      await task();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  const add = () =>
    run(async () => {
      if (!email.trim()) return;
      const result = await api.addMember(org.id, {
        email: email.trim().toLowerCase(),
        role_key: roleKey,
        title: title.trim() || undefined,
      });
      if (result.invited) {
        const token = result.accept_token ?? "";
        const link = token ? `${window.location.origin}/invite#token=${token}` : "";
        setInviteLink(link);
        push(t("mem.invitedToast"), "ok");
      } else {
        push(t("mem.created", { name: result.name || result.email }), "ok");
      }
      setEmail("");
      setTitle("");
      await reload();
    });

  const changeRole = (member: OrgMember, next: string) =>
    run(async () => {
      if (!member.membership_id) return;
      await api.updateMember(org.id, member.membership_id, { role_key: next });
      push(t("mem.roleUpdated", { name: member.name || member.email, role: next }), "ok");
      await reload();
    });

  const changeStatus = (member: OrgMember, status: string) =>
    run(async () => {
      if (!member.membership_id) return;
      await api.updateMember(org.id, member.membership_id, { status });
      await reload();
    });

  const remove = (member: OrgMember) => {
    if (!member.membership_id) return;
    if (!window.confirm(t("mem.removeConfirm", { name: member.name || member.email }))) return;
    run(async () => {
      await api.removeMember(org.id, member.membership_id as string);
      push(t("mem.removed", { name: member.name || member.email }), "ok");
      await reload();
    });
  };

  const resetPassword = (member: OrgMember) =>
    run(async () => {
      if (!member.membership_id) return;
      const result = await api.resetMemberPassword(org.id, member.membership_id);
      setTemp({ name: member.name || member.email, password: result.temporary_password });
    });

  const showIdentities = (member: OrgMember) => {
    const next = expanded === member.id ? "" : member.id;
    setExpanded(next);
    if (!next || identities[member.id]) return;
    api
      .userIdentities(member.id)
      .then((rows) => setIdentities((prev) => ({ ...prev, [member.id]: rows })))
      .catch(() => undefined);
  };

  const pending = invitations.filter((row) => row.status === "pending");

  return (
    <div className="stack" style={{ gap: 16 }}>
      {manage ? (
        <div className="card">
          <div className="card-head">
            <Icon name="users" size={15} />
            <span className="card-title">{t("mem.invite")}</span>
            <span className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              {t("mem.inviteHint")}
            </span>
          </div>
          <div className="card-body">
            <div className="access-form-row">
              <div className="field" style={{ flex: "2 1 220px" }}>
                <label>{t("mem.email")}</label>
                <input
                  className="input"
                  type="email"
                  value={email}
                  placeholder="teammate@example.com"
                  onChange={(event) => setEmail(event.target.value)}
                  onKeyDown={(event) => event.key === "Enter" && add()}
                />
              </div>
              <div className="field" style={{ flex: "1 1 150px" }}>
                <label>{t("mem.role")}</label>
                <select
                  className="select"
                  value={roleKey}
                  onChange={(event) => setRoleKey(event.target.value)}
                >
                  {roleOptions.map((role) => (
                    <option key={role.key} value={role.key}>
                      {role.name} ({role.key})
                    </option>
                  ))}
                </select>
              </div>
              <div className="field" style={{ flex: "1 1 140px" }}>
                <label>{t("mem.titleField")}</label>
                <input
                  className="input"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </div>
              <button className="btn btn-primary" onClick={add} disabled={busy || !email.trim()}>
                <Icon name="plus" size={14} />
                {t("mem.add")}
              </button>
            </div>

            {inviteLink ? (
              <div className="access-note">
                <Icon name="ticket" size={14} />
                <span>{t("mem.inviteLink")}</span>
                <code className="mono truncate" style={{ flex: 1 }}>
                  {inviteLink}
                </code>
                <CopyButton value={inviteLink} />
                <button className="icon-btn" onClick={() => setInviteLink("")} title={t("common.close")}>
                  <Icon name="close" size={13} />
                </button>
              </div>
            ) : null}

            {temp ? (
              <div className="access-note access-note-warn">
                <Icon name="key" size={14} />
                <span>{t("mem.resetDone")}</span>
                <code className="mono">{temp.password}</code>
                <CopyButton value={temp.password} />
                <span className="muted">{temp.name}</span>
                <button className="icon-btn" onClick={() => setTemp(null)} title={t("common.close")}>
                  <Icon name="close" size={13} />
                </button>
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="card">
        <div className="card-head">
          <Icon name="users" size={15} />
          <span className="card-title">{t("mem.title")}</span>
          <span className="count-badge">{members.length}</span>
          <span className="spacer" />
          {!manage ? <Pill tone="idle">{t("acc.readonly")}</Pill> : null}
        </div>

        {members.length === 0 ? (
          <Empty icon="users" title={t("mem.none")} hint={t("mem.noneHint")} />
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>{t("mem.email")}</th>
                <th>{t("mem.role")}</th>
                <th>{t("mem.titleField")}</th>
                <th>{t("mem.lastLogin")}</th>
                <th>{t("mem.identities")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {members.map((member) => {
                const owner = member.role_key === "owner";
                const userBindings = bindings.filter((row) => row.user_id === member.id);
                const isOpen = expanded === member.id;
                return (
                  <Fragment key={member.id}>
                    <tr>
                      <td>
                        <div className="access-identity">
                          <span className="access-avatar">
                            {(member.name || member.email || "?").slice(0, 1).toUpperCase()}
                          </span>
                          <div>
                            <div className="row" style={{ gap: 6 }}>
                              <b>{member.name || member.email}</b>
                              {member.id === currentUserId || member.email === currentEmail ? (
                                <span className="chip chip-you">{t("mem.you")}</span>
                              ) : null}
                              {member.status === "suspended" ? (
                                <Pill tone="err">{t("mem.statusSuspended")}</Pill>
                              ) : null}
                            </div>
                            <span className="muted mono" style={{ fontSize: 11 }}>
                              {member.email}
                            </span>
                          </div>
                        </div>
                      </td>
                      <td>
                        {manage && !owner ? (
                          <select
                            className="select select-sm"
                            value={member.role_key}
                            disabled={busy}
                            onChange={(event) => changeRole(member, event.target.value)}
                          >
                            {roleOptions.map((role) => (
                              <option key={role.key} value={role.key}>
                                {role.name}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <Pill tone={roleTone(member.role_key)}>{member.role_key}</Pill>
                        )}
                        {userBindings.length ? (
                          <span
                            className="chip chip-more"
                            style={{ marginLeft: 6 }}
                            title={userBindings
                              .map((row) => {
                                const project = projects.find((item) => item.id === row.scope_id);
                                return `${project?.name ?? row.scope_id}: ${row.role_key || row.permissions.join(", ")}`;
                              })
                              .join("\n")}
                          >
                            +{userBindings.length}
                          </span>
                        ) : null}
                      </td>
                      <td className="muted">{member.title || "—"}</td>
                      <td className="muted">
                        {member.last_login_at
                          ? relativeTime(member.last_login_at, locale)
                          : t("mem.never")}
                      </td>
                      <td>
                        <button className="linkbtn" onClick={() => showIdentities(member)}>
                          {member.has_password ? t("mem.noIdentity") : "SSO"}
                          <Icon name={isOpen ? "chevron-down" : "chevron-right"} size={12} />
                        </button>
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {manage && !owner ? (
                          <div className="row" style={{ justifyContent: "flex-end", gap: 2 }}>
                            <button
                              className="icon-btn"
                              title={t("mem.resetPassword")}
                              disabled={busy}
                              onClick={() => resetPassword(member)}
                            >
                              <Icon name="key" size={13} />
                            </button>
                            <button
                              className="icon-btn"
                              title={
                                member.status === "suspended" ? t("mem.activate") : t("mem.suspend")
                              }
                              disabled={busy}
                              onClick={() =>
                                changeStatus(
                                  member,
                                  member.status === "suspended" ? "active" : "suspended",
                                )
                              }
                            >
                              <Icon
                                name={member.status === "suspended" ? "check" : "lock"}
                                size={13}
                              />
                            </button>
                            <button
                              className="icon-btn"
                              title={t("mem.remove")}
                              disabled={busy}
                              onClick={() => remove(member)}
                            >
                              <Icon name="trash" size={13} />
                            </button>
                          </div>
                        ) : owner ? (
                          <span className="muted" style={{ fontSize: 11 }} title={t("mem.ownerLocked")}>
                            <Icon name="lock" size={12} />
                          </span>
                        ) : null}
                      </td>
                    </tr>
                    {isOpen ? (
                      <tr className="access-subrow">
                        <td colSpan={6}>
                          <div className="access-identities">
                            {(identities[member.id] ?? []).length === 0 ? (
                              <span className="muted">
                                {member.has_password ? t("mem.noIdentity") : t("common.loading")}
                              </span>
                            ) : (
                              identities[member.id].map((identity) => (
                                <span key={identity.id} className="chip">
                                  <Icon name="key" size={11} /> {identity.kind} ·{" "}
                                  <span className="mono">{identity.subject}</span>
                                </span>
                              ))
                            )}
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {pending.length ? (
        <div className="card">
          <div className="card-head">
            <Icon name="ticket" size={15} />
            <span className="card-title">{t("mem.pendingInvites")}</span>
            <span className="count-badge">{pending.length}</span>
          </div>
          <table className="table">
            <tbody>
              {pending.map((row) => (
                <tr key={row.id}>
                  <td className="mono">{row.email}</td>
                  <td>
                    <Pill tone={roleTone(row.role_key)}>{row.role_key}</Pill>
                  </td>
                  <td className="muted">
                    {t("mem.invitedBy", { name: row.invited_by || "—" })} ·{" "}
                    {relativeTime(row.created_at, locale)}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {manage ? (
                      <button
                        className="btn btn-sm btn-ghost"
                        disabled={busy}
                        onClick={() =>
                          run(async () => {
                            await api.revokeInvitation(org.id, row.id);
                            await reload();
                          })
                        }
                      >
                        {t("common.delete")}
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      <BindingsCard
        org={org}
        members={members}
        roles={roles}
        bindings={bindings}
        projects={projects}
        can={can}
        reload={reload}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ grants */

/**
 * Project-scoped grants. Membership decides the baseline inside the org; a
 * binding only ever *adds* capability, and only inside the one scope it names.
 */
function BindingsCard({ org, members, roles, bindings, projects, can, reload }: Omit<Props, "invitations" | "currentUserId" | "currentEmail">) {
  const { t, locale } = useI18n();
  const { push } = useToast();
  const manage = can("member:manage");
  const [userId, setUserId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [roleKey, setRoleKey] = useState("developer");
  const [busy, setBusy] = useState(false);

  const nameOf = (id: string) => {
    const project = projects.find((item) => item.id === id);
    return project?.name ?? id;
  };
  const userOf = (id: string) => {
    const member = members.find((item) => item.id === id);
    return member?.name || member?.email || id;
  };

  const add = async () => {
    if (!userId || !projectId) return;
    setBusy(true);
    try {
      await api.createBinding(org.id, {
        user_id: userId,
        role_key: roleKey,
        permissions: [],
        scope_type: "project",
        scope_id: projectId,
      });
      push(t("bind.created"), "ok");
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <Icon name="lock" size={15} />
        <span className="card-title">{t("bind.title")}</span>
        <span className="count-badge">{bindings.length}</span>
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 11.5 }}>
          {t("bind.subtitle")}
        </span>
      </div>

      {manage ? (
        <div className="card-body" style={{ borderBottom: "1px solid var(--line)" }}>
          <div className="access-form-row">
            <div className="field" style={{ flex: "1 1 180px" }}>
              <label>{t("bind.user")}</label>
              <select className="select" value={userId} onChange={(event) => setUserId(event.target.value)}>
                <option value="">{t("bind.selectUser")}</option>
                {members.map((member) => (
                  <option key={member.id} value={member.id}>
                    {member.name || member.email}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: "1 1 180px" }}>
              <label>{t("bind.project")}</label>
              <select
                className="select"
                value={projectId}
                onChange={(event) => setProjectId(event.target.value)}
              >
                <option value="">{t("bind.selectProject")}</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field" style={{ flex: "1 1 140px" }}>
              <label>{t("bind.role")}</label>
              <select className="select" value={roleKey} onChange={(event) => setRoleKey(event.target.value)}>
                {roles.map((role) => (
                  <option key={role.key} value={role.key}>
                    {roleName(role, locale)}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="btn"
              onClick={add}
              disabled={busy || !userId || !projectId}
              title={t("bind.add")}
            >
              <Icon name="plus" size={14} />
              {t("bind.add")}
            </button>
          </div>
        </div>
      ) : null}

      {bindings.length === 0 ? (
        <Empty icon="lock" title={t("bind.none")} hint={t("bind.noneHint")} />
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>{t("bind.user")}</th>
              <th>{t("bind.scope")}</th>
              <th>{t("bind.role")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {bindings.map((row) => (
              <tr key={row.id}>
                <td>{userOf(row.user_id)}</td>
                <td>
                  <span className="chip">
                    <Icon name="layers" size={11} />
                    {nameOf(row.scope_id)}
                  </span>
                </td>
                <td>
                  <Pill tone={roleTone(row.role_key)}>{row.role_key || "—"}</Pill>
                  {row.permissions.length ? (
                    <span className={cx("muted", "mono")} style={{ marginLeft: 6, fontSize: 11 }}>
                      {row.permissions.join(", ")}
                    </span>
                  ) : null}
                </td>
                <td style={{ textAlign: "right" }}>
                  {manage ? (
                    <button
                      className="icon-btn"
                      title={t("bind.remove")}
                      onClick={async () => {
                        if (!window.confirm(t("bind.removeConfirm", { name: userOf(row.user_id) })))
                          return;
                        try {
                          await api.removeBinding(org.id, row.id);
                          await reload();
                        } catch (err) {
                          push(errorMessage(err), "err");
                        }
                      }}
                    >
                      <Icon name="trash" size={13} />
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
