"use client";

import Link from "next/link";
import { Icon } from "./Icon";
import { CopyButton, Pill, StatusPill } from "./ui";
import { PROJECT_STATUS, TYPE_ICON, TYPE_LABEL, clockTime, relativeTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import type { Project } from "@/lib/types";

export function ProjectCard({ project, compact }: { project: Project; compact?: boolean }) {
  const { t, locale } = useI18n();
  const { me, seesAll } = useSession();
  const deploy = project.deployment;
  const typeKey = TYPE_LABEL[project.type];
  // Only meaningful for a narrowed scope: with scope=all every card is somebody
  // in the org's project, and labelling all of them would be noise.
  const shared =
    !seesAll && !!project.owner_id && project.owner_id !== me?.user?.id ? project.owner_name : "";

  return (
    <Link href={`/projects/${project.id}`} className="project-card">
      <div className="project-top">
        <span className="project-glyph">
          <Icon name={TYPE_ICON[project.type] ?? "layers"} size={15} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="project-name truncate">{project.name}</div>
          <div className="muted mono" style={{ fontSize: 11 }}>
            {project.slug} · {typeKey ? t(typeKey) : project.type}
          </div>
        </div>
        <StatusPill map={PROJECT_STATUS} value={project.status} />
      </div>

      {!compact ? (
        <p className="project-desc">{project.description || t("common.noDescription")}</p>
      ) : null}

      <div className="project-url">
        <Icon name="globe" size={12} />
        <span className="truncate">{project.domains[0]?.fqdn ?? project.url ?? "—"}</span>
        <span style={{ marginLeft: "auto", color: "var(--ink-4)" }}>:{project.port ?? "—"}</span>
      </div>

      <div className="project-meta">
        {project.resources.map((resource) => (
          <span className="tag" key={resource.id}>
            {resource.kind === "storage" ? "storage" : "db"}:
            {resource.name.length > 16 ? `${resource.name.slice(0, 15)}…` : resource.name}
          </span>
        ))}
        {project.resources.length === 0 ? <span className="tag">{t("common.noResources")}</span> : null}
        {shared ? (
          <span className="tag" title={t("proj.granted")}>
            <Icon name="user" size={10} /> {shared}
          </span>
        ) : null}
      </div>

      <div className="project-foot">
        {deploy ? (
          <Pill
            tone={deploy.status === "running" ? "ok" : deploy.status === "failed" ? "err" : "warn"}
            live={deploy.status === "running"}
          >
            {deploy.version} ·{" "}
            {deploy.status === "running"
              ? t("status.deploy.running")
              : deploy.status === "failed"
                ? t("status.deploy.failed")
                : t("status.deploy.deploying")}
          </Pill>
        ) : (
          <Pill tone="idle">{t("common.notDeployed")}</Pill>
        )}
        <span className="muted" style={{ fontSize: 11, display: "flex", alignItems: "center", gap: 4 }}>
          <Icon name="clock" size={11} />
          {relativeTime(project.updated_at, locale)}
        </span>
        <div className="spacer" />
        <span className="muted" style={{ fontSize: 11 }}>
          {clockTime(project.created_at, locale)}
        </span>
        <span onClick={(event) => event.preventDefault()}>
          <CopyButton value={project.url} label={t("common.copyUrl")} />
        </span>
      </div>
    </Link>
  );
}
