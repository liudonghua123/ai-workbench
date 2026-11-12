"use client";

/**
 * Single sign-on tab.
 *
 * The form is not hand-written per provider: it renders whatever
 * `/orgs/{id}/sso/schema` declares, so adding a sixth IdP on the backend makes a
 * sixth card appear here with no React change. Two rules the UI enforces
 * because the backend cannot:
 *
 * * a secret field left empty means "keep the stored one" — never "clear it";
 * * saving resets the connection to *configured, not verified*, so the only way
 *   to see "verified" is to actually run the probe.
 */

import { useState } from "react";

import { Icon, type IconName } from "@/components/Icon";
import { CopyButton, Empty, Pill, useToast } from "@/components/ui";
import { ssoStatus } from "@/lib/access";
import { api, API_BASE } from "@/lib/api";
import { errorMessage, relativeTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type {
  IdentityProvider,
  OrgRole,
  Organization,
  SsoField,
  SsoKindSchema,
  SsoSchema,
} from "@/lib/types";

const KIND_ICON: Record<string, IconName> = {
  cas: "shield",
  oauth2: "key",
  oidc: "lock",
  wecom: "pulse",
  dingtalk: "spark",
  mock: "ticket",
};

type Draft = {
  id?: string;
  kind: string;
  name: string;
  enabled: boolean;
  default_role_key: string;
  auto_create_users: boolean;
  auto_join: boolean;
  domains: string;
  /** every text-ish field, JSON included, kept as the raw string the user typed */
  values: Record<string, string>;
  hasSecret: boolean;
  status: string;
};

function kindSchema(schema: SsoSchema | null, kind: string): SsoKindSchema | undefined {
  return schema?.kinds.find((item) => item.kind === kind);
}

function draftFrom(
  provider: IdentityProvider | null,
  kind: string,
  schema: SsoSchema | null,
): Draft {
  const definition = kindSchema(schema, kind);
  const fields = [...(definition?.fields ?? []), ...(definition?.secrets ?? [])];
  const values: Record<string, string> = {};
  const config = (provider?.config ?? {}) as Record<string, unknown>;

  for (const field of fields) {
    if (field.type === "switch") continue;
    if ((definition?.secrets ?? []).includes(field)) {
      values[field.key] = ""; // never echo a stored secret back
      continue;
    }
    const current = config[field.key];
    if (field.type === "json") {
      values[field.key] = current ? JSON.stringify(current, null, 0) : "";
    } else {
      values[field.key] =
        current === undefined || current === null
          ? String(field.default ?? "")
          : String(current);
    }
  }

  return {
    id: provider?.id,
    kind,
    name: provider?.name ?? definition?.label ?? kind,
    enabled: provider?.enabled ?? false,
    default_role_key: provider?.default_role_key ?? "developer",
    auto_create_users: provider?.auto_create_users ?? true,
    auto_join: provider?.auto_join ?? true,
    domains: (provider?.allowed_domains ?? []).join(", "),
    values,
    hasSecret: provider?.has_secret ?? false,
    status: provider?.status ?? "unconfigured",
  };
}

export function SsoPanel({
  org,
  providers,
  schema,
  roles,
  can,
  reload,
}: {
  org: Organization;
  providers: IdentityProvider[];
  schema: SsoSchema | null;
  roles: OrgRole[];
  can: (permission: string) => boolean;
  reload: () => Promise<void>;
}) {
  const { t, locale } = useI18n();
  const { push } = useToast();
  const manage = can("sso:manage");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [probing, setProbing] = useState("");

  const probe = async (provider: IdentityProvider) => {
    setProbing(provider.id);
    try {
      const result = await api.probeSso(org.id, provider.id);
      push(result.ok ? t("sso.probeOk") : `${t("sso.probeFail")} · ${result.message ?? ""}`, result.ok ? "ok" : "err");
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setProbing("");
    }
  };

  const save = async () => {
    if (!draft) return;
    const definition = kindSchema(schema, draft.kind);
    if (!definition) return;

    const config: Record<string, unknown> = {};
    const secret: Record<string, unknown> = {};
    // JSON common fields (group→role mapping) are config values too, so they are
    // parsed alongside the per-kind fields instead of being a special case.
    const jsonCommon = (schema?.common ?? []).filter((field) => field.type === "json");
    for (const field of [...definition.fields, ...jsonCommon]) {
      const raw = (draft.values[field.key] ?? "").trim();
      if (field.type === "json") {
        if (!raw) continue;
        try {
          config[field.key] = JSON.parse(raw);
        } catch {
          push(t("sso.invalidJson", { field: field.key }), "err");
          return;
        }
      } else if (raw) {
        config[field.key] = raw;
      }
    }
    for (const field of definition.secrets) {
      const raw = (draft.values[field.key] ?? "").trim();
      if (raw) secret[field.key] = raw;
    }
    const payload = {
      kind: draft.kind,
      name: draft.name,
      enabled: draft.enabled,
      config,
      secret,
      default_role_key: draft.default_role_key,
      auto_create_users: draft.auto_create_users,
      auto_join: draft.auto_join,
      allowed_domains: draft.domains
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    };

    setBusy(true);
    try {
      if (draft.id) await api.updateSso(org.id, draft.id, payload);
      else await api.createSso(org.id, payload);
      push(t("sso.saved"), "ok");
      setDraft(null);
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (provider: IdentityProvider) => {
    setBusy(true);
    try {
      await api.updateSso(org.id, provider.id, {
        name: provider.name,
        enabled: !provider.enabled,
        config: provider.config,
        default_role_key: provider.default_role_key,
        auto_create_users: provider.auto_create_users,
        auto_join: provider.auto_join,
        allowed_domains: provider.allowed_domains,
      });
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (provider: IdentityProvider) => {
    if (!window.confirm(t("sso.deleteConfirm", { name: provider.name }))) return;
    setBusy(true);
    try {
      await api.removeSso(org.id, provider.id);
      push(t("sso.deleted"), "ok");
      await reload();
    } catch (err) {
      push(errorMessage(err), "err");
    } finally {
      setBusy(false);
    }
  };

  const definition = draft ? kindSchema(schema, draft.kind) : undefined;
  const callbackBase = `${API_BASE}/auth`;

  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="card">
        <div className="card-head">
          <Icon name="lock" size={15} />
          <span className="card-title">{t("sso.title")}</span>
          <span className="count-badge">{providers.length}</span>
          <span className="spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            {t("sso.subtitle")}
          </span>
          {manage && schema ? (
            <button
              className="btn btn-sm"
              onClick={() => setDraft(draftFrom(null, schema.kinds[0]?.kind ?? "oidc", schema))}
            >
              <Icon name="plus" size={13} />
              {t("sso.add")}
            </button>
          ) : null}
        </div>

        {providers.length === 0 ? (
          <Empty icon="lock" title={t("sso.none")} hint={t("sso.noneHint")} />
        ) : (
          <div className="sso-list">
            {providers.map((provider) => {
              const state = ssoStatus(provider.status);
              const callback = `${callbackBase}/${provider.key}/callback`;
              return (
                <div key={provider.id} className="sso-row">
                  <span className={`sso-icon kind-${provider.kind}`}>
                    <Icon name={KIND_ICON[provider.kind] ?? "shield"} size={16} />
                  </span>

                  <div className="sso-main">
                    <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                      <b>{provider.name}</b>
                      <span className="chip mono">{provider.kind}</span>
                      <Pill tone={state.tone}>{t(state.key)}</Pill>
                      {provider.enabled ? null : <Pill tone="idle">{t("sso.disabled")}</Pill>}
                      {provider.missing.length ? (
                        <span className="sso-missing" title={provider.missing.join(", ")}>
                          {t("sso.missing", { fields: provider.missing.join(", ") })}
                        </span>
                      ) : null}
                    </div>
                    <div className="sso-callback">
                      <span className="muted">{t("sso.callback")}</span>
                      <code className="mono truncate">{callback}</code>
                      <CopyButton value={callback} />
                    </div>
                    <div className="muted" style={{ fontSize: 11 }}>
                      {t("sso.defaultRole")}: <b>{provider.default_role_key}</b>
                      {provider.last_check_at
                        ? ` · ${t("sso.probe")} ${relativeTime(provider.last_check_at, locale)}`
                        : ""}
                      {provider.last_error ? ` · ${provider.last_error}` : ""}
                    </div>
                  </div>

                  <div className="sso-actions">
                    {manage ? (
                      <>
                        <button
                          className="btn btn-sm"
                          onClick={() => probe(provider)}
                          disabled={probing === provider.id || busy}
                        >
                          <Icon name="pulse" size={13} />
                          {probing === provider.id ? t("sso.probing") : t("sso.probe")}
                        </button>
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => setDraft(draftFrom(provider, provider.kind, schema))}
                          disabled={busy}
                        >
                          {t("role.edit")}
                        </button>
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => toggle(provider)}
                          disabled={busy}
                        >
                          {provider.enabled ? t("common.disable") : t("common.enable")}
                        </button>
                        <button
                          className="icon-btn"
                          title={t("common.delete")}
                          onClick={() => remove(provider)}
                          disabled={busy}
                        >
                          <Icon name="trash" size={13} />
                        </button>
                      </>
                    ) : (
                      <Pill tone="idle">{t("acc.readonly")}</Pill>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {draft && definition ? (
        <SsoEditor
          draft={draft}
          definition={definition}
          schema={schema}
          roles={roles}
          busy={busy}
          onChange={setDraft}
          onCancel={() => setDraft(null)}
          onSave={save}
        />
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ editor */

function SsoEditor({
  draft,
  definition,
  schema,
  roles,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  draft: Draft;
  definition: SsoKindSchema;
  schema: SsoSchema | null;
  roles: OrgRole[];
  busy: boolean;
  onChange: (next: Draft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const { t, locale } = useI18n();
  const common = schema?.common ?? [];

  const set = (key: string, value: string) =>
    onChange({ ...draft, values: { ...draft.values, [key]: value } });

  return (
    <div className="card">
      <div className="card-head">
        <Icon name={KIND_ICON[draft.kind] ?? "shield"} size={15} />
        <span className="card-title">
          {draft.id ? `${t("role.edit")} · ${draft.name}` : t("sso.add")}
        </span>
        <span className="spacer" />
        {definition.docs ? (
          <a className="link" href={definition.docs} target="_blank" rel="noreferrer">
            {t("sso.docs")}
            <Icon name="external" size={12} />
          </a>
        ) : null}
      </div>

      <div className="card-body stack" style={{ gap: 14 }}>
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          {locale === "zh" ? definition.blurb : definition.blurb_en || definition.blurb}
        </p>

        <div className="access-form-row">
          <div className="field" style={{ flex: "0 1 170px" }}>
            <label>{t("sso.kind")}</label>
            <select
              className="select"
              value={draft.kind}
              disabled={Boolean(draft.id)}
              onChange={(event) =>
                onChange({ ...draftFrom(null, event.target.value, schema), id: draft.id })
              }
            >
              {(schema?.kinds ?? []).map((item) => (
                <option key={item.kind} value={item.kind}>
                  {locale === "zh" ? item.label_zh ?? item.label : item.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ flex: "1 1 220px" }}>
            <label>{t("sso.name")}</label>
            <input
              className="input"
              value={draft.name}
              onChange={(event) => onChange({ ...draft, name: event.target.value })}
            />
          </div>
          <div className="field" style={{ flex: "0 1 170px" }}>
            <label>{t("sso.defaultRole")}</label>
            <select
              className="select"
              value={draft.default_role_key}
              onChange={(event) => onChange({ ...draft, default_role_key: event.target.value })}
            >
              {roles.map((role) => (
                <option key={role.key} value={role.key}>
                  {role.name}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="sso-fields">
          {definition.fields.map((field) => (
            <Field
              key={field.key}
              field={field}
              value={draft.values[field.key] ?? ""}
              onChange={(value) => set(field.key, value)}
            />
          ))}
        </div>

        {definition.secrets.length ? (
          <div className="sso-fields">
            {definition.secrets.map((field) => (
              <Field
                key={field.key}
                field={{ ...field, type: "password" }}
                value={draft.values[field.key] ?? ""}
                onChange={(value) => set(field.key, value)}
                hint={draft.hasSecret ? t("sso.keepSecret") : undefined}
              />
            ))}
          </div>
        ) : null}

        <div className="sso-fields">
          {common.map((field) => {
            if (field.key === "name" || field.key === "default_role_key") return null;
            if (field.type === "switch") {
              return (
                <label key={field.key} className="sso-switch">
                  <input
                    type="checkbox"
                    checked={Boolean(
                      field.key === "auto_create_users"
                        ? draft.auto_create_users
                        : field.key === "auto_join"
                          ? draft.auto_join
                          : draft.values[field.key] === "true",
                    )}
                    onChange={(event) =>
                      field.key === "auto_create_users"
                        ? onChange({ ...draft, auto_create_users: event.target.checked })
                        : field.key === "auto_join"
                          ? onChange({ ...draft, auto_join: event.target.checked })
                          : set(field.key, String(event.target.checked))
                    }
                  />
                  <span>
                    <b>{locale === "zh" ? field.label_zh ?? field.label : field.label}</b>
                    <span className="muted">{locale === "zh" ? field.help : field.help_en}</span>
                  </span>
                </label>
              );
            }
            if (field.type === "list") {
              return (
                <div key={field.key} className="field">
                  <label>{locale === "zh" ? field.label_zh ?? field.label : field.label}</label>
                  <input
                    className="input"
                    value={draft.domains}
                    placeholder="example.com, corp.example.com"
                    onChange={(event) => onChange({ ...draft, domains: event.target.value })}
                  />
                  <span className="hint">{t("sso.domainsHint")}</span>
                </div>
              );
            }
            return (
              <Field
                key={field.key}
                field={field}
                value={draft.values[field.key] ?? ""}
                onChange={(value) => set(field.key, value)}
              />
            );
          })}
        </div>

        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn btn-ghost" onClick={onCancel} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-primary" onClick={onSave} disabled={busy}>
            {t("common.save")}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  field,
  value,
  onChange,
  hint,
}: {
  field: SsoField;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
}) {
  const { t, locale } = useI18n();
  const label = locale === "zh" ? field.label_zh ?? field.label : field.label;
  const help = locale === "zh" ? field.help : field.help_en ?? field.help;

  if (field.type === "select") {
    return (
      <div className="field">
        <label>
          {label}
          {field.required ? <span className="req">*</span> : null}
        </label>
        <select className="select" value={value} onChange={(event) => onChange(event.target.value)}>
          {(field.options ?? []).map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        {help ? <span className="hint">{help}</span> : null}
      </div>
    );
  }

  if (field.type === "json") {
    return (
      <div className="field" style={{ gridColumn: "1 / -1" }}>
        <label>{label}</label>
        <textarea
          className="textarea mono"
          rows={3}
          value={value}
          placeholder={field.placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
        <span className="hint">{help ?? t("sso.roleMapHint")}</span>
      </div>
    );
  }

  return (
    <div className="field">
      <label>
        {label}
        {field.required ? <span className="req">*</span> : null}
      </label>
      <input
        className="input"
        type={field.type === "password" ? "password" : "text"}
        value={value}
        placeholder={field.placeholder}
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
      />
      {hint ?? help ? <span className="hint">{hint ?? help}</span> : null}
    </div>
  );
}
