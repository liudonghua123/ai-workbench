"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { Icon } from "@/components/Icon";
import { CopyButton, Empty, Skeleton, StatusPill, Tabs, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { DEPLOY_STATUS, PROJECT_STATUS, TYPE_ICON, TYPE_LABEL, cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import type { Deployment, EnvVar, Project } from "@/lib/types";
import { DataTab, DeployTab, OverviewTab, SettingsTab } from "./tabs";
import { CodeTab, ConventionsTab, PreviewTab, VersionTab } from "./workspace";

type TabKey =
  | "overview"
  | "code"
  | "preview"
  | "version"
  | "deploy"
  | "data"
  | "conventions"
  | "settings";

const ACTIVE = new Set(["queued", "building", "deploying"]);

export default function ProjectDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { push } = useToast();
  const { t } = useI18n();
  const { can } = useSession();
  const projectId = params?.id as string;

  const [project, setProject] = useState<Project | null>(null);
  const [env, setEnv] = useState<EnvVar[]>([]);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [ide, setIde] = useState<{
    vscode_url: string;
    available: boolean;
    repo_path: string;
    note: string;
  } | null>(null);
  const [tab, setTab] = useState<TabKey>("overview");
  const [error, setError] = useState("");
  const [deploying, setDeploying] = useState(false);

  const load = useCallback(async () => {
    try {
      const [proj, envs, deps] = await Promise.all([
        api.project(projectId),
        api.env(projectId),
        api.deployments(projectId),
      ]);
      setProject(proj);
      setEnv(envs);
      setDeployments(deps);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("detail.loadFailed"));
    }
  }, [projectId, t]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api.ide(projectId).then(setIde).catch(() => undefined);
  }, [projectId]);

  const latest = deployments[0] ?? project?.deployment ?? null;
  const busy = latest ? ACTIVE.has(latest.status) : false;

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(async () => {
      const deps = await api.deployments(projectId).catch(() => null);
      if (deps) setDeployments(deps);
    }, 1400);
    return () => clearInterval(timer);
  }, [busy, projectId]);

  const deploy = async () => {
    setDeploying(true);
    try {
      const dep = await api.deploy(projectId);
      push(t("detail.deploySubmitted", { version: dep.version }));
      setTab("deploy");
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : t("detail.deployFailed"), "err");
    } finally {
      setDeploying(false);
    }
  };

  const remove = async () => {
    if (!project) return;
    if (!window.confirm(t("detail.deleteConfirm", { name: project.name }))) return;
    try {
      await api.deleteProject(project.id);
      push(t("detail.deleted"));
      router.push("/projects");
    } catch (err) {
      push(err instanceof Error ? err.message : t("detail.deleteFailed"), "err");
    }
  };

  const tabs = useMemo(
    () => [
      { key: "overview" as const, label: t("detail.tabOverview") },
      { key: "code" as const, label: t("detail.tabCode") },
      { key: "preview" as const, label: t("detail.tabPreview") },
      { key: "version" as const, label: t("detail.tabVersion") },
      { key: "deploy" as const, label: t("detail.tabDeploy"), count: deployments.length },
      { key: "data" as const, label: t("detail.tabData") },
      { key: "conventions" as const, label: t("detail.tabConventions") },
      { key: "settings" as const, label: t("detail.tabSettings") },
    ],
    [deployments.length, t],
  );

  if (error) {
    return (
      <main className="content">
        <div className="card">
          <Empty
            icon="close"
            title={t("detail.cannotLoad")}
            hint={error}
            action={
              <Link href="/projects" className="btn btn-sm">
                {t("common.backToProjects")}
              </Link>
            }
          />
        </div>
      </main>
    );
  }

  if (!project) {
    return (
      <main className="content">
        <div className="stack">
          <Skeleton height={40} width={320} />
          <Skeleton height={120} radius={13} />
          <Skeleton height={320} radius={13} />
        </div>
      </main>
    );
  }

  const typeKey = TYPE_LABEL[project.type];

  return (
    <main className="content">
      <div className="page-head" style={{ alignItems: "flex-start" }}>
        <span className="project-glyph" style={{ width: 40, height: 40, borderRadius: 11 }}>
          <Icon name={TYPE_ICON[project.type] ?? "layers"} size={19} />
        </span>
        <div className="page-head-main">
          <div className="row" style={{ gap: 8 }}>
            <h1 className="page-title" style={{ fontSize: 22 }}>
              {project.name}
            </h1>
            <StatusPill map={PROJECT_STATUS} value={project.status} />
            {latest ? <StatusPill map={DEPLOY_STATUS} value={latest.status} /> : null}
          </div>
          <p className="page-desc" style={{ marginTop: 4 }}>
            {project.description || t("detail.noDescription")}
          </p>
          <div className="row" style={{ gap: 6, marginTop: 8, flexWrap: "wrap" }}>
            <span className="tag">{typeKey ? t(typeKey) : project.type}</span>
            <span className="tag">{project.template_key}</span>
            {project.git_initialized ? <span className="tag">git</span> : null}
            {project.owner_name ? (
              <span className="tag" title={t("proj.owner")}>
                <Icon name="user" size={10} /> {project.owner_name}
              </span>
            ) : null}
          </div>
          <div className="addr-row">
            <Address
              label={t("detail.prodUrl")}
              url={project.url}
              note={isPortFree(project.url) ? t("detail.portFree") : ""}
            />
            <Address
              label={t("detail.previewUrl")}
              url={project.preview.url}
              note={project.preview.status === "running" ? "" : t("detail.previewStopped")}
              muted={project.preview.status !== "running"}
            />
          </div>
        </div>
        <div className="spacer" />
        <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
          <a className="btn" href={project.url} target="_blank" rel="noreferrer" title={project.url}>
            <Icon name="external" size={13} /> {t("detail.openApp")}
          </a>
          <span style={{ display: "inline-flex" }}>
            <CopyButton value={project.url} label={t("common.copyUrl")} />
          </span>
          {can("deploy:run") ? (
            <button className="btn btn-accent" onClick={deploy} disabled={deploying || busy}>
              <Icon name={busy ? "refresh" : "rocket"} size={13} />
              {busy ? t("detail.deploying") : deploying ? t("detail.submitting") : t("detail.deploy")}
            </button>
          ) : null}
        </div>
      </div>

      <Tabs tabs={tabs} value={tab} onChange={setTab} />

      {tab === "overview" ? (
        <OverviewTab
          project={project}
          deployments={deployments}
          env={env}
          onDeploy={deploy}
          busy={busy}
        />
      ) : null}
      {tab === "code" ? <CodeTab project={project} ide={ide} /> : null}
      {tab === "preview" ? <PreviewTab project={project} onChanged={load} /> : null}
      {tab === "version" ? <VersionTab project={project} onChanged={load} /> : null}
      {tab === "deploy" ? (
        <DeployTab
          project={project}
          deployments={deployments}
          onRefresh={load}
          onDeploy={deploy}
          busy={busy}
        />
      ) : null}
      {tab === "data" ? <DataTab project={project} /> : null}
      {tab === "conventions" ? <ConventionsTab project={project} /> : null}
      {tab === "settings" ? (
        <SettingsTab
          project={project}
          env={env}
          onEnvChange={setEnv}
          onDelete={remove}
          onRefresh={load}
        />
      ) : null}

      <div className={cx("row", "muted")} style={{ marginTop: 20, gap: 6, fontSize: 11.5 }}>
        <Icon name="folder" size={12} />
        <span className="mono">{project.repo_path}</span>
      </div>
    </main>
  );
}

/* --------------------------------------------------------------------------
   Address strip — the two URLs the platform hands out for a project. They are
   shown as bare hosts on purpose: with the built-in gateway there is no port to
   read, and seeing the same host twice (differing only by suffix) is what makes
   "preview vs production" obvious at a glance.
   -------------------------------------------------------------------------- */

function isPortFree(url: string): boolean {
  try {
    return !new URL(url).port;
  } catch {
    return false;
  }
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function Address({
  label,
  url,
  note,
  muted,
}: {
  label: string;
  url: string;
  note?: string;
  muted?: boolean;
}) {
  const { t } = useI18n();
  if (!url) return null;
  return (
    <span className={cx("addr", muted && "is-muted")} title={url}>
      <span className="addr-label">{label}</span>
      <a className="addr-host mono" href={url} target="_blank" rel="noreferrer">
        {hostOf(url)}
      </a>
      {note ? <span className="addr-note">{note}</span> : null}
      <CopyButton value={url} label={t("common.copyUrl")} />
    </span>
  );
}
