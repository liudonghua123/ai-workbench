"use client";

import { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { CreateProjectDialog } from "@/components/CreateProjectDialog";
import { Empty, Pill, Skeleton, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { RESOURCE_LABEL, TYPE_ICON, TYPE_LABEL } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Template } from "@/lib/types";

export default function TemplatesPage() {
  const { push } = useToast();
  const { t } = useI18n();
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
      push(t("tpl.rescanned", { n: result.count }));
    } catch (err) {
      push(err instanceof Error ? err.message : t("tpl.rescanFailed"), "err");
    }
  };

  const [descBefore, descAfter] = t("tpl.desc").split("{path}");

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("tpl.title")}</h1>
          <p className="page-desc">
            {descBefore}
            <code className="mono">data/templates/&lt;name&gt;/template.yaml</code>
            {descAfter}
          </p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={reload}>
          <Icon name="refresh" size={13} /> {t("tpl.rescan")}
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
          <Empty icon="grid" title={t("tpl.empty")} />
        </div>
      ) : (
        <div className="grid grid-2">
          {templates.map((template) => {
            const typeKey = TYPE_LABEL[template.type];
            return (
              <section
                className="card"
                key={template.key}
                style={{ display: "flex", flexDirection: "column" }}
              >
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
                  {template.tags.includes("recommended") ? (
                    <Pill tone="accent">{t("common.recommended")}</Pill>
                  ) : null}
                  <span className="tag">{typeKey ? t(typeKey) : template.type}</span>
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
                    <span className="tag">{t("common.files", { n: template.file_count })}</span>
                    {template.resources.map((resource) => {
                      const labelKey = RESOURCE_LABEL[resource];
                      return (
                        <span className="tag" key={resource}>
                          {labelKey ? t(labelKey) : resource}
                        </span>
                      );
                    })}
                  </div>

                  <div>
                    <div
                      className="muted"
                      style={{
                        fontSize: 10.5,
                        letterSpacing: "0.1em",
                        textTransform: "uppercase",
                        marginBottom: 5,
                      }}
                    >
                      {t("tpl.manifest")}
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

                <div
                  className="card-body"
                  style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }}
                >
                  <button
                    className="btn btn-accent"
                    style={{ width: "100%" }}
                    onClick={() => {
                      setPreset(template.key);
                      setOpen(true);
                    }}
                  >
                    <Icon name="plus" size={13} /> {t("tpl.use")}
                  </button>
                </div>
              </section>
            );
          })}
        </div>
      )}

      <CreateProjectDialog open={open} onClose={() => setOpen(false)} presetTemplate={preset} />
    </main>
  );
}
