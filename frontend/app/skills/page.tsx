"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { cx } from "@/lib/format";
import type { Skill } from "@/lib/types";

export default function SkillsPage() {
  const { push } = useToast();
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
        push(`${updated.name} 已启用（可被 Agent 调用）`);
      } else {
        push(`${updated.name} ${updated.enabled ? "已启用" : "已停用"}`);
      }
    } catch (err) {
      push(err instanceof Error ? err.message : "操作失败", "err");
    }
  };

  const scan = async () => {
    setBusy(true);
    try {
      const result = await api.scanSkills();
      push(`扫描到 ${result.scanned} 个 skill · ${result.root}`);
      await load();
    } catch (err) {
      push(err instanceof Error ? err.message : "扫描失败", "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">Skills</h1>
          <p className="page-desc">
            可复用的能力包：描述何时使用、需要哪些工具。扫描 <code className="mono">data/skills/*/SKILL.md</code>{" "}
            即可热加载，Agent 绑定后按需调用。
          </p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={scan} disabled={busy}>
          <Icon name={busy ? "refresh" : "search"} size={13} />
          {busy ? "扫描中…" : "扫描目录"}
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
            title="还没有 Skill"
            hint="在 data/skills 下创建 <name>/SKILL.md，再点击扫描目录。"
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
                  {skill.description || "暂无描述"}
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
                      未声明工具
                    </span>
                  )}
                </div>
              </div>

              <div className="card-body row" style={{ borderTop: "1px solid var(--line)", paddingTop: 11 }}>
                <Pill tone={skill.enabled ? "ok" : "idle"}>{skill.enabled ? "已启用" : "已停用"}</Pill>
                <div className="spacer" />
                <button
                  className={cx("btn btn-sm", skill.enabled ? "" : "btn-accent")}
                  onClick={() => toggle(skill)}
                >
                  <Icon name={skill.enabled ? "stop" : "play"} size={11} />
                  {skill.enabled ? "停用" : "启用"}
                </button>
              </div>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
