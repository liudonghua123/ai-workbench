"use client";

/**
 * Project workspace panels: code, debug (preview), version control, conventions.
 *
 * These are the four verbs the platform promises — edit, debug, run, publish — and
 * each panel calls the same REST endpoints that the MCP tools and the agent call.
 * Nothing here is privileged: if it works in the UI, an agent can do it, and vice
 * versa.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CodeEditor } from "@/components/CodeEditor";
import { Icon } from "@/components/Icon";
import { LogViewer } from "@/components/LogViewer";
import { CopyButton, Empty, LoadingRows, Pill, StatusPill, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { DEPLOY_STATUS, cx, relativeTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type {
  ConventionReport,
  Deployment,
  GitCommit,
  GitDiff,
  GitStatus,
  PreviewState,
  Project,
  TreeNode,
} from "@/lib/types";

const ACTIVE = ["queued", "building", "deploying"];

/* ==========================================================================
   shared bits
   ========================================================================== */

function DiffView({ diff, empty }: { diff: string; empty: string }) {
  if (!diff.trim()) {
    return (
      <div className="diff-empty">
        <span className="muted">{empty}</span>
      </div>
    );
  }
  return (
    <pre className="diff">
      {diff.split("\n").map((line, index) => {
        const kind = line.startsWith("@@")
          ? "hunk"
          : line.startsWith("+++") || line.startsWith("---")
            ? "meta"
            : line.startsWith("+")
              ? "add"
              : line.startsWith("-")
                ? "del"
                : line.startsWith("diff ") || line.startsWith("index ")
                  ? "meta"
                  : "";
        return (
          <div key={index} className={cx("diff-line", kind && `diff-${kind}`)}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

function FileStatus({ status }: { status: string }) {
  const tone =
    status === "untracked" || status === "added"
      ? "ok"
      : status === "deleted"
        ? "err"
        : "warn";
  return <Pill tone={tone}>{status.slice(0, 3)}</Pill>;
}

/* ==========================================================================
   code — file tree + editor
   ========================================================================== */

type Tree = { name: string; path: string; type: "file" | "dir"; children: Tree[] };

function buildTree(nodes: TreeNode[]): Tree[] {
  const root: Tree[] = [];
  const dirs = new Map<string, Tree>();

  const ensureDir = (path: string): Tree => {
    const existing = dirs.get(path);
    if (existing) return existing;
    const name = path.split("/").pop() ?? path;
    const node: Tree = { name, path, type: "dir", children: [] };
    dirs.set(path, node);
    const parentPath = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    if (parentPath) ensureDir(parentPath).children.push(node);
    else root.push(node);
    return node;
  };

  for (const node of nodes) {
    if (node.type === "dir") {
      ensureDir(node.path);
      continue;
    }
    const parentPath = node.path.includes("/")
      ? node.path.slice(0, node.path.lastIndexOf("/"))
      : "";
    const file: Tree = { name: node.name, path: node.path, type: "file", children: [] };
    if (parentPath) ensureDir(parentPath).children.push(file);
    else root.push(file);
  }

  const sortTree = (items: Tree[]) => {
    items.sort((a, b) =>
      a.type === b.type ? a.name.localeCompare(b.name) : a.type === "dir" ? -1 : 1,
    );
    items.forEach((item) => sortChildren(item));
  };
  const sortChildren = (item: Tree) => {
    if (item.children.length) sortTree(item.children);
  };
  sortTree(root);
  return root;
}

export function CodeTab({
  project,
  ide,
  onDirtyChange,
}: {
  project: Project;
  ide: { vscode_url: string; available: boolean; repo_path: string; note: string } | null;
  onDirtyChange?: (dirty: number) => void;
}) {
  const { push } = useToast();
  const { t } = useI18n();
  const [nodes, setNodes] = useState<TreeNode[] | null>(null);
  const [tree, setTree] = useState<Tree[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [active, setActive] = useState("");
  const [content, setContent] = useState("");
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState("");
  const [changed, setChanged] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);
  const [newPath, setNewPath] = useState("");

  const dirty = active !== "" && content !== saved;

  const loadGit = useCallback(async () => {
    try {
      const state = await api.gitStatus(project.id);
      const map: Record<string, string> = {};
      state.files.forEach((file) => {
        map[file.path] = file.status;
      });
      setChanged(map);
      onDirtyChange?.(state.files.length);
    } catch {
      setChanged({});
    }
  }, [project.id, onDirtyChange]);

  const loadTree = useCallback(async () => {
    try {
      const data = await api.tree(project.id);
      setNodes(data.nodes);
      const built = buildTree(data.nodes);
      setTree(built);
      const all: string[] = [];
      const walk = (items: Tree[]) =>
        items.forEach((item) => {
          if (item.type === "dir") {
            all.push(item.path);
            walk(item.children);
          }
        });
      walk(built);
      setExpanded((prev) => (prev.size === 0 ? new Set(all) : prev));
    } catch (err) {
      push(err instanceof Error ? err.message : t("code.readFailed"), "err");
    }
  }, [project.id, push, t]);

  useEffect(() => {
    loadTree();
    loadGit();
  }, [loadTree, loadGit]);

  const openFile = async (path: string) => {
    if (dirty && !window.confirm(t("code.dirtyConfirm"))) return;
    try {
      const file = await api.readFile(project.id, path);
      setActive(path);
      setContent(file.content);
      setSaved(file.content);
    } catch (err) {
      push(err instanceof Error ? err.message : t("code.readFailed"), "err");
    }
  };

  const save = useCallback(async () => {
    if (!active || content === saved) return;
    setSaving(true);
    try {
      await api.writeFile(project.id, active, content);
      setSaved(content);
      push(t("code.saved", { path: active }));
      await loadGit();
    } catch (err) {
      push(err instanceof Error ? err.message : t("code.saveFailed"), "err");
    } finally {
      setSaving(false);
    }
  }, [active, content, saved, project.id, push, t, loadGit]);

  const create = async () => {
    const path = newPath.trim().replace(/^\/+/, "");
    if (!path) return;
    try {
      await api.writeFile(project.id, path, "");
      setCreating(false);
      setNewPath("");
      await loadTree();
      await openFile(path);
    } catch (err) {
      push(err instanceof Error ? err.message : t("code.saveFailed"), "err");
    }
  };

  const remove = async () => {
    if (!active) return;
    if (!window.confirm(t("code.deleteConfirm", { path: active }))) return;
    try {
      await api.deleteFile(project.id, active);
      push(t("code.deleted", { path: active }));
      setActive("");
      setContent("");
      setSaved("");
      await Promise.all([loadTree(), loadGit()]);
    } catch (err) {
      push(err instanceof Error ? err.message : t("code.revertFailed"), "err");
    }
  };

  const revert = async () => {
    if (!active) return;
    try {
      await api.gitRestore(project.id, active);
      const file = await api.readFile(project.id, active);
      setContent(file.content);
      setSaved(file.content);
      push(t("code.reverted", { path: active }));
      await loadGit();
    } catch (err) {
      push(err instanceof Error ? err.message : t("code.revertFailed"), "err");
    }
  };

  const keyword = filter.trim().toLowerCase();
  const visible = keyword
    ? (nodes ?? []).filter((node) => node.type === "file" && node.path.toLowerCase().includes(keyword))
    : [];

  const renderTree = (items: Tree[], depth = 0) =>
    items.map((item) => {
      const isOpen = expanded.has(item.path);
      return (
        <div key={item.path}>
          <button
            type="button"
            className={cx(
              "tree-item",
              item.type === "file" && active === item.path && "active",
              item.type === "dir" && "tree-dir",
            )}
            style={{ paddingLeft: 8 + depth * 12 }}
            onClick={() => {
              if (item.type === "dir") {
                setExpanded((prev) => {
                  const next = new Set(prev);
                  if (next.has(item.path)) next.delete(item.path);
                  else next.add(item.path);
                  return next;
                });
              } else {
                openFile(item.path);
              }
            }}
            title={item.path}
          >
            {item.type === "dir" ? (
              <>
                <Icon name={isOpen ? "chevron-down" : "chevron-right"} size={11} />
                <Icon name={isOpen ? "folder-open" : "folder"} size={12} />
              </>
            ) : (
              <>
                <span style={{ width: 11 }} />
                <Icon name="file" size={11} />
              </>
            )}
            <span className="truncate">{item.name}</span>
            {item.type === "file" && changed[item.path] ? (
              <i className="tree-mark" title={t("code.changed")} />
            ) : null}
          </button>
          {item.type === "dir" && isOpen ? renderTree(item.children, depth + 1) : null}
        </div>
      );
    });

  return (
    <div className="code-workbench">
      <aside className="code-side">
        <div className="code-side-head">
          <Icon name="folder-open" size={12} />
          <span>{t("code.workspace")}</span>
          <div className="spacer" />
          <button
            className="icon-btn"
            title={t("code.newFile")}
            aria-label={t("code.newFile")}
            onClick={() => setCreating((prev) => !prev)}
          >
            <Icon name="plus" size={13} />
          </button>
          <button
            className="icon-btn"
            title={t("common.refresh")}
            aria-label={t("common.refresh")}
            onClick={() => {
              loadTree();
              loadGit();
            }}
          >
            <Icon name="refresh" size={13} />
          </button>
        </div>

        {creating ? (
          <div className="code-new">
            <input
              className="input"
              autoFocus
              placeholder={t("code.newFilePrompt")}
              value={newPath}
              onChange={(event) => setNewPath(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") create();
                if (event.key === "Escape") setCreating(false);
              }}
            />
            <button className="btn btn-sm btn-primary" onClick={create}>
              {t("code.create")}
            </button>
          </div>
        ) : null}

        <div className="code-filter">
          <Icon name="search" size={12} />
          <input
            className="input"
            placeholder={t("code.filter")}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </div>

        <div className="code-tree">
          {nodes === null ? (
            <LoadingRows rows={5} />
          ) : keyword ? (
            visible.length === 0 ? (
              <div className="code-empty muted">—</div>
            ) : (
              visible.map((node) => (
                <button
                  key={node.path}
                  type="button"
                  className={cx("tree-item", active === node.path && "active")}
                  style={{ paddingLeft: 12 }}
                  onClick={() => openFile(node.path)}
                  title={node.path}
                >
                  <Icon name="file" size={11} />
                  <span className="truncate">{node.path}</span>
                  {changed[node.path] ? <i className="tree-mark" /> : null}
                </button>
              ))
            )
          ) : (
            renderTree(tree)
          )}
        </div>
      </aside>

      <section className="code-main">
        <div className="editor-head">
          <Icon name="code" size={12} />
          <span className="mono truncate">{active || t("code.noFile")}</span>
          {dirty ? <Pill tone="warn">{t("code.unsaved")}</Pill> : null}
          <div className="spacer" />
          {changed[active] ? (
            <button className="btn btn-sm" onClick={revert} title={t("code.revert")}>
              <Icon name="refresh" size={12} /> {t("code.revert")}
            </button>
          ) : null}
          <button className="btn btn-sm" onClick={remove} disabled={!active}>
            <Icon name="trash" size={12} />
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={save}
            disabled={!dirty || saving}
            title="⌘S / Ctrl+S"
          >
            <Icon name="save" size={12} />
            {saving ? t("common.saving") : t("common.save")}
          </button>
          {ide?.available ? (
            <a className="btn btn-sm" href={ide.vscode_url} target="_blank" rel="noreferrer">
              <Icon name="external" size={12} /> {t("code.externalIde")}
            </a>
          ) : null}
        </div>

        <div className="code-surface">
          {active ? (
            <CodeEditor value={content} path={active} onChange={setContent} onSave={save} />
          ) : (
            <Empty icon="code" title={t("code.pick")} hint={t("code.pickHint")} />
          )}
        </div>

        <div className="code-foot">
          <Icon name="shield" size={11} />
          <span>{t("code.savedHint")}</span>
        </div>
      </section>
    </div>
  );
}

/* ==========================================================================
   preview — debug environment
   ========================================================================== */

export function PreviewTab({
  project,
  onChanged,
}: {
  project: Project;
  onChanged?: () => void;
}) {
  const { push } = useToast();
  const { t } = useI18n();
  const [state, setState] = useState<PreviewState | null>(null);
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [log, setLog] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [frameKey, setFrameKey] = useState(0);
  const [note, setNote] = useState("");
  const frameRef = useRef<HTMLIFrameElement>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.preview(project.id);
      setState(next);
      if (next.latest) {
        const detail = await api.deployment(next.latest.id);
        setDeployment(detail);
        setLog(detail.log ?? "");
        setStreaming(ACTIVE.includes(detail.status));
      }
    } catch (err) {
      push(err instanceof Error ? err.message : t("pv.failed"), "err");
    }
  }, [project.id, push, t]);

  useEffect(() => {
    load();
  }, [load]);

  // live log for an in-flight preview; one snapshot then deltas
  useEffect(() => {
    if (!deployment || !streaming) return;
    const source = new EventSource(api.deploymentStreamUrl(deployment.id));
    setLog("");
    source.addEventListener("log", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { lines: string[] };
      setLog((prev) => `${prev}${payload.lines.join("\n")}\n`);
    });
    source.addEventListener("status", (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as Partial<Deployment>;
      setDeployment((prev) => (prev ? { ...prev, ...payload } : prev));
    });
    const finish = () => {
      source.close();
      setStreaming(false);
      setFrameKey((prev) => prev + 1);
      load();
      onChanged?.();
    };
    source.addEventListener("done", finish);
    source.addEventListener("timeout", finish);
    source.onerror = () => {
      source.close();
      setStreaming(false);
    };
    return () => source.close();
  }, [deployment?.id, streaming, load, onChanged]);

  const start = async () => {
    setBusy(true);
    try {
      const dep = await api.startPreview(project.id);
      setDeployment(dep);
      setLog("");
      setStreaming(true);
      setState((prev) => (prev ? { ...prev, status: dep.status } : prev));
      push(t("pv.started", { url: project.preview.url }));
    } catch (err) {
      push(err instanceof Error ? err.message : t("pv.failed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    try {
      await api.stopPreview(project.id);
      push(t("pv.stopped"));
      setStreaming(false);
      await load();
      onChanged?.();
    } catch (err) {
      push(err instanceof Error ? err.message : t("pv.failed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    setBusy(true);
    try {
      const result = await api.publish(project.id, note);
      push(t("pv.published", { version: result.version, tag: result.tag.tag ?? "" }));
      setNote("");
      onChanged?.();
    } catch (err) {
      push(err instanceof Error ? err.message : t("pv.publishFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const running = state?.status === "running";
  const pending = state ? ACTIVE.includes(state.status) : false;

  return (
    <div className="split-2 preview-layout">
      <section className="card preview-card">
        <div className="card-head">
          <Icon name="globe" size={14} />
          <span className="card-title">{t("pv.frame")}</span>
          {state ? <StatusPill map={DEPLOY_STATUS} value={state.status} /> : null}
          <div className="spacer" />
          <span className="mono muted" style={{ fontSize: 11 }}>
            {project.preview.fqdn}
          </span>
          <button
            className="icon-btn"
            title={t("pv.reload")}
            aria-label={t("pv.reload")}
            onClick={() => setFrameKey((prev) => prev + 1)}
          >
            <Icon name="refresh" size={13} />
          </button>
          <a
            className="icon-btn"
            href={project.preview.url}
            target="_blank"
            rel="noreferrer"
            title={t("pv.open")}
            aria-label={t("pv.open")}
          >
            <Icon name="external" size={13} />
          </a>
        </div>

        <div className="preview-stage">
          {running ? (
            <iframe
              key={frameKey}
              ref={frameRef}
              className="preview-frame"
              src={project.preview.url}
              title={project.name}
            />
          ) : (
            <Empty
              icon="play"
              title={pending ? t("pv.starting") : t("pv.notRunning")}
              hint={t("pv.notRunningHint", {
                fqdn: project.preview.fqdn,
                port: project.preview.port ?? 0,
              })}
              action={
                <button className="btn btn-primary btn-sm" onClick={start} disabled={busy || pending}>
                  <Icon name="play" size={12} /> {t("pv.start")}
                </button>
              }
            />
          )}
        </div>
      </section>

      <section className="stack" style={{ gap: 12 }}>
        <div className="card card-pad">
          <div className="row" style={{ gap: 8 }}>
            <b>{t("pv.title")}</b>
            <div className="spacer" />
            <CopyButton value={project.preview.url} label={t("common.copyUrl")} />
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "6px 0 12px", lineHeight: 1.65 }}>
            {t("pv.subtitle")}
          </p>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {running ? (
              <button className="btn btn-sm" onClick={stop} disabled={busy}>
                <Icon name="stop" size={12} /> {t("pv.stop")}
              </button>
            ) : (
              <button className="btn btn-sm" onClick={start} disabled={busy || pending}>
                <Icon name="play" size={12} /> {t("pv.start")}
              </button>
            )}
            <button
              className="btn btn-sm"
              onClick={stop}
              disabled={busy || !running}
              title={t("pv.restart")}
            >
              <Icon name="refresh" size={12} /> {t("pv.restart")}
            </button>
            <button className="btn btn-sm btn-accent" onClick={publish} disabled={busy}>
              <Icon name="rocket" size={12} /> {t("pv.publish")}
            </button>
          </div>
          <input
            className="input"
            style={{ marginTop: 10 }}
            placeholder={t("pv.message")}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <p className="muted" style={{ fontSize: 11, margin: "8px 0 0", lineHeight: 1.6 }}>
            {t("pv.publishHint")}
          </p>
        </div>

        <div className="card">
          <div className="card-head">
            <Icon name="route" size={14} />
            <span className="card-title">{t("pv.facts")}</span>
            <div className="spacer" />
            {deployment?.image ? <span className="tag">{deployment.image}</span> : null}
          </div>
          <div className="card-body" style={{ paddingTop: 4 }}>
            <dl className="kv kv-tight">
              <dt>{t("pv.domain")}</dt>
              <dd className="mono">{project.preview.fqdn}</dd>
              <dt>{t("pv.port")}</dt>
              <dd className="mono">{project.preview.port ?? "—"}</dd>
              <dt>{t("pv.command")}</dt>
              <dd className="mono" style={{ lineHeight: 1.5 }}>
                {project.dev_command || "—"}
              </dd>
              <dt>{t("pv.health")}</dt>
              <dd className="mono">{project.health_path}</dd>
              <dt>{t("ov.port")}</dt>
              <dd className="mono">{project.port ?? "—"}</dd>
            </dl>
          </div>
        </div>

        <LogViewer
          title={`${t("pv.log")} · ${deployment?.version ?? "—"}`}
          log={log || deployment?.log || ""}
          live={streaming}
          empty={t("pv.frameEmpty")}
        />
      </section>
    </div>
  );
}

/* ==========================================================================
   version control
   ========================================================================== */

export function VersionTab({ project, onChanged }: { project: Project; onChanged?: () => void }) {
  const { push } = useToast();
  const { t, locale } = useI18n();
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [commits, setCommits] = useState<GitCommit[]>([]);
  const [diff, setDiff] = useState<GitDiff | null>(null);
  const [selectedFile, setSelectedFile] = useState("");
  const [selectedCommit, setSelectedCommit] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [state, history] = await Promise.all([
        api.gitStatus(project.id),
        api.gitLog(project.id, 30),
      ]);
      setStatus(state);
      setCommits(history.commits);
    } catch (err) {
      push(err instanceof Error ? err.message : t("detail.loadFailed"), "err");
    }
  }, [project.id, push, t]);

  useEffect(() => {
    load();
  }, [load]);

  const openFile = async (path: string) => {
    setSelectedCommit("");
    setSelectedFile(path);
    try {
      setDiff(await api.gitDiff(project.id, path));
    } catch (err) {
      push(err instanceof Error ? err.message : t("detail.loadFailed"), "err");
    }
  };

  const openCommit = async (sha: string) => {
    setSelectedFile("");
    setSelectedCommit(sha);
    try {
      setDiff(await api.gitShow(project.id, sha));
    } catch (err) {
      push(err instanceof Error ? err.message : t("detail.loadFailed"), "err");
    }
  };

  const commit = async () => {
    setBusy(true);
    try {
      const result = await api.gitCommit(project.id, message);
      if (result.committed) {
        push(t("git.committed", { short: result.short ?? "" }));
        setMessage("");
        setDiff(null);
        setSelectedFile("");
      } else {
        push(t("git.nothing"));
      }
      await load();
      onChanged?.();
    } catch (err) {
      push(err instanceof Error ? err.message : t("git.commitFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const revert = async (sha: string) => {
    if (!window.confirm(t("git.revertConfirm", { sha: sha.slice(0, 8) }))) return;
    setBusy(true);
    try {
      const result = await api.gitRevert(project.id, sha);
      push(t("git.reverted", { short: result.short ?? "" }));
      await load();
      onChanged?.();
    } catch (err) {
      push(err instanceof Error ? err.message : t("git.revertFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const files = status?.files ?? [];

  return (
    <div className="split-2 version-layout">
      <section className="card">
        <div className="card-head">
          <Icon name="clock" size={14} />
          <span className="card-title">{t("git.history")}</span>
          <div className="spacer" />
          <span className="tag">{status?.branch || "—"}</span>
          <button
            className="icon-btn"
            onClick={load}
            title={t("common.refresh")}
            aria-label={t("common.refresh")}
          >
            <Icon name="refresh" size={13} />
          </button>
        </div>
        <div className="card-body" style={{ padding: 6 }}>
          {commits.length === 0 ? (
            <Empty icon="clock" title={t("git.empty")} />
          ) : (
            <div className="stack" style={{ gap: 2 }}>
              {commits.map((item) => (
                <button
                  key={item.sha}
                  type="button"
                  className={cx("commit-row", selectedCommit === item.sha && "active")}
                  onClick={() => openCommit(item.sha)}
                >
                  <span className="commit-dot" />
                  <span style={{ minWidth: 0, flex: 1 }}>
                    <span className="commit-msg truncate">{item.message}</span>
                    <span className="commit-meta">
                      <span className="mono">{item.short}</span>
                      <span>·</span>
                      <span>{relativeTime(item.date, locale)}</span>
                      {item.tags.map((tag) => (
                        <span className="tag tag-accent" key={tag}>
                          {tag}
                        </span>
                      ))}
                    </span>
                  </span>
                  {status?.head?.sha === item.sha ? (
                    <span className="tag">{t("git.head")}</span>
                  ) : null}
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="stack" style={{ gap: 12 }}>
        <div className="card card-pad">
          <div className="row" style={{ gap: 8 }}>
            <Icon name="route" size={13} />
            <b style={{ fontSize: 13 }}>{t("git.changes")}</b>
            <Pill tone={files.length ? "warn" : "ok"}>
              {files.length ? t("git.dirty", { n: files.length }) : t("git.clean")}
            </Pill>
            <div className="spacer" />
            <span className="mono muted" style={{ fontSize: 11 }}>
              {status?.head?.short ?? "—"}
            </span>
          </div>

          {files.length > 0 ? (
            <div className="change-list">
              {files.map((file) => (
                <button
                  key={file.path}
                  type="button"
                  className={cx("change-row", selectedFile === file.path && "active")}
                  onClick={() => openFile(file.path)}
                >
                  <FileStatus status={file.status} />
                  <span className="mono truncate">{file.path}</span>
                  <span className="spacer" />
                  <span className="diff-count add">+{file.additions}</span>
                  <span className="diff-count del">-{file.deletions}</span>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
              {t("git.noChanges")}
            </p>
          )}

          <div className="commit-box">
            <textarea
              className="textarea"
              rows={2}
              placeholder={t("git.messagePlaceholder")}
              value={message}
              onChange={(event) => setMessage(event.target.value)}
            />
            <div className="row" style={{ gap: 8, marginTop: 8 }}>
              <button className="btn btn-sm btn-primary" onClick={commit} disabled={busy || !files.length}>
                <Icon name="check" size={12} />
                {busy ? t("git.committing") : t("git.commit")}
              </button>
              <span className="muted" style={{ fontSize: 11 }}>
                {t("git.appendOnly")}
              </span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <Icon name="code" size={14} />
            <span className="card-title">
              {selectedCommit ? selectedCommit.slice(0, 8) : selectedFile || t("git.changes")}
            </span>
            <div className="spacer" />
            {diff?.additions !== undefined ? (
              <>
                <span className="diff-count add">+{diff.additions}</span>
                <span className="diff-count del">-{diff.deletions}</span>
              </>
            ) : null}
            {selectedCommit ? (
              <button className="btn btn-sm btn-danger" onClick={() => revert(selectedCommit)} disabled={busy}>
                <Icon name="refresh" size={12} /> {t("git.revert")}
              </button>
            ) : null}
          </div>
          <div className="diff-body">
            {diff ? (
              <DiffView
                diff={diff.diff}
                empty={selectedFile ? t("git.noChanges") : t("git.pickCommit")}
              />
            ) : (
              <Empty
                icon="code"
                title={t("git.pickFile")}
                hint={t("git.appendOnly")}
              />
            )}
          </div>
        </div>
      </section>
    </div>
  );
}

/* ==========================================================================
   conventions
   ========================================================================== */

const CHECK_KEY = {
  pass: "cv.status.pass",
  warn: "cv.status.warn",
  fail: "cv.status.fail",
} as const;

export function ConventionsTab({ project }: { project: Project }) {
  const { push } = useToast();
  const { t } = useI18n();
  const [report, setReport] = useState<ConventionReport | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      setReport(await api.conventions(project.id));
    } catch (err) {
      push(err instanceof Error ? err.message : t("detail.loadFailed"), "err");
    } finally {
      setBusy(false);
    }
  }, [project.id, push, t]);

  useEffect(() => {
    load();
  }, [load]);

  if (!report) {
    return (
      <div className="card card-pad">
        <LoadingRows rows={4} />
      </div>
    );
  }

  const findings = report.score.warn + report.score.fail;

  return (
    <div className="stack" style={{ gap: 14 }}>
      <div className="card card-pad">
        <div className="row" style={{ gap: 10, flexWrap: "wrap" }}>
          <span className="project-glyph" style={{ width: 34, height: 34, borderRadius: 10 }}>
            <Icon name="shield" size={16} />
          </span>
          <div style={{ minWidth: 0 }}>
            <div className="row" style={{ gap: 8 }}>
              <b style={{ fontSize: 15 }}>{t("cv.title")}</b>
              <Pill tone={report.conformant ? "ok" : "warn"}>
                {report.conformant ? t("cv.conformant") : t("cv.drift", { n: findings })}
              </Pill>
              <span className="tag">{report.label}</span>
            </div>
            <p className="muted" style={{ fontSize: 12, margin: "4px 0 0" }}>
              {t("cv.subtitle")}
            </p>
          </div>
          <div className="spacer" />
          <span className="muted" style={{ fontSize: 12 }}>
            {t("cv.score", report.score)}
          </span>
          <button className="btn btn-sm" onClick={load} disabled={busy}>
            <Icon name="refresh" size={12} /> {t("cv.reload")}
          </button>
        </div>
      </div>

      <div className="cv-grid">
        <section className="card">
          <div className="card-head">
            <Icon name="check" size={14} />
            <span className="card-title">{t("cv.checks")}</span>
            <div className="spacer" />
            <span className="muted" style={{ fontSize: 11.5 }}>
              {report.spec.label}
            </span>
          </div>
          <div className="card-body" style={{ paddingTop: 4 }}>
            {report.checks.map((check) => (
              <div className="check-row" key={check.key}>
                <span className={cx("check-dot", `is-${check.status}`)} />
                <span style={{ minWidth: 0 }}>
                  <b style={{ fontSize: 12.5 }}>{check.title}</b>
                  <span className="muted check-detail">{check.detail}</span>
                </span>
                <span className="spacer" />
                <Pill tone={check.status === "pass" ? "ok" : check.status === "warn" ? "warn" : "err"}>
                  {t(CHECK_KEY[check.status])}
                </Pill>
              </div>
            ))}
          </div>
        </section>

        <div className="stack" style={{ gap: 14 }}>
          <section className="card">
            <div className="card-head">
              <Icon name="terminal" size={14} />
              <span className="card-title">{t("cv.commands")}</span>
            </div>
            <div className="card-body" style={{ paddingTop: 4 }}>
              <dl className="kv kv-tight">
                <dt>{t("cv.dev")}</dt>
                <dd className="mono">{report.commands.dev || "—"}</dd>
                <dt>{t("cv.build")}</dt>
                <dd className="mono">{report.commands.build || "—"}</dd>
                <dt>{t("cv.run")}</dt>
                <dd className="mono">{report.commands.run || "—"}</dd>
                <dt>{t("cv.health")}</dt>
                <dd className="mono">{report.commands.health_path}</dd>
                <dt>{t("cv.preview")}</dt>
                <dd className="mono">{report.preview.fqdn}</dd>
              </dl>
            </div>
          </section>

          <section className="card">
            <div className="card-head">
              <Icon name="file" size={14} />
              <span className="card-title">{t("cv.manifest")}</span>
              <div className="spacer" />
              <span className="tag">{report.manifest.path}</span>
            </div>
            <div className="card-body" style={{ paddingTop: 8 }}>
              {report.manifest.present ? (
                <div className="stack" style={{ gap: 4 }}>
                  <span className="muted" style={{ fontSize: 12 }}>
                    {t("cv.manifestOverrides", {
                      file: report.manifest.path,
                      n: Object.keys(report.manifest.overrides).length,
                    })}
                  </span>
                  {Object.entries(report.manifest.overrides).map(([key, value]) => (
                    <div className="row-between mono" key={key} style={{ fontSize: 11.5 }}>
                      <span>{key}</span>
                      <span className="muted truncate" style={{ maxWidth: 220 }}>
                        {value}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <span className="muted" style={{ fontSize: 12 }}>
                  {t("cv.manifestNone", { file: report.manifest.path })}
                </span>
              )}
            </div>
          </section>
        </div>
      </div>

      <div className="cv-grid">
        <section className="card">
          <div className="card-head">
            <Icon name="folder" size={14} />
            <span className="card-title">{t("cv.layout")}</span>
          </div>
          <table className="table">
            <tbody>
              {report.spec.layout.map((item) => (
                <tr key={item.path}>
                  <td className="mono" style={{ whiteSpace: "nowrap" }}>
                    {item.path}
                  </td>
                  <td className="muted">{item.meaning}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="card">
          <div className="card-head">
            <Icon name="shield" size={14} />
            <span className="card-title">{t("cv.rules")}</span>
          </div>
          <div className="card-body" style={{ paddingTop: 6 }}>
            {report.spec.conventions.map((item) => (
              <div className="rule-row" key={item.key}>
                <b>{item.title}</b>
                <span className="muted">{item.detail}</span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
