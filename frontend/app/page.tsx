"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Icon } from "@/components/Icon";
import { ProjectCard } from "@/components/ProjectCard";
import { Empty, LoadingRows, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { eventTone, relativeTime } from "@/lib/format";
import type { AgentRun, Overview, Project } from "@/lib/types";

export default function DashboardPage() {
  const router = useRouter();
  const { push } = useToast();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState("");
  const [running, setRunning] = useState(false);
  const [run, setRun] = useState<AgentRun | null>(null);

  useEffect(() => {
    Promise.all([api.overview(), api.projects()])
      .then(([data, list]) => {
        setOverview(data);
        setProjects(list);
      })
      .catch((err) => setError(err.message));
  }, []);

  const askAgent = async () => {
    if (!prompt.trim()) return;
    setRunning(true);
    setRun(null);
    try {
      const result = await api.runAgent("app-architect", prompt.trim());
      setRun(result);
      push(result.status === "succeeded" ? "Agent 已完成执行" : "Agent 执行出现问题", result.status === "succeeded" ? "ok" : "err");
      const list = await api.projects();
      setProjects(list);
      const fresh = list.find((project) => recentCreated(result, project));
      if (fresh) router.prefetch(`/projects/${fresh.id}`);
    } catch (err) {
      push(err instanceof Error ? err.message : "Agent 执行失败", "err");
    } finally {
      setRunning(false);
    }
  };

  const counts = overview?.counts;

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">工作台总览</h1>
          <p className="page-desc">
            创建项目即自动装配对象存储、数据库与 <code className="mono">&lt;slug&gt;.localtest.me</code>{" "}
            域名；同一套 API 通过 MCP 暴露给 Agent，人能点的，Agent 也能调。
          </p>
        </div>
        <div className="spacer" />
        <div className="row" style={{ gap: 6 }}>
          {overview ? (
            <>
              <Pill tone="accent" icon="plug">
                MCP {overview.mcp_endpoint}
              </Pill>
              <Pill tone="idle" icon="route">
                deploy: {overview.deploy_mode}
              </Pill>
            </>
          ) : (
            <Skeleton width={180} height={21} radius={999} />
          )}
        </div>
      </div>

      {error ? (
        <div className="card card-pad" style={{ borderColor: "var(--err)", marginBottom: 16 }}>
          <div className="row" style={{ color: "var(--err)", fontWeight: 600 }}>
            <Icon name="close" size={15} /> 无法连接后端 API
          </div>
          <p className="muted" style={{ fontSize: 12.5, margin: "6px 0 0" }}>
            {error} — 请确认后端已在 <code className="mono">http://127.0.0.1:8000</code> 运行。
          </p>
        </div>
      ) : null}

      <section className="grid grid-4" style={{ marginBottom: 16 }}>
        <Kpi
          label="项目"
          value={counts?.projects}
          foot={`${counts?.projects_ready ?? 0} 个就绪`}
          icon="layers"
        />
        <Kpi
          label="运行中部署"
          value={counts?.deployments_running}
          foot={`累计 ${counts?.deployments ?? 0} 次 · 失败 ${counts?.deployments_failed ?? 0}`}
          icon="rocket"
          tone="ok"
        />
        <Kpi
          label="Agents"
          value={counts?.agents}
          foot={`默认模型 ${overview?.providers ? "workbuddy-flash" : "—"}`}
          icon="cpu"
        />
        <Kpi
          label="MCP / Skills"
          value={counts ? `${counts.mcp_servers} / ${counts.skills}` : undefined}
          foot={`${counts?.skills_enabled ?? 0} 个 skill 已启用`}
          icon="plug"
        />
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <Icon name="settings" size={14} />
          <span className="card-title">资源能力（Provider 可插拔）</span>
          <div className="spacer" />
          <span className="muted" style={{ fontSize: 11.5 }}>
            改一个环境变量即可换成真实云
          </span>
        </div>
        <div className="card-body" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {overview ? (
            <>
              {(
                [
                  ["storage", "对象存储", "cloud"],
                  ["database", "云数据库", "database"],
                  ["domain", "域名", "globe"],
                  ["deploy", "部署", "rocket"],
                ] as const
              ).map(([key, label, icon]) => (
                <span
                  key={key}
                  className="tag"
                  style={{ height: 28, padding: "0 10px", gap: 6, fontSize: 11.5 }}
                >
                  <Icon name={icon} size={12} />
                  {label}
                  <b style={{ color: "var(--accent-ink)", fontWeight: 600 }}>
                    {overview.providers[key]}
                  </b>
                </span>
              ))}
              <span className="tag" style={{ height: 28, padding: "0 10px", gap: 6, fontSize: 11.5 }}>
                <Icon name="globe" size={12} />
                域名后缀
                <b style={{ color: "var(--accent-ink)", fontWeight: 600 }}>
                  {overview.domains.suffix}
                </b>
              </span>
            </>
          ) : (
            <LoadingRows rows={1} />
          )}
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.55fr) minmax(0,1fr)", gap: 16, alignItems: "start" }}>
        <section>
          <div className="row-between" style={{ marginBottom: 10 }}>
            <div className="row" style={{ gap: 8 }}>
              <h2 style={{ fontSize: 13.5, fontWeight: 600, margin: 0 }}>最近项目</h2>
              <span className="nav-count">{projects?.length ?? 0}</span>
            </div>
            <Link href="/projects" className="link" style={{ fontSize: 12 }}>
              全部项目 <Icon name="arrow-right" size={12} />
            </Link>
          </div>
          {projects === null ? (
            <div className="stack">
              <Skeleton height={132} radius={13} />
              <Skeleton height={132} radius={13} />
            </div>
          ) : projects.length === 0 ? (
            <div className="card">
              <Empty
                icon="layers"
                title="还没有项目"
                hint="从模板创建第一个应用，或用右侧的 Agent 用一句话生成。"
              />
            </div>
          ) : (
            <div className="stack" style={{ gap: 12 }}>
              {projects.slice(0, 3).map((project) => (
                <ProjectCard key={project.id} project={project} />
              ))}
            </div>
          )}
        </section>

        <section className="stack" style={{ gap: 16 }}>
          <div className="card">
            <div className="card-head">
              <span className="agent-avatar" style={{ width: 22, height: 22, borderRadius: 6 }}>
                <Icon name="spark" size={12} />
              </span>
              <span className="card-title">交给 Agent</span>
              <div className="spacer" />
              <Pill tone="accent">app-architect</Pill>
            </div>
            <div className="card-body stack" style={{ gap: 10 }}>
              <textarea
                className="textarea"
                rows={3}
                placeholder="例如：帮我创建一个纯前端的活动落地页，叫「秋季发布会」"
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
              />
              <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                {["建一个后端 API 服务", "创建全栈管理台并部署"].map((sample) => (
                  <button key={sample} className="btn btn-sm btn-ghost" onClick={() => setPrompt(sample)}>
                    {sample}
                  </button>
                ))}
              </div>
              <button className="btn btn-accent" onClick={askAgent} disabled={running || !prompt.trim()}>
                <Icon name={running ? "refresh" : "play"} size={13} />
                {running ? "Agent 执行中…" : "运行 Agent"}
              </button>

              {run ? (
                <div className="stack" style={{ gap: 8 }}>
                  <div className="steps" style={{ marginTop: 0, paddingTop: 0, borderTop: 0 }}>
                    {run.steps.map((step) => (
                      <div key={step.index} className={`step ${step.ok ? "" : "step-err"}`}>
                        <span className="step-idx">{step.index}</span>
                        <span>
                          <span className="step-tool">{step.tool}</span>{" "}
                          <span className="muted">{step.thought}</span>
                          {step.error ? (
                            <div style={{ color: "var(--err)", marginTop: 2 }}>{step.error}</div>
                          ) : null}
                        </span>
                      </div>
                    ))}
                  </div>
                  <button className="btn btn-sm" onClick={() => router.push("/projects")}>
                    查看项目列表 <Icon name="arrow-right" size={12} />
                  </button>
                </div>
              ) : null}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <Icon name="pulse" size={14} />
              <span className="card-title">事件流</span>
              <div className="spacer" />
              <Link href="/events" className="link" style={{ fontSize: 11.5 }}>
                全部
              </Link>
            </div>
            <div className="card-body" style={{ paddingTop: 6, paddingBottom: 6 }}>
              {overview === null ? (
                <LoadingRows rows={3} />
              ) : overview.recent_events.length === 0 ? (
                <Empty icon="pulse" title="暂无事件" />
              ) : (
                overview.recent_events.slice(0, 7).map((event) => (
                  <div className="event-row" key={event.id}>
                    <span className="event-dot" style={{ background: eventTone(event.kind) }} />
                    <span style={{ minWidth: 0 }}>
                      <span style={{ display: "block" }}>{event.message}</span>
                      <span className="mono muted" style={{ fontSize: 10.5 }}>
                        {event.kind}
                      </span>
                    </span>
                    <span className="event-time">{relativeTime(event.created_at)}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

function Kpi({
  label,
  value,
  foot,
  icon,
  tone,
}: {
  label: string;
  value?: number | string;
  foot: string;
  icon: "layers" | "rocket" | "cpu" | "plug";
  tone?: "ok";
}) {
  return (
    <div className="card kpi">
      <div className="kpi-label">
        <Icon name={icon} size={12} />
        {label}
      </div>
      <div className="kpi-value">{value ?? "—"}</div>
      <div className="kpi-foot">
        {tone === "ok" ? <span style={{ color: "var(--ok)" }}>●</span> : null}
        {foot}
      </div>
      <svg className="kpi-spark" width="46" height="20" viewBox="0 0 46 20" fill="none">
        <path
          d="M1 15c5 0 6-7 11-7s5 5 10 5 5-9 10-9 6 4 13 3"
          stroke="var(--accent)"
          strokeWidth="1.3"
          strokeLinecap="round"
        />
      </svg>
    </div>
  );
}

function recentCreated(run: AgentRun, project: Project): boolean {
  const step = run.steps.find((item) => item.tool === "create_project" && item.ok);
  if (!step) return false;
  return step.result_preview.includes(project.id);
}
