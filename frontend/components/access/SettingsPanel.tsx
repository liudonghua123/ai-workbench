"use client";

/**
 * Settings tab: the org profile, the account's own sessions and password, and
 * the one destructive action the console offers.
 *
 * Everything here is scoped to the organization in the URL, so an admin of one
 * org cannot rename another by editing a path segment — the API checks the same
 * thing this UI does.
 */

import { useState } from "react";

import { Icon } from "@/components/Icon";
import { CopyButton, Pill, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { errorMessage, relativeTime, untilTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Organization, SessionMe } from "@/lib/types";

export function SettingsPanel({
  org,
  me,
  can,
  reload,
  switchOrg,
  refresh,
}: {
  org: Organization;
  me: SessionMe;
  can: (permission: string) => boolean;
  reload: () => Promise<void>;
  switchOrg: (orgId: string) => Promise<void>;
  refresh: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const { push } = useToast();

  const [name, setName] = useState(org.name);
  const [description, setDescription] = useState(org.description);
  const [plan, setPlan] = useState(org.plan || "free");
  const [newOrg, setNewOrg] = useState("");
  const [purge, setPurge] = useState(false);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState("");

  const saveProfile = async () => {
    setBusy("profile");
    try {
      await api.updateOrg(org.id, { name, description, plan });
      push(t("org.saved"), "ok");
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy("");
    }
  };

  const createOrg = async () => {
    if (!newOrg.trim()) return;
    setBusy("create");
    try {
      const created = await api.createOrg({ name: newOrg.trim() });
      setNewOrg("");
      push(t("org.created"), "ok");
      await switchOrg(created.id);
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy("");
    }
  };

  const deleteOrg = async () => {
    if (!window.confirm(t("org.deleteConfirm", { name: org.name }))) return;
    setBusy("delete");
    try {
      await api.removeOrg(org.id, purge);
      push(t("org.deleted"), "ok");
      // land somewhere valid: pick a surviving org, or fall back to the entry page
      const fallback = me.organizations.find((item) => item.id !== org.id);
      if (fallback) await switchOrg(fallback.id);
      else {
        await refresh();
        window.location.href = "/";
      }
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy("");
    }
  };

  const changePassword = async () => {
    setBusy("password");
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      push(t("pwd.done"), "ok");
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy("");
    }
  };

  const editable = can("org:update");

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid grid-2" style={{ alignItems: "start" }}>
        <div className="card">
          <div className="card-head">
            <Icon name="building" size={15} />
            <span className="card-title">{t("org.settings")}</span>
            <span className="spacer" />
            {!editable ? <Pill tone="idle">{t("acc.readonly")}</Pill> : null}
          </div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div className="field">
              <label>{t("org.name")}</label>
              <input
                className="input"
                value={name}
                disabled={!editable}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="field">
              <label>{t("org.slug")}</label>
              <div className="row">
                <input className="input mono" value={org.slug} readOnly />
                <CopyButton value={org.id} />
              </div>
              <span className="hint">{t("org.slugHint")}</span>
            </div>
            <div className="field">
              <label>{t("org.description")}</label>
              <textarea
                className="textarea"
                rows={2}
                value={description}
                disabled={!editable}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
            <div className="field" style={{ maxWidth: 200 }}>
              <label>{t("org.plan")}</label>
              <select
                className="select"
                value={plan}
                disabled={!editable}
                onChange={(event) => setPlan(event.target.value)}
              >
                {["free", "team", "enterprise"].map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button
                className="btn btn-primary"
                onClick={saveProfile}
                disabled={!editable || busy === "profile" || !name.trim()}
              >
                {t("org.save")}
              </button>
            </div>
          </div>
        </div>

        <div className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-head">
              <Icon name="plus" size={15} />
              <span className="card-title">{t("org.new")}</span>
              <span className="spacer" />
              <span className="muted" style={{ fontSize: 11.5 }}>
                {t("org.newHint")}
              </span>
            </div>
            <div className="card-body">
              <div className="access-form-row">
                <div className="field" style={{ flex: 1 }}>
                  <input
                    className="input"
                    value={newOrg}
                    placeholder={t("org.name")}
                    onChange={(event) => setNewOrg(event.target.value)}
                    onKeyDown={(event) => event.key === "Enter" && createOrg()}
                  />
                </div>
                <button
                  className="btn"
                  onClick={createOrg}
                  disabled={busy === "create" || !newOrg.trim()}
                >
                  {t("org.create")}
                </button>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <Icon name="key" size={15} />
              <span className="card-title">{t("pwd.title")}</span>
            </div>
            <div className="card-body stack" style={{ gap: 12 }}>
              {me.user?.has_password ? (
                <div className="field">
                  <label>{t("pwd.current")}</label>
                  <input
                    className="input"
                    type="password"
                    value={current}
                    autoComplete="current-password"
                    onChange={(event) => setCurrent(event.target.value)}
                  />
                </div>
              ) : (
                <p className="hint" style={{ margin: 0 }}>
                  {t("pwd.noPassword")}
                </p>
              )}
              <div className="field">
                <label>{t("pwd.new")}</label>
                <input
                  className="input"
                  type="password"
                  value={next}
                  autoComplete="new-password"
                  onChange={(event) => setNext(event.target.value)}
                />
                <span className="hint">{t("pwd.hint")}</span>
              </div>
              <div className="row" style={{ justifyContent: "flex-end" }}>
                <button
                  className="btn"
                  onClick={changePassword}
                  disabled={busy === "password" || next.length < 8}
                >
                  {t("pwd.change")}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <Icon name="monitor" size={15} />
          <span className="card-title">{t("sess.title")}</span>
          <span className="count-badge">{me.sessions.length}</span>
          <span className="spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            {t("sess.hint")}
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>{t("sess.device")}</th>
              <th>{t("sess.ip")}</th>
              <th>{t("sess.created")}</th>
              <th>{t("sess.expires")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {me.sessions.map((session) => (
              <tr key={session.id}>
                <td>
                  <div className="row" style={{ gap: 6 }}>
                    <span className="truncate" style={{ maxWidth: 320 }}>
                      {session.user_agent || "—"}
                    </span>
                    {session.current ? <Pill tone="ok">{t("sess.current")}</Pill> : null}
                  </div>
                </td>
                <td className="mono muted">{session.ip || "—"}</td>
                <td className="muted">{relativeTime(session.created_at, locale)}</td>
                <td className="muted">{untilTime(session.expires_at, locale)}</td>
                <td style={{ textAlign: "right" }}>
                  {session.current ? null : (
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={async () => {
                        try {
                          await api.revokeSession(session.id);
                          push(t("sess.revoked"), "ok");
                          await reload();
                        } catch (err) {
                          push(errorMessage(err), "err");
                        }
                      }}
                    >
                      {t("sess.revoke")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {can("org:delete") ? (
        <div className="card access-danger">
          <div className="card-head">
            <Icon name="trash" size={15} />
            <span className="card-title">{t("org.danger")}</span>
            <span className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              {t("org.deleteHint")}
            </span>
          </div>
          <div className="card-body">
            <label className="sso-switch">
              <input
                type="checkbox"
                checked={purge}
                onChange={(event) => setPurge(event.target.checked)}
              />
              <span>
                <b>{t("org.purgeProjects")}</b>
                <span className="muted">{t("org.purgeHint")}</span>
              </span>
            </label>
            <div className="row" style={{ justifyContent: "flex-end", marginTop: 10 }}>
              <button
                className="btn btn-danger"
                onClick={deleteOrg}
                disabled={busy === "delete" || me.organizations.length < 2}
                title={
                  me.organizations.length < 2 ? t("org.cannotDeleteLast") : t("org.deleteHint")
                }
              >
                <Icon name="trash" size={13} />
                {t("org.delete")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
