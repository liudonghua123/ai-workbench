"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import type { Agent, AgentRun, Project } from "@/lib/types";

export default function AgentsPage() {
  const { push } = useToast();
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeKey, setActiveKey] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [projectId, setProjectId] = useState<string>("");
  const [running, setRunning] = useState(false);
  const [thread, setThread] = useState<{ role: "user" | "agent"; text: string; run?: AgentRun }[]>([]);
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
        { role: "agent", text: result.output || "（无输出）", run: result },
      ]);
      if (result.status !== "succeeded") push("Agent 执行存在失败步骤", "err");
      load();
    } catch (err) {
      setThread((prev) => [
        ...prev,
        { role: "agent", text: `执行失败：${err instanceof Error ? err.message : "未知错误"}` },
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
      push("Agent 已创建");
      load();
    } catch (err) {
      push(err instanceof Error ? err.message : "创建失败", "err");
    }
  };

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">Agents</h1>
          <p className="page-desc">
            Agent = 一句话目标 + 一组工具。工具来自系统 MCP（创建项目、改文件、部署……），
            harness 可替换为 agentscope 等实现。
          </p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => setCreating(true)}>
          <Icon name="plus" size={13} /> 新建 Agent
        </button>
      </div>

      <div className="chat">
        <section className="card" style={{ padding: 8 }}>
          {agents === null ? (
            <div style={{ padding: 8 }}>
              <Skeleton height={52} />
            </div>
          ) : agents.length === 0 ? (
            <Empty icon="cpu" title="还没有 Agent" />
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
                    <Icon name={agent.permission_scope === "readonly" ? "shield" : "spark"} size={14} />
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
              <div className="card-title">{active?.name ?? "选择一个 Agent"}</div>
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
              <option value="">不绑定项目</option>
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
                title="给 Agent 一个目标"
                hint={
                  active?.system_prompt
                    ? active.system_prompt
                    : "例如：帮我创建一个纯前端的活动落地页，叫「秋季发布会」，并部署。"
                }
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
                  正在调用工具…
                </span>
              </div>
            ) : null}
          </div>

          <div className="composer">
            <textarea
              className="textarea"
              rows={1}
              placeholder="描述你想完成的事，例如：创建全栈管理台并部署"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) run();
              }}
            />
            <button className="btn btn-accent" onClick={run} disabled={running || !prompt.trim()}>
              <Icon name="play" size={13} /> 运行
            </button>
          </div>
        </section>
      </div>

      {active ? (
        <section className="card card-pad" style={{ marginTop: 16 }}>
          <div className="row" style={{ gap: 14, flexWrap: "wrap" }}>
            <div className="stack" style={{ gap: 4 }}>
              <span className="muted" style={{ fontSize: 10.5, letterSpacing: "0.1em", textTransform: "uppercase" }}>
                绑定 MCP
              </span>
              <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                {(active.mcp_servers ?? []).length ? (
                  active.mcp_servers!.map((name) => (
                    <span className="tag" key={name}>
                      <Icon name="plug" size={10} /> {name}
                    </span>
                  ))
                ) : (
                  <span className="muted" style={{ fontSize: 11.5 }}>
                    未绑定
                  </span>
                )}
              </div>
            </div>
            <div className="stack" style={{ gap: 4 }}>
              <span className="muted" style={{ fontSize: 10.5, letterSpacing: "0.1em", textTransform: "uppercase" }}>
                绑定 Skills
              </span>
              <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                {(active.skills ?? []).length ? (
                  active.skills!.map((name) => (
                    <span className="tag" key={name}>
                      <Icon name="puzzle" size={10} /> {name}
                    </span>
                  ))
                ) : (
                  <span className="muted" style={{ fontSize: 11.5 }}>
                    未绑定
                  </span>
                )}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {creating ? (
        <div className="overlay" onClick={(event) => event.target === event.currentTarget && setCreating(false)}>
          <div className="modal" role="dialog" aria-modal="true">
            <div className="modal-head">
              <Icon name="cpu" size={15} />
              <span className="card-title">新建 Agent</span>
              <div className="spacer" />
              <button className="icon-btn" onClick={() => setCreating(false)}>
                <Icon name="close" size={14} />
              </button>
            </div>
            <div className="modal-body stack" style={{ gap: 12 }}>
              <div className="grid grid-2" style={{ gap: 10 }}>
                <div className="field">
                  <label>Key</label>
                  <input
                    className="input mono"
                    placeholder="release-bot"
                    value={form.key}
                    onChange={(event) => setForm({ ...form, key: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label>名称</label>
                  <input
                    className="input"
                    placeholder="发布机器人"
                    value={form.name}
                    onChange={(event) => setForm({ ...form, name: event.target.value })}
                  />
                </div>
              </div>
              <div className="grid grid-2" style={{ gap: 10 }}>
                <div className="field">
                  <label>模型</label>
                  <input
                    className="input mono"
                    placeholder="workbuddy-flash"
                    value={form.model}
                    onChange={(event) => setForm({ ...form, model: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label>权限范围</label>
                  <select
                    className="select"
                    value={form.scope}
                    onChange={(event) => setForm({ ...form, scope: event.target.value })}
                  >
                    <option value="readonly">readonly（只读巡检）</option>
                    <option value="project">project（可改项目）</option>
                    <option value="full">full（可创建 / 删除）</option>
                  </select>
                </div>
              </div>
            </div>
            <div className="modal-foot">
              <button className="btn" onClick={() => setCreating(false)}>
                取消
              </button>
              <button className="btn btn-accent" onClick={createAgent} disabled={!form.key || !form.name}>
                创建
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </main>
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
