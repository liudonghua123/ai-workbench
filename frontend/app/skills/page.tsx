"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { HubEntry, Skill, SkillContent } from "@/lib/types";

type Panel =
  | { kind: "view"; content: SkillContent }
  | { kind: "edit"; id: string; meta: Record<string, unknown>; body: string }
  | { kind: "install" };

/** Read a file without an <input> in the DOM: the picker returns handles. */
async function pickSkillFile(): Promise<{ name: string; text?: string; archive?: string } | null> {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".md,.zip,text/markdown,application/zip";
  return new Promise((resolve) => {
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return resolve(null);
      const reader = new FileReader();
      const isZip = file.name.toLowerCase().endsWith(".zip");
      reader.onload = () =>
        resolve(
          isZip
            ? { name: file.name, archive: String(reader.result).split(",")[1] ?? "" }
            : { name: file.name, text: String(reader.result) },
        );
      reader.onerror = () => resolve(null);
      isZip ? reader.readAsDataURL(file) : reader.readAsText(file);
    };
    input.click();
  });
}

export default function SkillsPage() {
  const { push } = useToast();
  const { t } = useI18n();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [panel, setPanel] = useState<Panel | null>(null);
  const [hub, setHub] = useState<HubEntry[] | null>(null);
  const editTimer = useRef<number | null>(null);

  const load = useCallback(
    () =>
      api
        .skills()
        .then(setSkills)
        .catch((err) => push(err.message, "err")),
    [push],
  );

  useEffect(() => {
    load();
    api.skillHub().then((data) => setHub(data.entries)).catch(() => setHub([]));
  }, [load]);

  const toggle = async (skill: Skill) => {
    try {
      const updated = await api.toggleSkill(skill.id);
      setSkills((prev) => prev?.map((item) => (item.id === updated.id ? updated : item)) ?? null);
      push(t("skill.toggled", { name: updated.name, state: updated.enabled ? t("common.enabled") : t("common.disabled") }));
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.opFailed"), "err");
    }
  };

  const scan = async () => {
    setBusy(true);
    try {
      const result = await api.scanSkills();
      push(t("skill.scanned", { n: result.scanned, root: result.root }));
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.scanFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (skill: Skill) => {
    if (!window.confirm(t("skill.deleteConfirm", { name: skill.name }))) return;
    try {
      await api.deleteSkill(skill.id);
      push(t("skill.deleted", { name: skill.name }));
      setPanel(null);
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.opFailed"), "err");
    }
  };

  const openView = async (skill: Skill) => {
    try {
      const content = await api.skillContent(skill.id);
      setPanel({ kind: "view", content });
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.opFailed"), "err");
    }
  };

  const openEdit = (content: SkillContent) => {
    if (content.main) {
      setPanel({
        kind: "edit",
        id: content.skill.id,
        meta: content.main.meta,
        body: content.main.body,
      });
      return;
    }
    // metadata-only skill: scaffold a SKILL.md from the row, so
    // "Edit" works even before the skill ever had files on disk
    const skill = content.skill;
    const metaLines = [`name: ${skill.name}`];
    if (skill.description) metaLines.push(`description: ${skill.description}`);
    if (skill.version) metaLines.push(`version: ${skill.version}`);
    const scaffold = [
      "---",
      ...metaLines,
      "---",
      "",
      `# ${skill.name}`,
      "",
      skill.description || "",
      "",
    ].join("\n");
    setPanel({ kind: "edit", id: skill.id, meta: {}, body: scaffold });
  };

  const saveEdit = async (state: Extract<Panel, { kind: "edit" }>) => {
    try {
      await api.saveSkillContent(state.id, state.body, state.meta);
      push(t("skill.saved"));
      setPanel(null);
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.opFailed"), "err");
    }
  };

  const installUrl = async (url: string) => {
    if (!url.trim()) return;
    setBusy(true);
    try {
      const row = await api.installSkillUrl(url.trim());
      push(t("skill.installed", { name: row.name }));
      setPanel(null);
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.installFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const installHub = async (entry: HubEntry) => {
    setBusy(true);
    try {
      const row = await api.installSkillUrl(`${entry.url}/tree/main/`);
      push(t("skill.installed", { name: row.name }));
      setPanel(null);
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.installFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const installFile = async () => {
    const picked = await pickSkillFile();
    if (!picked) return;
    setBusy(true);
    try {
      const row = await api.uploadSkill(picked);
      push(t("skill.installed", { name: row.name }));
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("skill.installFailed"), "err");
    } finally {
      setBusy(false);
    }
  };

  const [descBefore, descAfter] = t("skill.desc").split("{path}");

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("skill.title")}</h1>
          <p className="page-desc">
            {descBefore}
            <code className="mono">data/skills/*/SKILL.md</code>
            {descAfter}
          </p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => setPanel({ kind: "install" })}>
          <Icon name="download" size={13} />
          {t("skill.install")}
        </button>
        <button className="btn" onClick={scan} disabled={busy}>
          <Icon name={busy ? "refresh" : "search"} size={13} />
          {busy ? t("skill.scanning") : t("skill.scan")}
        </button>
      </div>

      {skills === null ? (
        <div className="grid grid-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} height={172} radius={13} />
          ))}
        </div>
      ) : skills.length === 0 ? (
        <div className="card">
          <Empty
            icon="puzzle"
            title={t("skill.empty")}
            hint={t("skill.emptyHint", { path: "<name>/SKILL.md" })}
          />
        </div>
      ) : (
        <div className="grid grid-3">
          {skills.map((skill) => (
            <section
              key={skill.id}
              className="card"
              style={{
                display: "flex",
                flexDirection: "column",
                opacity: skill.enabled ? 1 : 0.62,
              }}
            >
              <div className="card-head">
                <span className="project-glyph" style={{ width: 26, height: 26, borderRadius: 7 }}>
                  <Icon name="puzzle" size={13} />
                </span>
                <div style={{ minWidth: 0 }}>
                  <div className="card-title truncate">{skill.name}</div>
                  <div className="muted mono" style={{ fontSize: 10.5 }}>
                    {skill.key} · v{skill.version}
                  </div>
                </div>
                <div className="spacer" />
                <span className="tag">{skill.source}</span>
              </div>

              <div className="card-body stack" style={{ gap: 10, flex: 1 }}>
                <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6 }}>
                  {skill.description || t("common.noDescription")}
                </p>
                <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                  {skill.tools?.length ? (
                    skill.tools.map((tool) => (
                      <span className="tag" key={tool}>
                        {tool}
                      </span>
                    ))
                  ) : (
                    <span className="muted" style={{ fontSize: 11.5 }}>
                      {t("skill.noTools")}
                    </span>
                  )}
                </div>
              </div>

              <div className="card-body row" style={{ borderTop: "1px solid var(--line)", paddingTop: 11 }}>
                <Pill tone={skill.enabled ? "ok" : "idle"}>
                  {skill.enabled ? t("common.enabled") : t("common.disabled")}
                </Pill>
                <div className="spacer" />
                <button className="icon-btn" title={t("skill.view")} onClick={() => openView(skill)}>
                  <Icon name="eye" size={13} />
                </button>
                <button className={cx("btn btn-sm", skill.enabled ? "" : "btn-accent")} onClick={() => toggle(skill)}>
                  <Icon name={skill.enabled ? "stop" : "play"} size={11} />
                  {skill.enabled ? t("common.disable") : t("common.enable")}
                </button>
              </div>
            </section>
          ))}
        </div>
      )}

      {panel ? (
        <div
          className="overlay"
          onClick={(event) => event.target === event.currentTarget && setPanel(null)}
        >
          {panel.kind === "install" ? (
            <InstallPanel
              busy={busy}
              hub={hub}
              onClose={() => setPanel(null)}
              onUrl={installUrl}
              onHub={installHub}
              onFile={installFile}
            />
          ) : panel.kind === "view" ? (
            <ViewPanel
              content={panel.content}
              onClose={() => setPanel(null)}
              onEdit={() => openEdit(panel.content)}
              onDelete={() => remove(panel.content.skill)}
            />
          ) : (
            <EditPanel
              state={panel}
              onClose={() => setPanel(null)}
              onSave={saveEdit}
              onTimer={editTimer}
            />
          )}
        </div>
      ) : null}
    </main>
  );
}

function InstallPanel({
  busy,
  hub,
  onClose,
  onUrl,
  onHub,
  onFile,
}: {
  busy: boolean;
  hub: HubEntry[] | null;
  onClose: () => void;
  onUrl: (url: string) => void;
  onHub: (entry: HubEntry) => void;
  onFile: () => void;
}) {
  const { t } = useI18n();
  const [url, setUrl] = useState("");

  return (
    <div className="modal" role="dialog" aria-modal="true">
      <div className="modal-head">
        <Icon name="download" size={15} />
        <span className="card-title">{t("skill.installTitle")}</span>
        <div className="spacer" />
        <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="modal-body stack" style={{ gap: 14 }}>
        <div className="field">
          <label>{t("skill.installFromUrl")}</label>
          <div className="row" style={{ gap: 8 }}>
            <input
              className="input mono"
              style={{ flex: 1 }}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://github.com/user/repo/tree/main/skills/my-skill"
              onKeyDown={(event) => event.key === "Enter" && onUrl(url)}
            />
            <button className="btn btn-accent" disabled={busy || !url.trim()} onClick={() => onUrl(url)}>
              {t("skill.installGo")}
            </button>
          </div>
          <p className="muted" style={{ fontSize: 11.5, margin: "6px 0 0" }}>
            {t("skill.installUrlHint")}
          </p>
        </div>

        <div className="row" style={{ gap: 8, alignItems: "center" }}>
          <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
          <span className="muted" style={{ fontSize: 11 }}>
            {t("skill.or")}
          </span>
          <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
        </div>

        <button className="btn" onClick={onFile} disabled={busy}>
          <Icon name="upload" size={13} />
          {t("skill.installFromFile")}
        </button>

        {hub && hub.length ? (
          <div className="stack" style={{ gap: 8 }}>
            <span className="muted" style={{ fontSize: 11.5, fontWeight: 600 }}>
              {t("skill.hubTitle")}
            </span>
            <div className="stack" style={{ gap: 6 }}>
              {hub.map((entry) => (
                <div
                  key={entry.repo}
                  className="row"
                  style={{
                    gap: 10,
                    padding: "9px 11px",
                    border: "1px solid var(--line)",
                    borderRadius: 10,
                  }}
                >
                  <Icon name="globe" size={13} />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="mono" style={{ fontSize: 11.5, fontWeight: 600 }}>
                      {entry.repo}
                    </div>
                    <div className="muted" style={{ fontSize: 11 }}>
                      {entry.description}
                    </div>
                  </div>
                  <button className="btn btn-sm" disabled={busy} onClick={() => onHub(entry)}>
                    {t("skill.installGo")}
                  </button>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function ViewPanel({
  content,
  onClose,
  onEdit,
  onDelete,
}: {
  content: SkillContent;
  onClose: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<string>("SKILL.md");
  const skill = content.skill;
  const files = content.files;
  const active = files.find((file) => file.path === selected);

  return (
    <div className="modal modal-lg" role="dialog" aria-modal="true">
      <div className="modal-head">
        <Icon name="puzzle" size={15} />
        <span className="card-title truncate">{skill.name}</span>
        <span className="tag mono" style={{ fontSize: 10 }}>
          v{skill.version}
        </span>
        <span className="tag">{skill.source}</span>
        <div className="spacer" />
        <button className="icon-btn" title={t("common.delete")} onClick={onDelete}>
          <Icon name="trash" size={13} />
        </button>
        <button className="btn btn-sm btn-accent" onClick={onEdit}>
          <Icon name="pencil" size={11} />
          {t("skill.edit")}
        </button>
        <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="modal-body" style={{ display: "grid", gridTemplateColumns: "200px 1fr", gap: 0 }}>
        <div
          className="stack"
          style={{
            gap: 2,
            borderRight: "1px solid var(--line)",
            paddingRight: 10,
            marginRight: 12,
            maxHeight: 420,
            overflowY: "auto",
          }}
        >
          {files.length === 0 ? (
            <span className="muted" style={{ fontSize: 11.5 }}>
              {t("skill.noFiles")}
            </span>
          ) : (
            files.map((file) => (
              <button
                key={file.path}
                className={cx("btn btn-sm", file.path === selected ? "btn-accent" : "")}
                style={{ justifyContent: "flex-start", border: "none", background: "transparent" }}
                onClick={() => setSelected(file.path)}
              >
                <Icon name={file.path === "SKILL.md" ? "file" : "code"} size={12} />
                <span className="mono truncate" style={{ fontSize: 11 }}>
                  {file.path}
                </span>
              </button>
            ))
          )}
        </div>
        <div style={{ maxHeight: 420, overflowY: "auto", minWidth: 0 }}>
          {active?.content != null ? (
            <pre className="terminal" style={{ margin: 0, maxHeight: "none", whiteSpace: "pre-wrap" }}>
              {active.content}
            </pre>
          ) : active?.binary ? (
            <Empty icon="file" title={t("skill.binaryFile")} hint={t("skill.binaryHint")} />
          ) : (
            <Empty icon="file" title={t("skill.noFiles")} hint={t("skill.noFilesHint")} />
          )}
        </div>
      </div>
    </div>
  );
}

function EditPanel({
  state,
  onClose,
  onSave,
  onTimer,
}: {
  state: Extract<Panel, { kind: "edit" }>;
  onClose: () => void;
  onSave: (state: Extract<Panel, { kind: "edit" }>) => void;
  onTimer: React.RefObject<number | null>;
}) {
  const { t } = useI18n();
  const [body, setBody] = useState(state.body);
  const dirty = body !== state.body;

  // Ctrl+S saves, like every editor the operator already knows.
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "s") {
        event.preventDefault();
        if (dirty) onSave({ ...state, body });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [body, dirty, onSave, state]);

  useEffect(
    () => () => {
      if (onTimer.current) window.clearTimeout(onTimer.current);
    },
    [onTimer],
  );

  return (
    <div className="modal modal-lg" role="dialog" aria-modal="true">
      <div className="modal-head">
        <Icon name="pencil" size={15} />
        <span className="card-title">SKILL.md</span>
        <div className="spacer" />
        {dirty ? <Pill tone="warn">{t("skill.unsaved")}</Pill> : null}
        <button
          className="btn btn-sm btn-accent"
          disabled={!dirty}
          onClick={() => onSave({ ...state, body })}
        >
          <Icon name="save" size={11} />
          {t("common.save")}
        </button>
        <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
          <Icon name="close" size={14} />
        </button>
      </div>
      <div className="modal-body">
        <textarea
          className="textarea mono"
          style={{ minHeight: 380, fontSize: 12, lineHeight: 1.65 }}
          value={body}
          spellCheck={false}
          onChange={(event) => setBody(event.target.value)}
        />
        <p className="muted" style={{ fontSize: 11.5, margin: "8px 0 0" }}>
          {t("skill.editHint")}
        </p>
      </div>
    </div>
  );
}
