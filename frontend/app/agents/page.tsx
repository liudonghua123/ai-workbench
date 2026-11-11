"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Agent, AgentRun, Project } from "@/lib/types";

export default function AgentsPage() {
  const { push } = useToast();
  const { t } = useI18n();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeKey, setActiveKey] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [projectId, setProjectId] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [thread, setThread] = useState<{ role: "user" | "agent"; text: string; run?: AgentRun }[]>(
    [],
  );
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ key: "", name: "", model: "", scope: "project" });
  const threadRef = useRef<HTMLDivElement>(null);

  const load = () =>
    Promise.all([api.agents(), api.projects()])
      .then(([list, projectList]) => {
        setAgents(list);
        setProjects(projectList);
        if (!activeKey && list[0]) setActiveKey(list[0].key);
      })
      .catch((err) => push(err.message, "err"));

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const node = threadRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [thread]);

  const active = useMemo(
    () => agents?.find((agent) => agent.key === activeKey) ?? null,
    [agents, activeKey],
  );

  const run = async () => {
    if (!prompt.trim() || !active) return;
    const text = prompt.trim();
    setThread((prev) => [...prev, { role: "user", text }]);
    setPrompt("");
    setRunning(true);
    try {
      const result = await api.runAgent(active.key, text, projectId || undefined);
      setThread((prev) => [
        ...prev,
        { role: "agent", text: result.output || t("agent.noOutput"), run: result },
      ]);
      if (result.status !== "succeeded") push(t("agent.stepFailed"), "err");
      load();
    } catch (err) {
      setThread((prev) => [
        ...prev,
        {
          role: "agent",
          text: t("agent.runFailed", {
            error: err instanceof Error ? err.message : t("agent.unknownError"),
          }),
        },
      ]);
    } finally {
      setRunning(false);
    }
  };

  const createAgent = async () => {
    if (!form.key.trim() || !form.name.trim()) return;
    try {
      await api.createAgent({
        key: form.key.trim(),
        name: form.name.trim(),
        model: form.model.trim() || undefined,
        permission_scope: form.scope,
      });
      setCreating(false);
      setForm({ key: "", name: "", model: "", scope: "project" });
      push(t("agent.created"));
      load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("agent.createFailed"), "err");
    }
  };

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("agent.title")}</h1>
          <p className="page-desc">{t("agent.desc")}</p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => setCreating(true)}>
          <Icon name="plus" size={13} /> {t("agent.new")}
        </button>
      </div>

      <div className="chat">
        <section className="card" style={{ padding: 8 }}>
          {agents === null ? (
            <div style={{ padding: 8 }}>
              <Skeleton height={52} />
            </div>
          ) : agents.length === 0 ? (
            <Empty icon="cpu" title={t("agent.empty")} />
          ) : (
            <div className="stack" style={{ gap: 3 }}>
              {agents.map((agent) => (
                <div
                  key={agent.id}
                  className={cx("agent-item", activeKey === agent.key && "active")}
                  onClick={() => {
                    setActiveKey(agent.key);
                    setThread([]);
                  }}
                >
                  <span className="agent-avatar">
                    <Icon
                      name={agent.permission_scope === "readonly" ? "shield" : "spark"}
                      size={14}
                    />
                  </span>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="row" style={{ gap: 6 }}>
                      <b style={{ fontSize: 12.5 }}>{agent.name}</b>
                      <Pill tone={agent.permission_scope === "readonly" ? "idle" : "accent"}>
                        {agent.permission_scope}
                      </Pill>
                    </div>
                    <div className="muted truncate" style={{ fontSize: 11 }}>
                      {agent.description}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="card" style={{ display: "flex", flexDirection: "column", minHeight: 460 }}>
          <div className="card-head">
            <span className="agent-avatar" style={{ width: 24, height: 24, borderRadius: 7 }}>
              <Icon name="cpu" size={13} />
            </span>
            <div style={{ minWidth: 0 }}>
              <div className="card-title">{active?.name ?? t("agent.pick")}</div>
              <div className="muted mono" style={{ fontSize: 10.5 }}>
                {active?.model ?? "—"} · harness: local
              </div>
            </div>
            <div className="spacer" />
            <select
              className="select"
              style={{ width: 190, height: 30, fontSize: 12 }}
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              <option value="">{t("agent.noProject")}</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </div>

          <div className="thread" ref={threadRef}>
            {thread.length === 0 ? (
              <Empty
                icon="spark"
                title={t("agent.goalTitle")}
                hint={active?.system_prompt ? active.system_prompt : t("agent.goalHint")}
              />
            ) : (
              thread.map((item, index) =>
                item.role === "user" ? (
                  <div key={index} className="bubble bubble-user">
                    {item.text}
                  </div>
                ) : (
                  <div key={index} className="bubble bubble-agent">
                    <Rich text={item.text} />
                    {item.run?.steps?.length ? (
                      <div className="steps">
                        {item.run.steps.map((step) => (
                          <div key={step.index} className={cx("step", !step.ok && "step-err")}>
                            <span className="step-idx">{step.index}</span>
                            <span>
                              <span className="step-tool">{step.tool}</span>{" "}
                              <span className="muted">{step.thought}</span>
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ),
              )
            )}
            {running ? (
              <div className="bubble bubble-agent">
                <span className="row" style={{ gap: 8, color: "var(--ink-3)" }}>
                  <Icon name="refresh" size={13} />
                  {t("agent.calling")}
                </span>
              </div>
            ) : null}
          </div>

          <div className="composer">
            <textarea
              className="textarea"
              rows={1}
              placeholder={t("agent.placeholder")}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) run();
              }}
            />
            <button className="btn btn-accent" onClick={run} disabled={running || !prompt.trim()}>
              <Icon name="play" size={13} /> {t("common.run")}
            </button>
          </div>
        </section>
      </div>

      {active ? (
        <section className="card card-pad" style={{ marginTop: 16 }}>
          <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
            <Binding
              label={t("agent.boundMcp")}
              icon="plug"
              items={active.mcp_servers}
            />
            <Binding
              label={t("agent.boundSkills")}
              icon="puzzle"
              items={active.skills}
            />
          </div>
        </section>
      ) : null}

      {creating ? (
        <div
          className="overlay"
          onClick={(event) => event.target === event.currentTarget && setCreating(false)}
        >
          <div className="modal" role="dialog" aria-modal="true">
            <div className="modal-head">
              <Icon name="cpu" size={15} />
              <span className="card-title">{t("agent.new")}</span>
              <div className="spacer" />
              <button
                className="icon-btn"
                onClick={() => setCreating(false)}
                aria-label={t("common.close")}
              >
                <Icon name="close" size={14} />
              </button>
            </div>
            <div className="modal-body stack" style={{ gap: 12 }}>
              <div className="grid grid-2" style={{ gap: 10 }}>
                <div className="field">
                  <label>{t("agent.formKey")}</label>
                  <input
                    className="input mono"
                    placeholder="release-bot"
                    value={form.key}
                    onChange={(event) => setForm({ ...form, key: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label>{t("agent.formName")}</label>
                  <input
                    className="input"
                    placeholder={t("agent.namePlaceholder")}
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                  />
                </div>
              </div>
              <div className="grid grid-2" style={{ gap: 10 }}>
                <div className="field">
                  <label>{t("agent.formModel")}</label>
                  <input
                    className="input mono"
                    placeholder="workbuddy-flash"
                    value={form.model}
                    onChange={(event) => setForm({ ...form, model: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label>{t("agent.formScope")}</label>
                  <select
                    className="select"
                    value={form.scope}
                    onChange={(event) => setForm({ ...form, scope: event.target.value })}
                  >
                    <option value="readonly">{t("agent.scopeReadonly")}</option>
                    <option value="project">{t("agent.scopeProject")}</option>
                    <option value="full">{t("agent.scopeFull")}</option>
                  </select>
                </div>
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setCreating(false)}>
                {t("common.cancel")}
              </button>
              <button
                className="btn btn-accent"
                onClick={createAgent}
                disabled={!form.key || !form.name}
              >
                {t("common.create")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

function Binding({
  label,
  icon,
  items,
}: {
  label: string;
  icon: "plug" | "puzzle";
  items?: string[];
}) {
  const { t } = useI18n();
  return (
    <div className="stack" style={{ gap: 4 }}>
      <span
        className="muted"
        style={{ fontSize: 10.5, letterSpacing: "0.1em", textTransform: "uppercase" }}
      >
        {label}
      </span>
      <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
        {items?.length ? (
          items.map((name) => (
            <span className="tag" key={name}>
              <Icon name={icon} size={10} /> {name}
            </span>
          ))
        ) : (
          <span className="muted" style={{ fontSize: 11.5 }}>
            {t("common.unbound")}
          </span>
        )}
      </div>
    </div>
  );
}

function Rich({ text }: { text: string }) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <span>
      {parts.map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return <b key={index}>{part.slice(2, -2)}</b>;
        }
        if (part.startsWith("`") && part.endsWith("`")) {
          return <code key={index}>{part.slice(1, -1)}</code>;
        }
        return <span key={index}>{part}</span>;
      })}
    </span>
  );
}
