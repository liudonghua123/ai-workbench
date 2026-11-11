"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/Icon";
import { LogViewer } from "@/components/LogViewer";
import { CopyButton, Empty, KV, LoadingRows, Pill, StatusPill, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import {
  DEPLOY_STATUS,
  DOMAIN_STATUS,
  cx,
  fmtBytes,
  relativeTime,
} from "@/lib/format";
import type { Deployment, EnvVar, Project, TreeNode } from "@/lib/types";

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
  const [domains, setDomains] = useState(project.domains);

  const verify = async (domainId: string) => {
    try {
      const updated = await api.verifyDomain(project.id, domainId);
      setDomains((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      push(
        updated.status === "active" ? `${updated.fqdn} 解析已生效` : `${updated.fqdn} 仍在等待解析`,
        updated.status === "active" ? "ok" : "err",
      );
    } catch (err) {
      push(err instanceof Error ? err.message : "校验失败", "err");
    }
  };

  const lastDeploy = deployments[0];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.35fr) minmax(0,1fr)", gap: 16, alignItems: "start" }}>
      <div className="stack" style={{ gap: 16 }}>
        <section className="card">
          <div className="card-head">
            <Icon name="route" size={14} />
            <span className="card-title">运行时</span>
            <div className="spacer" />
            <Pill tone="idle">{project.runtime || "static"}</Pill>
          </div>
          <div className="card-body">
            <KV
              items={[
                { k: "访问地址", v: <a className="link" href={project.url} target="_blank" rel="noreferrer">{project.url}</a> },
                { k: "本地端口", v: String(project.port ?? "—") },
                { k: "启动命令", v: project.run_command || "—" },
                { k: "模板", v: `${project.template_key} @ ${project.template_version}` },
                { k: "工作区", v: project.repo_path },
                { k: "最后更新", v: relativeTime(project.updated_at) },
              ]}
            />
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="cloud" size={14} />
            <span className="card-title">云资源</span>
            <div className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              创建项目时按约定自动装配
            </span>
          </div>
          {project.resources.length === 0 ? (
            <Empty icon="cloud" title="没有装配资源" hint="该模板不需要额外云资源。" />
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>名称</th>
                  <th>Provider</th>
                  <th>端点 / DSN</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {project.resources.map((resource) => (
                  <tr key={resource.id}>
                    <td>
                      <span className="row" style={{ gap: 6, whiteSpace: "nowrap" }}>
                        <Icon name={resource.kind === "storage" ? "cloud" : "database"} size={13} />
                        {resource.kind === "storage" ? "对象存储" : "数据库"}
                      </span>
                    </td>
                    <td className="mono">{resource.name}</td>
                    <td>
                      <span className="tag">{resource.provider}</span>
                    </td>
                    <td className="mono truncate" style={{ maxWidth: 260 }} title={resource.endpoint}>
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
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="terminal" size={14} />
            <span className="card-title">最近部署</span>
            <div className="spacer" />
            <button className="btn btn-sm" onClick={onDeploy} disabled={busy}>
              <Icon name="rocket" size={12} /> 重新部署
            </button>
          </div>
          <div className="card-body" style={{ paddingTop: 6, paddingBottom: 6 }}>
            {deployments.length === 0 ? (
              <Empty icon="rocket" title="还没有部署记录" hint="点击部署会生成构建日志与健康检查结果。" />
            ) : (
              deployments.slice(0, 4).map((deployment) => (
                <div className="event-row" key={deployment.id}>
                  <span className="event-dot" style={{ background: deployment.status === "failed" ? "var(--err)" : "var(--ok)" }} />
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
                    <span className="event-time">{relativeTime(deployment.finished_at ?? deployment.started_at)}</span>
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
            <span className="card-title">域名绑定</span>
            <div className="spacer" />
            <span className="tag">*.{project.domains[0]?.suffix ?? "localtest.me"}</span>
          </div>
          <div className="card-body" style={{ paddingTop: 4, paddingBottom: 4 }}>
            {domains.length === 0 ? (
              <Empty icon="globe" title="未绑定域名" />
            ) : (
              domains.map((domain) => (
                <div className="event-row" key={domain.id}>
                  <span className="event-dot" style={{ background: domain.status === "active" ? "var(--ok)" : "var(--warn)" }} />
                  <span style={{ minWidth: 0 }}>
                    <span className="mono" style={{ display: "block", fontSize: 12 }}>
                      {domain.fqdn}
                    </span>
                    <span className="muted mono" style={{ fontSize: 10.5 }}>
                      → {domain.target}
                      {domain.is_primary ? " · 主域名" : ""}
                    </span>
                  </span>
                  <span className="row" style={{ marginLeft: "auto", gap: 6 }}>
                    <StatusPill map={DOMAIN_STATUS} value={domain.status} />
                    <button className="icon-btn" title="重新校验解析" onClick={() => verify(domain.id)}>
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
            <span className="card-title">环境变量</span>
            <div className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              {env.length} 项
            </span>
          </div>
          <div className="card-body" style={{ paddingTop: 8 }}>
            {env.length === 0 ? (
              <Empty icon="shield" title="暂无环境变量" />
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
            <span className="card-title">让 Agent 接手</span>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "0 0 10px", lineHeight: 1.6 }}>
            项目已注册到系统 MCP，Agent 可用同一套工具读取文件、改环境变量、触发部署。
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
   ide
   ========================================================================== */

export function IdeTab({
  project,
  ide,
}: {
  project: Project;
  ide: { vscode_url: string; available: boolean; repo_path: string; note: string } | null;
}) {
  const { push } = useToast();
  const [nodes, setNodes] = useState<TreeNode[] | null>(null);
  const [active, setActive] = useState("");
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    if (ide?.available) return;
    api
      .tree(project.id)
      .then((data) => {
        setNodes(data.nodes.filter((node) => node.type === "file"));
      })
      .catch((err) => push(err.message, "err"));
  }, [project.id, ide?.available, push]);

  const openFile = async (path: string) => {
    if (dirty && !window.confirm("当前文件有未保存修改，确定切换？")) return;
    try {
      const file = await api.readFile(project.id, path);
      setActive(path);
      setContent(file.content);
      setDirty(false);
    } catch (err) {
      push(err instanceof Error ? err.message : "读取失败", "err");
    }
  };

  const save = async () => {
    if (!active) return;
    setSaving(true);
    try {
      await api.writeFile(project.id, active, content);
      setDirty(false);
      push(`已保存 ${active}`);
    } catch (err) {
      push(err instanceof Error ? err.message : "保存失败", "err");
    } finally {
      setSaving(false);
    }
  };

  const visible = useMemo(() => {
    if (!nodes) return [];
    const keyword = filter.trim().toLowerCase();
    return keyword ? nodes.filter((node) => node.path.toLowerCase().includes(keyword)) : nodes;
  }, [nodes, filter]);

  if (ide?.available) {
    return (
      <div className="stack" style={{ gap: 10 }}>
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <Pill tone="ok" icon="code">
            code-server 在线
          </Pill>
          <span className="mono muted" style={{ fontSize: 11.5 }}>
            {ide.repo_path}
          </span>
          <div className="spacer" />
          <a className="btn btn-sm" href={ide.vscode_url} target="_blank" rel="noreferrer">
            <Icon name="external" size={12} /> 新标签打开
          </a>
        </div>
        <iframe className="ide-frame" src={ide.vscode_url} title="VS Code" />
      </div>
    );
  }

  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <Pill tone="warn" icon="code">
          内置编辑器
        </Pill>
        <span className="muted" style={{ fontSize: 11.5 }}>
          配置 <code className="mono">WB_IDE_URL_TEMPLATE</code> 可接入 Web 版 VS Code（code-server / openvscode-server）。
        </span>
      </div>

      <div className="ide">
        <div className="ide-tree">
          <div className="ide-tree-head">
            <Icon name="folder-open" size={12} /> 工作区
          </div>
          <div style={{ padding: "0 10px 8px" }}>
            <input
              className="input"
              style={{ height: 26, fontSize: 11.5 }}
              placeholder="过滤文件"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
            />
          </div>
          {nodes === null ? (
            <div style={{ padding: 8 }}>
              <LoadingRows rows={4} />
            </div>
          ) : (
            visible.map((node) => (
              <div
                key={node.path}
                className={cx("tree-item", active === node.path && "active")}
                onClick={() => openFile(node.path)}
                title={node.path}
              >
                <Icon name="file" size={11} />
                <span className="truncate">
                  {node.path.includes("/") ? (
                    <span style={{ color: "var(--ink-4)" }}>{node.path.slice(0, node.path.lastIndexOf("/") + 1)}</span>
                  ) : null}
                  {node.name}
                </span>
              </div>
            ))
          )}
        </div>

        <div className="editor">
          <div className="editor-head">
            <Icon name="file" size={12} />
            <span className="mono">{active || "未选择文件"}</span>
            {dirty ? <span className="pill pill-warn">未保存</span> : null}
            <div className="spacer" />
            <button className="btn btn-sm" onClick={save} disabled={!active || saving || !dirty}>
              <Icon name="save" size={12} /> {saving ? "保存中…" : "保存"}
            </button>
          </div>
          <div className="editor-body">
            {active ? (
              <textarea
                className="code-area"
                spellCheck={false}
                value={content}
                onChange={(event) => {
                  setContent(event.target.value);
                  setDirty(true);
                }}
              />
            ) : (
              <Empty icon="code" title="选择一个文件开始编辑" hint="左侧列出项目全部可编辑文件。" />
            )}
          </div>
        </div>
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
  const [selected, setSelected] = useState<string | null>(deployments[0]?.id ?? null);
  const [detail, setDetail] = useState<Deployment | null>(null);

  useEffect(() => {
    if (!selected && deployments[0]) setSelected(deployments[0].id);
  }, [deployments, selected]);

  const loadDetail = useCallback(async () => {
    if (!selected) return;
    try {
      setDetail(await api.deployment(selected));
    } catch {
      /* ignore transient polling errors */
    }
  }, [selected]);

  useEffect(() => {
    loadDetail();
    const active = detail ? ["queued", "building", "deploying"].includes(detail.status) : false;
    if (!active) return;
    const timer = setInterval(loadDetail, 1200);
    return () => clearInterval(timer);
  }, [loadDetail, detail?.status]);

  const selectedMeta = deployments.find((item) => item.id === selected) ?? detail;
  const streaming = detail ? ["queued", "building", "deploying"].includes(detail.status) : false;

  const stop = async () => {
    if (!selected) return;
    try {
      await api.stopDeployment(selected);
      push("已停止部署");
      await loadDetail();
      await onRefresh();
    } catch (err) {
      push(err instanceof Error ? err.message : "停止失败", "err");
    }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "340px minmax(0,1fr)", gap: 16, alignItems: "start" }}>
      <section className="card">
        <div className="card-head">
          <Icon name="rocket" size={14} />
          <span className="card-title">部署记录</span>
          <div className="spacer" />
          <button className="btn btn-sm btn-primary" onClick={onDeploy} disabled={busy}>
            <Icon name="plus" size={12} /> 部署
          </button>
        </div>
        <div className="card-body" style={{ padding: 8 }}>
          {deployments.length === 0 ? (
            <Empty icon="rocket" title="还没有部署" hint="部署会生成版本号、构建日志与健康检查。" />
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
                      {relativeTime(deployment.finished_at ?? deployment.started_at)} · {deployment.environment}
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
                {selectedMeta ? <StatusPill map={DEPLOY_STATUS} value={selectedMeta.status} /> : null}
                {selectedMeta?.provider ? <span className="tag">{selectedMeta.provider}</span> : null}
              </div>
              <div className="muted mono" style={{ fontSize: 11.5, marginTop: 4 }}>
                {selectedMeta?.url || project.url}
              </div>
            </div>
            <div className="spacer" />
            <button className="btn btn-sm" onClick={loadDetail} disabled={!selected}>
              <Icon name="refresh" size={12} /> 刷新
            </button>
            <button className="btn btn-sm btn-danger" onClick={stop} disabled={!streaming}>
              <Icon name="stop" size={12} /> 停止
            </button>
          </div>
          {selectedMeta?.error ? (
            <div className="pill pill-err" style={{ marginTop: 10, height: "auto", padding: "6px 9px" }}>
              {selectedMeta.error}
            </div>
          ) : null}
        </div>

        <LogViewer
          title={`deploy · ${selectedMeta?.version ?? "—"}`}
          log={detail?.log ?? ""}
          live={streaming}
          empty={selected ? "等待构建输出…" : "选择一条部署记录查看日志"}
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
  const [sql, setSql] = useState("SELECT name FROM sqlite_master WHERE type='table'");
  const [rows, setRows] = useState<Record<string, unknown>[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [objects, setObjects] = useState<{ key: string; size: number }[] | null>(null);
  const [bucket, setBucket] = useState("");

  const hasDb = project.resources.some((resource) => resource.kind === "database");
  const hasStorage = project.resources.some((resource) => resource.kind === "storage");

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
      push(`${result.row_count} 行 · ${result.database}`);
    } catch (err) {
      push(err instanceof Error ? err.message : "查询失败", "err");
      setRows([]);
    } finally {
      setBusy(false);
    }
  };

  const columns = rows && rows.length > 0 ? Object.keys(rows[0]) : [];

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(0,1fr)", gap: 16, alignItems: "start" }}>
      <section className="card">
        <div className="card-head">
          <Icon name="database" size={14} />
          <span className="card-title">数据库控制台</span>
          <div className="spacer" />
          <span className="tag">
            {project.resources.find((resource) => resource.kind === "database")?.name ?? "无数据库"}
          </span>
        </div>
        <div className="card-body stack" style={{ gap: 10 }}>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {[
              "SELECT name FROM sqlite_master WHERE type='table'",
              "SELECT * FROM _meta",
            ].map((sample) => (
              <button key={sample} className="btn btn-sm btn-ghost" onClick={() => setSql(sample)}>
                {sample.length > 34 ? `${sample.slice(0, 33)}…` : sample}
              </button>
            ))}
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
              <Icon name="play" size={12} /> {busy ? "执行中…" : "执行"}
            </button>
            <span className="muted" style={{ fontSize: 11.5 }}>
              {hasDb ? "走统一 Provider 接口，切换到真实云数据库后代码不变" : "该项目未装配数据库"}
            </span>
          </div>

          {rows === null ? null : rows.length === 0 ? (
            <Empty icon="database" title="没有返回行" />
          ) : (
            <div style={{ overflowX: "auto", border: "1px solid var(--line)", borderRadius: 10 }}>
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
          )}
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <Icon name="cloud" size={14} />
          <span className="card-title">对象存储</span>
          <div className="spacer" />
          <span className="tag">{bucket || "—"}</span>
        </div>
        <div className="card-body" style={{ paddingTop: 6, paddingBottom: 6 }}>
          {objects === null ? (
            <LoadingRows rows={3} />
          ) : objects.length === 0 ? (
            <Empty icon="cloud" title="桶内暂无对象" hint="上传资源后这里会列出 key 与大小。" />
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
      push("环境变量已保存");
    } catch (err) {
      push(err instanceof Error ? err.message : "保存失败", "err");
    }
  };

  const addDomain = async () => {
    if (!subdomain.trim()) return;
    try {
      await api.addDomain(project.id, subdomain.trim());
      setSubdomain("");
      await onRefresh();
      push("域名已绑定");
    } catch (err) {
      push(err instanceof Error ? err.message : "绑定失败", "err");
    }
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.3fr) minmax(0,1fr)", gap: 16, alignItems: "start" }}>
      <section className="card">
        <div className="card-head">
          <Icon name="shield" size={14} />
          <span className="card-title">环境变量</span>
          <div className="spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            写入 .env 并注入部署
          </span>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Key</th>
              <th>Value</th>
              <th style={{ width: 70 }}>类型</th>
            </tr>
          </thead>
          <tbody>
            {env.map((item) => (
              <tr key={item.id}>
                <td className="mono">{item.key}</td>
                <td className="mono truncate" style={{ maxWidth: 300 }} title={item.value}>
                  {item.value}
                </td>
                <td>{item.is_secret ? <Pill tone="warn">secret</Pill> : <Pill tone="idle">plain</Pill>}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="card-body" style={{ borderTop: "1px solid var(--line)" }}>
          <div className="grid grid-2" style={{ gap: 10 }}>
            <div className="field">
              <label>Key</label>
              <input className="input mono" value={key} onChange={(event) => setKey(event.target.value)} placeholder="FEATURE_FLAG" />
            </div>
            <div className="field">
              <label>Value</label>
              <input className="input mono" value={value} onChange={(event) => setValue(event.target.value)} placeholder="true" />
            </div>
          </div>
          <div className="row" style={{ marginTop: 10, gap: 10 }}>
            <label className="row" style={{ gap: 6, fontSize: 12, cursor: "pointer" }}>
              <input type="checkbox" checked={secret} onChange={(event) => setSecret(event.target.checked)} style={{ accentColor: "var(--accent)" }} />
              标记为密钥
            </label>
            <div className="spacer" />
            <button className="btn btn-sm btn-primary" onClick={addEnv} disabled={!key.trim()}>
              <Icon name="plus" size={12} /> 保存变量
            </button>
          </div>
        </div>
      </section>

      <div className="stack" style={{ gap: 16 }}>
        <section className="card">
          <div className="card-head">
            <Icon name="globe" size={14} />
            <span className="card-title">自定义域名</span>
          </div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <div className="field">
              <label>子域名</label>
              <div className="row" style={{ gap: 6 }}>
                <input
                  className="input mono"
                  value={subdomain}
                  onChange={(event) => setSubdomain(event.target.value)}
                  placeholder={`${project.slug}-staging`}
                />
                <button className="btn btn-sm" onClick={addDomain} disabled={!subdomain.trim()}>
                  绑定
                </button>
              </div>
              <span className="hint">默认后缀 {project.domains[0]?.suffix ?? "localtest.me"}</span>
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
            <span className="card-title">项目标识</span>
          </div>
          <div className="card-body">
            <KV
              items={[
                { k: "项目 ID", v: project.id },
                { k: "Slug", v: project.slug },
                { k: "创建时间", v: relativeTime(project.created_at) },
                {
                  k: "删除命令",
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

        <section className="card card-pad" style={{ borderColor: "color-mix(in srgb, var(--err) 40%, var(--line))" }}>
          <div className="row" style={{ gap: 8, marginBottom: 6 }}>
            <Icon name="trash" size={14} style={{ color: "var(--err)" }} />
            <span className="card-title" style={{ color: "var(--err)" }}>
              删除项目
            </span>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "0 0 12px", lineHeight: 1.6 }}>
            会清理工作区文件、对象存储桶、数据库与域名绑定，不可恢复。
          </p>
          <button className="btn btn-sm btn-danger" onClick={onDelete}>
            <Icon name="trash" size={12} /> 永久删除
          </button>
        </section>
      </div>
    </div>
  );
}
