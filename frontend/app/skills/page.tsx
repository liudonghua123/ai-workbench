"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Skill } from "@/lib/types";

export default function SkillsPage() {
  const { push } = useToast();
  const { t } = useI18n();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    api
      .skills()
      .then(setSkills)
      .catch((err) => push(err.message, "err"));

  useEffect(() => {
    load();
  }, []);

  const toggle = async (skill: Skill) => {
    try {
      const updated = await api.toggleSkill(skill.id);
      setSkills((prev) => prev?.map((item) => (item.id === updated.id ? updated : item)) ?? null);
      if (updated.enabled && updated.source === "path") {
        push(t("skill.toggledPath", { name: updated.name }));
      } else {
        push(
          t("skill.toggled", {
            name: updated.name,
            state: updated.enabled ? t("common.enabled") : t("common.disabled"),
          }),
        );
      }
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

              <div
                className="card-body row"
                style={{ borderTop: "1px solid var(--line)", paddingTop: 11 }}
              >
                <Pill tone={skill.enabled ? "ok" : "idle"}>
                  {skill.enabled ? t("common.enabled") : t("common.disabled")}
                </Pill>
                <div className="spacer" />
                <button
                  className={cx("btn btn-sm", skill.enabled ? "" : "btn-accent")}
                  onClick={() => toggle(skill)}
                >
                  <Icon name={skill.enabled ? "stop" : "play"} size={11} />
                  {skill.enabled ? t("common.disable") : t("common.enable")}
                </button>
              </div>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
