"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/Icon";
import { ProjectCard } from "@/components/ProjectCard";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";
import { Empty, Pill, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { useI18n, type MessageKey } from "@/lib/i18n";
import type { Project } from "@/lib/types";

const FILTERS: { key: string; labelKey: MessageKey }[] = [
  { key: "all", labelKey: "common.all" },
  { key: "fullstack", labelKey: "type.fullstack" },
  { key: "frontend", labelKey: "type.frontend" },
  { key: "backend", labelKey: "type.backend" },
  { key: "agent", labelKey: "type.agent" },
];

export default function ProjectsPage() {
  const { t } = useI18n();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");

  const load = () => {
    api
      .projects()
      .then(setProjects)
      .catch((err) => setError(err.message));
  };

  useEffect(load, []);

  const visible = useMemo(() => {
    if (!projects) return [];
    return projects.filter((project) => {
      const matchesType = filter === "all" || project.type === filter;
      const haystack = `${project.name} ${project.slug} ${project.description} ${project.domains
        .map((domain) => domain.fqdn)
        .join(" ")}`.toLowerCase();
      return matchesType && haystack.includes(query.trim().toLowerCase());
    });
  }, [projects, filter, query]);

  const activeFilter = FILTERS.find((item) => item.key === filter);

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("proj.title")}</h1>
          <p className="page-desc">{t("proj.desc")}</p>
        </div>
        <div className="spacer" />
        <div className="row" style={{ gap: 8 }}>
          <div style={{ position: "relative" }}>
            <span style={{ position: "absolute", left: 10, top: 9, color: "var(--ink-3)" }}>
              <Icon name="search" size={14} />
            </span>
            <input
              className="input"
              style={{ paddingLeft: 30, width: 220 }}
              placeholder={t("proj.search")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <button className="btn btn-primary" onClick={() => setOpen(true)}>
            <Icon name="plus" size={14} /> {t("proj.new")}
          </button>
        </div>
      </div>

      <div className="row" style={{ gap: 6, marginBottom: 16, flexWrap: "wrap" }}>
        {FILTERS.map((item) => {
          const count =
            item.key === "all"
              ? projects?.length ?? 0
              : projects?.filter((project) => project.type === item.key).length ?? 0;
          return (
            <button
              key={item.key}
              className={cx("btn btn-sm", filter === item.key ? "btn-primary" : "btn-ghost")}
              onClick={() => setFilter(item.key)}
            >
              {t(item.labelKey)}
              <span className="mono" style={{ opacity: 0.6 }}>
                {count}
              </span>
            </button>
          );
        })}
        <div className="spacer" />
        <Pill tone="idle" icon="layers">
          {t("proj.sort")}
        </Pill>
      </div>

      {error ? (
        <div className="card card-pad" style={{ borderColor: "var(--err)", color: "var(--err)" }}>
          {error}
        </div>
      ) : projects === null ? (
        <div className="project-grid">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} height={196} radius={13} />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <div className="card">
          <Empty
            icon="layers"
            title={t("proj.emptyTitle")}
            hint={t("proj.emptyHint")}
            action={
              <button className="btn btn-sm" onClick={() => setOpen(true)}>
                <Icon name="plus" size={13} /> {t("proj.newApp")}
              </button>
            }
          />
        </div>
      ) : (
        <div className="project-grid">
          {visible.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}

      <CreateProjectDialog
        open={open}
        onClose={() => {
          setOpen(false);
          load();
        }}
      />
      <div style={{ marginTop: 18 }} className="muted">
        <span style={{ fontSize: 11.5 }}>
          {t("proj.summary", {
            n: visible.length,
            type: activeFilter ? t(activeFilter.labelKey) : filter,
          })}
        </span>
      </div>
    </main>
  );
}
