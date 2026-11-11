"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { TYPE_ICON, TYPE_LABEL } from "@/lib/format";
import type { Template } from "@/lib/types";

export default function TemplatesPage() {
  const { push } = useToast();
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [preset, setPreset] = useState<string | undefined>();
  const [open, setOpen] = useState(false);

  const load = () =>
    api
      .templates()
      .then(setTemplates)
      .catch((err) => push(err.message, "err"));

  useEffect(() => {
    load();
  }, []);

  const reload = async () => {
    try {
      const result = await api.reloadTemplates();
      await load();
      push(`已重新扫描，共 ${result.count} 个模板`);
    } catch (err) {
      push(err instanceof Error ? err.message : "重载失败", "err");
    }
  };

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">模板</h1>
          <p className="page-desc">
            模板是「约定」的载体：声明运行时、资源需求、变量与文件清单，工作台据此装配项目。
            在 <code className="mono">data/templates/&lt;name&gt;/template.yaml</code> 放入目录即可热插拔。
          </p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={reload}>
          <Icon name="refresh" size={13} /> 重新扫描
        </button>
      </div>

      {templates === null ? (
        <div className="grid grid-2">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} height={210} radius={13} />
          ))}
        </div>
      ) : templates.length === 0 ? (
        <div className="card">
          <Empty icon="grid" title="没有可用模板" />
        </div>
      ) : (
        <div className="grid grid-2">
          {templates.map((template) => (
            <section className="card" key={template.key} style={{ display: "flex", flexDirection: "column" }}>
              <div className="card-head">
                <span className="project-glyph" style={{ width: 28, height: 28, borderRadius: 8 }}>
                  <Icon name={TYPE_ICON[template.type] ?? "grid"} size={14} />
                </span>
                <div style={{ minWidth: 0 }}>
                  <div className="card-title truncate">{template.name}</div>
                  <div className="muted mono" style={{ fontSize: 10.5 }}>
                    {template.key} · v{template.version}
                  </div>
                </div>
                <div className="spacer" />
                {template.tags.includes("recommended") ? <Pill tone="accent">推荐</Pill> : null}
                <span className="tag">{TYPE_LABEL[template.type]}</span>
              </div>

              <div className="card-body stack" style={{ gap: 12, flex: 1 }}>
                <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.6 }}>
                  {template.description}
                </p>

                <div className="row" style={{ gap: 5, flexWrap: "wrap" }}>
                  {template.tags
                    .filter((tag) => tag !== "recommended")
                    .map((tag) => (
                      <span className="tag" key={tag}>
                        {tag}
                      </span>
                    ))}
                  <span className="tag">{template.file_count} files</span>
                  {template.resources.map((resource) => (
                    <span className="tag" key={resource}>
                      {resource === "storage" ? "对象存储" : "数据库"}
                    </span>
                  ))}
                </div>

                <div>
                  <div className="muted" style={{ fontSize: 10.5, letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 5 }}>
                    文件清单
                  </div>
                  <div className="mono muted" style={{ fontSize: 11, lineHeight: 1.8 }}>
                    {template.files.slice(0, 5).join("  ·  ")}
                    {template.files.length > 5 ? `  ·  +${template.files.length - 5}` : ""}
                  </div>
                </div>

                {template.run_command ? (
                  <div className="terminal" style={{ padding: "8px 10px", fontSize: 11, maxHeight: 90 }}>
                    $ {template.run_command}
                  </div>
                ) : null}

                {template.notes ? (
                  <div className="muted" style={{ fontSize: 11.5, display: "flex", gap: 6 }}>
                    <Icon name="spark" size={12} />
                    {template.notes}
                  </div>
                ) : null}
              </div>

              <div className="card-body" style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}>
                <button
                  className="btn btn-accent"
                  style={{ width: "100%" }}
                  onClick={() => {
                    setPreset(template.key);
                    setOpen(true);
                  }}
                >
                  <Icon name="plus" size={13} /> 用此模板创建应用
                </button>
              </div>
            </section>
          ))}
        </div>
      )}

      <CreateProjectDialog
        open={open}
        onClose={() => setOpen(false)}
        presetTemplate={preset}
      />
    </main>
  );
}
