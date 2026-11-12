"use client";

import { useCallback, useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { CopyButton, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { clearToken, getToken, setToken } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import type { HarnessReport, LlmStatus, LlmTest, Me, Settings } from "@/lib/types";

export default function SettingsPage() {
  const { t } = useI18n();
  const { push } = useToast();
  const [settings, setSettings] = useState<Settings | null>(null);
  const [llm, setLlm] = useState<LlmStatus | null>(null);
  const [harnesses, setHarnesses] = useState<HarnessReport | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [providers, setProviders] = useState<{
    active: Record<string, string>;
    available: Record<string, string[]>;
  } | null>(null);
  const [token, setTokenValue] = useState("");
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<LlmTest | null>(null);

  const load = useCallback(async () => {
    try {
      const [s, l, h, p] = await Promise.all([
        api.settings(),
        api.llm(),
        api.harnesses(),
        api.providers(),
      ]);
      setSettings(s);
      setLlm(l);
      setHarnesses(h);
      setProviders(p as unknown as { active: Record<string, string>; available: Record<string, string[]> });
      try {
        setMe(await api.me());
      } catch {
        setMe(null);
      }
    } catch (err) {
      push(err instanceof Error ? err.message : "failed", "err");
    }
  }, [push]);

  useEffect(() => {
    setTokenValue(getToken());
    load();
  }, [load]);

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      setTest(await api.testLlm());
    } catch (err) {
      setTest({ ok: false, configured: false, error: err instanceof Error ? err.message : "error" });
    } finally {
      setTesting(false);
    }
  };

  if (!settings) {
    return (
      <main className="content">
        <Skeleton height={40} />
        <div style={{ height: 12 }} />
        <Skeleton height={220} />
      </main>
    );
  }

  const llmOn = settings.agent_harness === "llm";
  const roleLabel = me?.principal
    ? me.principal.role === "admin"
      ? "admin"
      : me.principal.role
    : "—";

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("sys.title")}</h1>
          <p className="page-desc">{t("sys.desc")}</p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={load}>
          <Icon name="refresh" size={13} /> {t("common.refresh")}
        </button>
      </div>

      <div className="grid grid-2" style={{ alignItems: "start" }}>
        {/* ---------------------------------------------------------- identity */}
        <section className="card">
          <div className="card-head">
            <Icon name="shield" size={14} />
            <span className="card-title">{t("sys.identity")}</span>
            <div className="spacer" />
            <Pill tone={settings.auth_enabled ? "ok" : "idle"}>
              {settings.auth_enabled ? t("sys.authOn") : t("sys.authOff")}
            </Pill>
          </div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div style={{ fontSize: 12.5 }}>
              {me?.principal?.anonymous || !settings.auth_enabled
                ? t("sys.anonymous")
                : t("sys.signedIn", { name: me?.principal?.name ?? "—", role: roleLabel })}
            </div>
            <div className="field">
              <label htmlFor="sys-token">{t("sys.token")}</label>
              <input
                id="sys-token"
                className="input mono"
                type="password"
                autoComplete="off"
                placeholder={t("sys.tokenPlaceholder")}
                value={token}
                onChange={(event) => setTokenValue(event.target.value)}
              />
            </div>
            <div className="row" style={{ gap: 8 }}>
              <button
                className="btn btn-sm btn-accent"
                disabled={!token.trim()}
                onClick={() => {
                  setToken(token);
                  push(t("sys.tokenSaved"));
                  load();
                }}
              >
                <Icon name="save" size={12} /> {t("sys.tokenSave")}
              </button>
              <button
                className="btn btn-sm"
                onClick={() => {
                  clearToken();
                  setTokenValue("");
                  push(t("sys.tokenCleared"));
                  load();
                }}
              >
                {t("sys.tokenClear")}
              </button>
            </div>
            <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
              {t("sys.authHint")}
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------- agent runtime */}
        <section className="card">
          <div className="card-head">
            <Icon name="cpu" size={14} />
            <span className="card-title">{t("sys.agent")}</span>
            <div className="spacer" />
            <Pill tone={llmOn ? "accent" : "idle"}>
              {llmOn ? t("sys.harnessLlm") : t("sys.harnessLocal")}
            </Pill>
          </div>
          <div className="card-body stack" style={{ gap: 12 }}>
            <div className="kv">
              <span>{t("sys.model")}</span>
              <b className="mono">{llm?.model ?? "—"}</b>
            </div>
            <div className="kv">
              <span>{t("sys.endpoint")}</span>
              <code className="mono truncate" style={{ fontSize: 11 }}>
                {llm?.base_url ?? "—"}
              </code>
            </div>
            <div className="kv">
              <span>{t("sys.harness")}</span>
              <span className="row" style={{ gap: 6 }}>
                {(harnesses?.items ?? []).map((item) => (
                  <span
                    key={item.name}
                    className="tag"
                    title={item.kind}
                    style={
                      item.active
                        ? {
                            background: "var(--accent)",
                            borderColor: "var(--accent)",
                            color: "#fff",
                            fontWeight: 600,
                          }
                        : { opacity: 0.7 }
                    }
                  >
                    {item.name}
                  </span>
                ))}
              </span>
            </div>
            <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
              <button className="btn btn-sm btn-primary" onClick={runTest} disabled={testing}>
                <Icon name="route" size={12} />{" "}
                {testing ? t("sys.testing") : t("sys.test")}
              </button>
              {test ? (
                <span
                  className={`pill ${test.ok ? "pill-ok" : "pill-err"}`}
                  style={{ height: "auto", padding: "4px 9px", fontWeight: 500 }}
                >
                  {test.ok
                    ? t("sys.testOk", { reply: test.reply || "pong" })
                    : t("sys.testFail", { error: test.error ?? "failed" })}
                </span>
              ) : null}
            </div>
            <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
              {llm?.configured ? t("sys.llmReady") : t("sys.llmHint")}
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------------- sql */}
        <section className="card">
          <div className="card-head">
            <Icon name="database" size={14} />
            <span className="card-title">{t("sys.sql")}</span>
            <div className="spacer" />
            <Pill tone={settings.sql.readonly ? "warn" : "err"}>
              {settings.sql.readonly ? t("sys.sqlReadonly") : t("sys.sqlWrite")}
            </Pill>
          </div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
              {settings.sql.allowed_read.map((keyword) => (
                <span className="tag mono" key={keyword}>
                  {keyword}
                </span>
              ))}
              {settings.sql.allowed_write.map((keyword) => (
                <span className="tag mono" key={keyword} style={{ borderColor: "var(--err)" }}>
                  {keyword}
                </span>
              ))}
            </div>
            <div className="muted" style={{ fontSize: 11.5, lineHeight: 1.6 }}>
              {t("sys.sqlHint")}
            </div>
            <div className="muted" style={{ fontSize: 11 }}>
              {t("sys.sqlLimit", { n: settings.sql.row_limit })}
            </div>
          </div>
        </section>

        {/* -------------------------------------------------------- providers */}
        <section className="card">
          <div className="card-head">
            <Icon name="cloud" size={14} />
            <span className="card-title">{t("sys.providers")}</span>
          </div>
          <div className="card-body" style={{ paddingTop: 4 }}>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("sys.capability")}</th>
                    <th>{t("sys.active")}</th>
                    <th>{t("sys.registered")}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(providers?.active ?? settings.providers).map(([kind, active]) => (
                    <tr key={kind}>
                      <td className="mono">{kind}</td>
                      <td>
                        <span className="tag">{String(active)}</span>
                      </td>
                      <td className="muted mono" style={{ fontSize: 11 }}>
                        {(providers?.available?.[kind] ?? []).join(", ") || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>

      {/* ------------------------------------------------------------ env */}
      <section className="card card-pad" style={{ marginTop: 16 }}>
        <div className="row" style={{ gap: 8, marginBottom: 12 }}>
          <Icon name="settings" size={14} />
          <span className="card-title">{t("sys.env")}</span>
        </div>
        <div className="grid grid-3" style={{ gap: 14 }}>
          <Field label={t("sys.appName")} value={settings.app_name} />
          <Field label={t("sys.dataDir")} value={settings.data_dir} mono />
          <Field
            label={t("sys.ports")}
            value={`${settings.port_range[0]}–${settings.port_range[1]}`}
            mono
          />
          <Field label={t("sys.suffix")} value={settings.domain_suffix} mono />
          <Field label={t("sys.deployMode")} value={settings.deploy_mode} mono />
          <div className="stack" style={{ gap: 3 }}>
            <span className="muted" style={{ fontSize: 10.5, letterSpacing: "0.08em", textTransform: "uppercase" }}>
              {t("sys.mcp")}
            </span>
            <span className="row" style={{ gap: 6 }}>
              <code className="mono truncate" style={{ fontSize: 11.5 }}>
                {settings.mcp_path}
              </code>
              <CopyButton value={settings.mcp_path} label={t("sys.copy")} />
            </span>
          </div>
        </div>

        <div className="divider" style={{ margin: "16px 0 12px" }} />

        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <span className="muted" style={{ fontSize: 11 }}>
            {t("sys.features")}
          </span>
          <Feature on={llmOn} label={t("sys.feature.llm")} />
          <Feature on={!settings.sql.readonly} label={t("sys.feature.sqlWrite")} />
          <Feature on={settings.auth_enabled} label={t("sys.feature.auth")} />
          <Feature on={Boolean(settings.ide_url_template)} label={t("sys.feature.ide")} />
        </div>
      </section>
    </main>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="stack" style={{ gap: 3 }}>
      <span
        className="muted"
        style={{ fontSize: 10.5, letterSpacing: "0.08em", textTransform: "uppercase" }}
      >
        {label}
      </span>
      <span className={mono ? "mono truncate" : "truncate"} style={{ fontSize: 12 }}>
        {value || "—"}
      </span>
    </div>
  );
}

function Feature({ on, label }: { on: boolean; label: string }) {
  const { t } = useI18n();
  return (
    <span className={`pill ${on ? "pill-ok" : ""}`} style={{ height: "auto", padding: "3px 8px" }}>
      {label} · {on ? t("sys.on") : t("sys.off")}
    </span>
  );
}
