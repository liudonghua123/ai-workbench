"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Icon } from "@/components/Icon";
import { LogViewer } from "@/components/LogViewer";
import { CopyButton, Empty, KV, LoadingRows, Pill, StatusPill, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import {
  DEPLOY_STATUS,
  DOMAIN_STATUS,
  RESOURCE_LABEL,
  cx,
  fmtBytes,
  relativeTime,
} from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Deployment, EnvVar, Project, SqlMode } from "@/lib/types";

const ACTIVE_DEPLOY = ["queued", "building", "deploying"];

/* ==========================================================================
   overview
   ========================================================================== */

export function OverviewTab({
  project,
  deployments,
  env,
  onDeploy,
  busy,
}: {
  project: Project;
  deployments: Deployment[];
  env: EnvVar[];
  onDeploy: () => void;
  busy: boolean;
}) {
  const { push } = useToast();
  const { t, locale } = useI18n();
  const [domains, setDomains] = useState(project.domains);

  const verify = async (domainId: string) => {
    try {
      const updated = await api.verifyDomain(project.id, domainId);
      setDomains((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      push(
        updated.status === "active"
          ? t("set.verifyOk", { fqdn: updated.fqdn })
          : t("set.verifyPending", { fqdn: updated.fqdn }),
        updated.status === "active" ? "ok" : "err",
      );
    } catch (err) {
      push(err instanceof Error ? err.message : t("set.verifyFailed"), "err");
    }
  };

  return (
    <div
      className="split"
      style={{ "--split": "minmax(0,1.35fr) minmax(0,1fr)" } as CSSProperties}
    >
      <div className="stack" style={{ gap: 16 }}>
        <section className="card">
          <div className="card-head">
            <Icon name="route" size={14} />
            <span className="card-title">{t("ov.runtime")}</span>
            <div className="spacer" />
            <Pill tone="idle">{project.runtime || "static"}</Pill>
          </div>
          <div className="card-body">
            <KV
              items={[
                {
                  k: t("ov.url"),
                  v: (
                    <a className="link mono" href={project.url} target="_blank" rel="noreferrer">
                      {project.url}
                    </a>
                  ),
                },
                {
                  k: t("ov.previewUrl"),
                  v: project.preview.url ? (
                    <a
                      className="link mono"
                      href={project.preview.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {project.preview.url}
                    </a>
                  ) : (
                    "—"
                  ),
                },
                { k: t("ov.port"), v: String(project.port ?? "—") },
                { k: t("ov.runCommand"), v: project.run_command || "—" },
                { k: t("ov.template"), v: `${project.template_key} @ ${project.template_version}` },
                { k: t("ov.workspace"), v: project.repo_path },
                { k: t("ov.updated"), v: relativeTime(project.updated_at, locale) },
              ]}
            />
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="cloud" size={14} />
            <span className="card-title">{t("ov.resources")}</span>
            <div className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              {t("ov.resourcesHint")}
            </span>
          </div>
          {project.resources.length === 0 ? (
            <Empty icon="cloud" title={t("ov.noResources")} hint={t("ov.noResourcesHint")} />
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>{t("ov.kind")}</th>
                    <th>{t("ov.name")}</th>
                    <th>Provider</th>
                    <th>{t("ov.dsn")}</th>
                    <th>{t("ov.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {project.resources.map((resource) => {
                    const labelKey = RESOURCE_LABEL[resource.kind];
                    return (
                      <tr key={resource.id}>
                        <td>
                          <span className="row" style={{ gap: 6, whiteSpace: "nowrap" }}>
                            <Icon
                              name={resource.kind === "storage" ? "cloud" : "database"}
                              size={13}
                            />
                            {labelKey ? t(labelKey) : resource.kind}
                          </span>
                        </td>
                        <td className="mono" style={{ whiteSpace: "nowrap" }}>
                          {resource.name}
                        </td>
                        <td>
                          <span className="tag">{resource.provider}</span>
                        </td>
                        <td
                          className="mono truncate"
                          style={{ maxWidth: 260 }}
                          title={resource.endpoint}
                        >
                          {String(
                            (resource.config?.dsn as string) ??
                              (resource.config?.path as string) ??
                              resource.endpoint,
                          )}
                        </td>
                        <td>
                          <Pill tone="ok">{resource.status}</Pill>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="terminal" size={14} />
            <span className="card-title">{t("ov.recentDeploys")}</span>
            <div className="spacer" />
            <button className="btn btn-sm" onClick={onDeploy} disabled={busy}>
              <Icon name="rocket" size={12} /> {t("ov.redeploy")}
            </button>
          </div>
          <div className="card-body" style={{ paddingTop: 6, paddingBottom: 6 }}>
            {deployments.length === 0 ? (
              <Empty icon="rocket" title={t("ov.noDeploys")} hint={t("ov.noDeploysHint")} />
            ) : (
              deployments.slice(0, 4).map((deployment) => (
                <div className="event-row" key={deployment.id}>
                  <span
                    className="event-dot"
                    style={{
                      background: deployment.status === "failed" ? "var(--err)" : "var(--ok)",
                    }}
                  />
                  <span style={{ minWidth: 0 }}>
                    <span style={{ display: "block" }}>
                      <b className="mono">{deployment.version}</b>{" "}
                      <span className="muted">{deployment.environment}</span>
                    </span>
                    <span className="mono muted" style={{ fontSize: 10.5 }}>
                      {deployment.url || "—"}
                    </span>
                  </span>
                  <span className="row" style={{ marginLeft: "auto", gap: 8 }}>
                    <StatusPill map={DEPLOY_STATUS} value={deployment.status} />
                    <span className="event-time">
                      {relativeTime(deployment.finished_at ?? deployment.started_at, locale)}
                    </span>
                  </span>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      <div className="stack" style={{ gap: 16 }}>
        <section className="card">
          <div className="card-head">
            <Icon name="globe" size={14} />
            <span className="card-title">{t("ov.domains")}</span>
            <div className="spacer" />
            <span className="tag">*.{project.domains[0]?.suffix ?? "localtest.me"}</span>
          </div>
          <div className="card-body" style={{ paddingTop: 4, paddingBottom: 4 }}>
            {domains.length === 0 ? (
              <Empty icon="globe" title={t("ov.noDomains")} />
            ) : (
              domains.map((domain) => (
                <div className="event-row" key={domain.id}>
                  <span
                    className="event-dot"
                    style={{
                      background: domain.status === "active" ? "var(--ok)" : "var(--warn)",
                    }}
                  />
                  <span style={{ minWidth: 0 }}>
                    <span className="mono" style={{ display: "block", fontSize: 12 }}>
                      {domain.fqdn}
                    </span>
                    <span className="muted mono" style={{ fontSize: 10.5 }}>
                      → {domain.target}
                      {domain.is_primary ? ` · ${t("ov.primary")}` : ""}
                    </span>
                  </span>
                  <span className="row" style={{ marginLeft: "auto", gap: 6 }}>
                    <StatusPill map={DOMAIN_STATUS} value={domain.status} />
                    <button
                      className="icon-btn"
                      title={t("ov.verify")}
                      aria-label={t("ov.verify")}
                      onClick={() => verify(domain.id)}
                    >
                      <Icon name="refresh" size={13} />
                    </button>
                  </span>
                </div>
              ))
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="shield" size={14} />
            <span className="card-title">{t("ov.env")}</span>
            <div className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              {t("ov.envCount", { n: env.length })}
            </span>
          </div>
          <div className="card-body" style={{ paddingTop: 8 }}>
            {env.length === 0 ? (
              <Empty icon="shield" title={t("ov.noEnv")} />
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {env.slice(0, 6).map((item) => (
                  <div key={item.id} className="row-between">
                    <span className="mono" style={{ fontSize: 11.5 }}>
                      {item.key}
                    </span>
                    <span className="mono muted truncate" style={{ fontSize: 11, maxWidth: 180 }}>
                      {item.value}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        <section className="card card-pad">
          <div className="row" style={{ gap: 8, marginBottom: 8 }}>
            <Icon name="plug" size={14} />
            <span className="card-title">{t("ov.agentTitle")}</span>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "0 0 10px", lineHeight: 1.6 }}>
            {t("ov.agentDesc")}
          </p>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            <span className="tag">write_project_file</span>
            <span className="tag">set_env_var</span>
            <span className="tag">deploy_project</span>
          </div>
        </section>
      </div>
    </div>
  );
}

/* ==========================================================================
   deployments
   ========================================================================== */

export function DeployTab({
  project,
  deployments,
  onRefresh,
  onDeploy,
  busy,
}: {
  project: Project;
  deployments: Deployment[];
  onRefresh: () => Promise<void> | void;
  onDeploy: () => void;
  busy: boolean;
}) {
  const { push } = useToast();
  const { t, locale } = useI18n();
  const [selected, setSelected] = useState<string | null>(deployments[0]?.id ?? null);
  const [detail, setDetail] = useState<Deployment | null>(null);
  const [streaming, setStreaming] = useState(false);
  const refreshRef = useRef(onRefresh);

  useEffect(() => {
    refreshRef.current = onRefresh;
  }, [onRefresh]);

  useEffect(() => {
    if (!selected && deployments[0]) setSelected(deployments[0].id);
  }, [deployments, selected]);

  const loadDetail = useCallback(async () => {
    if (!selected) return null;
    try {
      const result = await api.deployment(selected);
      setDetail(result);
      return result;
    } catch {
      return null;
    }
  }, [selected]);

  // One snapshot, then live deltas over SSE — no polling interval to tune or leak.
  useEffect(() => {
    setDetail(null);
    setStreaming(false);
    let cancelled = false;
    loadDetail().then((result) => {
      if (!cancelled && result && ACTIVE_DEPLOY.includes(result.status)) setStreaming(true);
    });
    return () => {
      cancelled = true;
    };
  }, [loadDetail]);

  useEffect(() => {
    if (!selected || !streaming) return;
    const source = new EventSource(api.deploymentStreamUrl(selected));
    // the stream replays from line 0, so restart the buffer instead of appending twice
    setDetail((prev) => (prev ? { ...prev, log: "" } : prev));

    source.addEventListener("log", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { lines: string[] };
      setDetail((prev) =>
        prev ? { ...prev, log: `${prev.log ?? ""}${payload.lines.join("\n")}\n` } : prev,
      );
    });
    source.addEventListener("status", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as Partial<Deployment>;
      setDetail((prev) => (prev ? { ...prev, ...payload } : prev));
    });
    const finish = () => {
      source.close();
      setStreaming(false);
      refreshRef.current();
    };
    source.addEventListener("done", finish);
    source.addEventListener("timeout", finish);
    source.onerror = () => {
      source.close();
      setStreaming(false);
    };
    return () => source.close();
  }, [selected, streaming]);

  const selectedMeta = deployments.find((item) => item.id === selected) ?? detail;

  const stop = async () => {
    if (!selected) return;
    try {
      await api.stopDeployment(selected);
      push(t("dep.stopped"));
      await loadDetail();
      await onRefresh();
    } catch (err) {
      push(err instanceof Error ? err.message : t("dep.stopFailed"), "err");
    }
  };

  // Nothing has shipped yet: one clear empty state, instead of a detail card that
  // renders a lone dash beside a blank log and reads like a failed load.
  if (deployments.length === 0) {
    return (
      <section className="card">
        <div className="card-head">
          <Icon name="rocket" size={14} />
          <span className="card-title">{t("dep.history")}</span>
          <div className="spacer" />
          <button className="btn btn-sm btn-primary" onClick={onDeploy} disabled={busy}>
            <Icon name="plus" size={12} /> {t("detail.deploy")}
          </button>
        </div>
        <Empty icon="rocket" title={t("dep.empty")} hint={t("dep.emptyHint")} />
        <div className="card-pad" style={{ borderTop: "1px solid var(--line)" }}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span className="muted" style={{ fontSize: 11.5 }}>
              {t("dep.appUrl")}
            </span>
            <a className="link mono" href={project.url} target="_blank" rel="noreferrer">
              {project.url}
            </a>
          </div>
        </div>
      </section>
    );
  }

  return (
    <div
      className="split"
      style={{ "--split": "340px minmax(0,1fr)" } as CSSProperties}
    >
      <section className="card">
        <div className="card-head">
          <Icon name="rocket" size={14} />
          <span className="card-title">{t("dep.history")}</span>
          <div className="spacer" />
          <button className="btn btn-sm btn-primary" onClick={onDeploy} disabled={busy}>
            <Icon name="plus" size={12} /> {t("detail.deploy")}
          </button>
        </div>
        <div className="card-body" style={{ padding: 8 }}>
          {deployments.length === 0 ? (
            <Empty icon="rocket" title={t("dep.empty")} hint={t("dep.emptyHint")} />
          ) : (
            <div className="stack" style={{ gap: 4 }}>
              {deployments.map((deployment) => (
                <button
                  key={deployment.id}
                  onClick={() => setSelected(deployment.id)}
                  className={cx("agent-item", selected === deployment.id && "active")}
                  style={{ border: "1px solid transparent", textAlign: "left", width: "100%" }}
                >
                  <span
                    className="event-dot"
                    style={{
                      marginTop: 8,
                      background:
                        deployment.status === "running"
                          ? "var(--ok)"
                          : deployment.status === "failed"
                            ? "var(--err)"
                            : "var(--warn)",
                    }}
                  />
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span className="row" style={{ gap: 6 }}>
                      <b className="mono" style={{ fontSize: 12 }}>
                        {deployment.version}
                      </b>
                      <StatusPill map={DEPLOY_STATUS} value={deployment.status} />
                    </span>
                    <span className="muted" style={{ fontSize: 10.5, display: "block" }}>
                      {relativeTime(deployment.finished_at ?? deployment.started_at, locale)} ·{" "}
                      {deployment.environment}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="stack" style={{ gap: 12 }}>
        <div className="card card-pad">
          <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
            <div style={{ minWidth: 0 }}>
              <div className="row" style={{ gap: 8 }}>
                <b className="mono">{selectedMeta?.version ?? "—"}</b>
                {selectedMeta ? (
                  <StatusPill map={DEPLOY_STATUS} value={selectedMeta.status} />
                ) : null}
                {selectedMeta?.provider ? <span className="tag">{selectedMeta.provider}</span> : null}
              </div>
              <div className="muted mono" style={{ fontSize: 11.5, marginTop: 4 }}>
                {selectedMeta?.url || project.url}
              </div>
            </div>
            <div className="spacer" />
            <button className="btn btn-sm" onClick={loadDetail} disabled={!selected}>
              <Icon name="refresh" size={12} /> {t("dep.refresh")}
            </button>
            <button className="btn btn-sm btn-danger" onClick={stop} disabled={!streaming}>
              <Icon name="stop" size={12} /> {t("dep.stop")}
            </button>
          </div>
          {selectedMeta?.error ? (
            <div
              className="pill pill-err"
              style={{ marginTop: 10, height: "auto", padding: "6px 9px" }}
            >
              {selectedMeta.error}
            </div>
          ) : null}
        </div>

        <LogViewer
          title={`deploy · ${selectedMeta?.version ?? "—"}`}
          log={detail?.log ?? ""}
          live={streaming}
          empty={selected ? t("dep.waiting") : t("dep.pick")}
        />
      </section>
    </div>
  );
}

/* ==========================================================================
   data
   ========================================================================== */

export function DataTab({ project }: { project: Project }) {
  const { push } = useToast();
  const { t } = useI18n();
  const [sql, setSql] = useState("SELECT name FROM sqlite_master WHERE type='table'");
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [objects, setObjects] = useState<{ key: string; size: number }[] | null>(null);
  const [bucket, setBucket] = useState("");
  const [mode, setMode] = useState<SqlMode | null>(null);

  const hasDb = project.resources.some((resource) => resource.kind === "database");
  const hasStorage = project.resources.some((resource) => resource.kind === "storage");

  useEffect(() => {
    api
      .sqlMode()
      .then(setMode)
      .catch(() => setMode(null));
  }, []);

  useEffect(() => {
    if (!hasStorage) return;
    api
      .objects(project.id)
      .then((data) => {
        setObjects(data.objects);
        setBucket(data.bucket);
      })
      .catch(() => setObjects([]));
  }, [hasStorage, project.id]);

  const run = async () => {
    setBusy(true);
    try {
      const result = await api.dbQuery(project.id, sql);
      setRows(result.rows);
      push(`${result.row_count} · ${result.database}`);
    } catch (err) {
      push(err instanceof Error ? err.message : t("data.noRows"), "err");
      setRows([]);
    } finally {
      setBusy(false);
    }
  };

  const columns = rows && rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div
      className="split"
      style={{ "--split": "minmax(0,1.4fr) minmax(0,1fr)" } as CSSProperties}
    >
      <section className="card">
        <div className="card-head">
          <Icon name="database" size={14} />
          <span className="card-title">{t("data.console")}</span>
          {mode ? (
            <Pill tone={mode.readonly ? "warn" : "err"}>
              {mode.readonly ? t("data.readonly") : t("sys.sqlWrite")}
            </Pill>
          ) : null}
          <div className="spacer" />
          <span className="tag">
            {project.resources.find((resource) => resource.kind === "database")?.name ??
              t("data.noDb")}
          </span>
        </div>
        <div className="card-body stack" style={{ gap: 10 }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {["SELECT name FROM sqlite_master WHERE type='table'", "SELECT * FROM _meta"].map(
              (sample) => (
                <button
                  key={sample}
                  className="btn btn-sm btn-ghost"
                  onClick={() => setSql(sample)}
                >
                  {sample.length > 34 ? `${sample.slice(0, 33)}…` : sample}
                </button>
              ),
            )}
          </div>
          <textarea
            className="textarea mono"
            rows={4}
            value={sql}
            onChange={(event) => setSql(event.target.value)}
            spellCheck={false}
          />
          <div className="row">
            <button className="btn btn-primary btn-sm" onClick={run} disabled={busy || !hasDb}>
              <Icon name="play" size={12} />{" "}
              {busy ? t("data.executing") : t("data.execute")}
            </button>
            <span className="muted" style={{ fontSize: 11.5 }}>
              {hasDb ? t("data.providerNote") : t("data.noDbProject")}
            </span>
          </div>

          {rows === null ? null : rows.length === 0 ? (
            <Empty icon="database" title={t("data.noRows")} />
          ) : (
            <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 10 }}>
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      {columns.map((column) => (
                        <th key={column}>{column}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, index) => (
                      <tr key={index}>
                        {columns.map((column) => (
                          <td key={column} className="mono">
                            {String(row[column] ?? "null")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <Icon name="cloud" size={14} />
          <span className="card-title">{t("data.storage")}</span>
          <div className="spacer" />
          <span className="tag">{bucket || "—"}</span>
        </div>
        <div className="card-body" style={{ paddingTop: 6, paddingBottom: 6 }}>
          {objects === null ? (
            <LoadingRows rows={3} />
          ) : objects.length === 0 ? (
            <Empty
              icon="cloud"
              title={t("data.emptyBucket")}
              hint={t("data.emptyBucketHint")}
            />
          ) : (
            objects.map((object) => (
              <div className="event-row" key={object.key}>
                <span className="event-dot" style={{ background: "var(--accent)" }} />
                <span className="mono truncate" style={{ fontSize: 11.5 }}>
                  {object.key}
                </span>
                <span className="event-time">{fmtBytes(object.size)}</span>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

/* ==========================================================================
   settings
   ========================================================================== */

export function SettingsTab({
  project,
  env,
  onEnvChange,
  onDelete,
  onRefresh,
}: {
  project: Project;
  env: EnvVar[];
  onEnvChange: (next: EnvVar[]) => void;
  onDelete: () => void;
  onRefresh: () => Promise<void> | void;
}) {
  const { push } = useToast();
  const { t, locale } = useI18n();
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [secret, setSecret] = useState(false);
  const [subdomain, setSubdomain] = useState("");

  const addEnv = async () => {
    if (!key.trim()) return;
    try {
      const created = await api.upsertEnv(project.id, key.trim(), value, secret);
      onEnvChange([...env.filter((item) => item.key !== created.key), created]);
      setKey("");
      setValue("");
      setSecret(false);
      push(t("set.envSaved"));
    } catch (err) {
      push(err instanceof Error ? err.message : t("set.saveFailed"), "err");
    }
  };

  const addDomain = async () => {
    if (!subdomain.trim()) return;
    try {
      await api.addDomain(project.id, subdomain.trim());
      setSubdomain("");
      await onRefresh();
      push(t("set.bound"));
    } catch (err) {
      push(err instanceof Error ? err.message : t("set.bindFailed"), "err");
    }
  };

  return (
    <div
      className="split"
      style={{ "--split": "minmax(0,1.3fr) minmax(0,1fr)" } as CSSProperties}
    >
      <section className="card">
        <div className="card-head">
          <Icon name="shield" size={14} />
          <span className="card-title">{t("set.env")}</span>
          <div className="spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            {t("set.envHint")}
          </span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("set.key")}</th>
                <th>{t("set.value")}</th>
                <th style={{ width: 70 }}>{t("set.kind")}</th>
              </tr>
            </thead>
            <tbody>
              {env.map((item) => (
                <tr key={item.id}>
                  <td className="mono">{item.key}</td>
                  <td className="mono truncate" style={{ maxWidth: 300 }} title={item.value}>
                    {item.value}
                  </td>
                  <td>
                    {item.is_secret ? (
                      <Pill tone="warn">{t("set.secret")}</Pill>
                    ) : (
                      <Pill tone="idle">{t("set.plain")}</Pill>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card-body" style={{ borderTop: "1px solid var(--line)" }}>
          <div className="grid grid-2" style={{ gap: 10 }}>
            <div className="field">
              <label>{t("set.key")}</label>
              <input
                className="input mono"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder="FEATURE_FLAG"
              />
            </div>
            <div className="field">
              <label>{t("set.value")}</label>
              <input
                className="input mono"
                value={value}
                onChange={(event) => setValue(event.target.value)}
                placeholder="true"
              />
            </div>
          </div>
          <div className="row" style={{ marginTop: 10, gap: 10 }}>
            <label className="row" style={{ gap: 6, fontSize: 12, cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={secret}
                onChange={(event) => setSecret(event.target.checked)}
                style={{ accentColor: "var(--accent)" }}
              />
              {t("set.markSecret")}
            </label>
            <div className="spacer" />
            <button className="btn btn-sm btn-primary" onClick={addEnv} disabled={!key.trim()}>
              <Icon name="plus" size={12} /> {t("set.saveVar")}
            </button>
          </div>
        </div>
      </section>

      <div className="stack" style={{ gap: 16 }}>
        <section className="card">
          <div className="card-head">
            <Icon name="globe" size={14} />
            <span className="card-title">{t("set.domains")}</span>
          </div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <div className="field">
              <label>{t("set.subdomain")}</label>
              <div className="row" style={{ gap: 6 }}>
                <input
                  className="input mono"
                  value={subdomain}
                  onChange={(event) => setSubdomain(event.target.value)}
                  placeholder={`${project.slug}-staging`}
                />
                <button className="btn btn-sm" onClick={addDomain} disabled={!subdomain.trim()}>
                  {t("set.bind")}
                </button>
              </div>
              <span className="hint">
                {t("set.defaultSuffix", {
                  suffix: project.domains[0]?.suffix ?? "localtest.me",
                })}
              </span>
            </div>
            {project.domains.map((domain) => (
              <div key={domain.id} className="row-between">
                <span className="mono" style={{ fontSize: 11.5 }}>
                  {domain.fqdn}
                </span>
                <StatusPill map={DOMAIN_STATUS} value={domain.status} />
              </div>
            ))}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="settings" size={14} />
            <span className="card-title">{t("set.identity")}</span>
          </div>
          <div className="card-body">
            <KV
              items={[
                { k: t("set.projectId"), v: project.id },
                { k: t("set.slug"), v: project.slug },
                { k: t("set.createdAt"), v: relativeTime(project.created_at, locale) },
                {
                  k: t("set.deleteCmd"),
                  v: (
                    <span className="row" style={{ gap: 4 }}>
                      <span className="truncate">rm -rf {project.repo_path}</span>
                      <CopyButton value={project.repo_path} />
                    </span>
                  ),
                },
              ]}
            />
          </div>
        </section>

        <section
          className="card card-pad"
          style={{ borderColor: "color-mix(in srgb, var(--err) 40%, var(--line))" }}
        >
          <div className="row" style={{ gap: 8, marginBottom: 6 }}>
            <Icon name="trash" size={14} style={{ color: "var(--err)" }} />
            <span className="card-title" style={{ color: "var(--err)" }}>
              {t("set.dangerTitle")}
            </span>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "0 0 12px", lineHeight: 1.6 }}>
            {t("set.dangerDesc")}
          </p>
          <button className="btn btn-sm btn-danger" onClick={onDelete}>
            <Icon name="trash" size={12} /> {t("set.deleteForever")}
          </button>
        </section>
      </div>
    </div>
  );
}
