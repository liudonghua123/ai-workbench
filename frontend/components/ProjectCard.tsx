"use client";

import Link from "next/link";
import { Icon } from "./Icon";
import { CopyButton, Pill, StatusPill } from "./ui";
import { PROJECT_STATUS, TYPE_ICON, TYPE_LABEL, clockTime, relativeTime } from "@/lib/format";
import type { Project } from "@/lib/types";

export function ProjectCard({ project, compact }: { project: Project; compact?: boolean }) {
  const deploy = project.deployment;
  return (
    <Link href={`/projects/${project.id}`} className="project-card">
      <div className="project-top">
        <span className="project-glyph">
          <Icon name={TYPE_ICON[project.type] ?? "layers"} size={15} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="project-name truncate">{project.name}</div>
          <div className="muted mono" style={{ fontSize: 11 }}>
            {project.slug} · {TYPE_LABEL[project.type] ?? project.type}
          </div>
        </div>
        <StatusPill map={PROJECT_STATUS} value={project.status} />
      </div>

      {!compact ? <p className="project-desc">{project.description || "暂无描述"}</p> : null}

      <div className="project-url">
        <Icon name="globe" size={12} />
        <span className="truncate">{project.domains[0]?.fqdn ?? project.url ?? "—"}</span>
        <span style={{ marginLeft: "auto", color: "var(--ink-4)" }}>:{project.port ?? "—"}</span>
      </div>

      <div className="project-meta">
        {project.resources.map((resource) => (
          <span className="tag" key={resource.id}>
            {resource.kind === "storage" ? "storage" : "db"}:{resource.name.length > 16 ? `${resource.name.slice(0, 15)}…` : resource.name}
          </span>
        ))}
        {project.resources.length === 0 ? <span className="tag">无资源</span> : null}
      </div>

      <div className="project-foot">
        {deploy ? (
          <Pill tone={deploy.status === "running" ? "ok" : deploy.status === "failed" ? "err" : "warn"} live={deploy.status === "running"}>
            {deploy.version} · {deploy.status === "running" ? "运行中" : deploy.status === "failed" ? "部署失败" : "部署中"}
          </Pill>
        ) : (
          <Pill tone="idle">未部署</Pill>
        )}
        <span className="muted" style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4 }}>
          <Icon name="clock" size={11} />
          {relativeTime(project.updated_at)}
        </span>
        <div className="spacer" />
        <span className="muted" style={{ fontSize: 11 }}>
          {clockTime(project.created_at)}
        </span>
        <span onClick={(event) => event.preventDefault()}>
          <CopyButton value={project.url} label="复制访问地址" />
        </span>
      </div>
    </Link>
  );
}
