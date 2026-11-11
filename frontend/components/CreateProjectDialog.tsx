"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Icon } from "./Icon";
import { Pill, useToast } from "./ui";
import { api } from "@/lib/api";
import { RESOURCE_LABEL, cx, TYPE_ICON, TYPE_LABEL } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { Template } from "@/lib/types";

export function CreateProjectDialog({
  open,
  onClose,
  presetTemplate,
}: {
  open: boolean;
  onClose: () => void;
  presetTemplate?: string;
}) {
  const router = useRouter();
  const { push } = useToast();
  const { t } = useI18n();
  const [templates, setTemplates] = useState<Template[]>([]);
  const [selected, setSelected] = useState<string>(presetTemplate ?? "fullstack-fastapi-nextjs");
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [variables, setVariables] = useState<Record<string, string>>({});
  const [deploy, setDeploy] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setError("");
    api
      .templates()
      .then((data) => {
        setTemplates(data);
        const key = presetTemplate && data.some((t) => t.key === presetTemplate)
          ? presetTemplate
          : data[0]?.key;
        if (key) setSelected(key);
      })
      .catch((err) => setError(err.message));
  }, [open, presetTemplate]);

  const template = useMemo(
    () => templates.find((item) => item.key === selected) ?? null,
    [templates, selected],
  );

  useEffect(() => {
    if (!template) return;
    const next: Record<string, string> = {};
    template.variables.forEach((variable) => {
      next[variable.key] = variable.default ?? "";
    });
    setVariables(next);
  }, [template]);

  if (!open) return null;
  if (typeof document === "undefined") return null;

  const submit = async () => {
    if (!name.trim()) {
      setError(t("dlg.nameRequired"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const project = await api.createProject({
        name: name.trim(),
        slug: slug.trim() || undefined,
        template_key: selected,
        description: description.trim(),
        variables,
        deploy,
      });
      push(t("dlg.created", { name: project.name, url: project.url }));
      onClose();
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("dlg.createFailed"));
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal modal-lg" role="dialog" aria-modal="true" aria-label={t("dlg.title")}>
        <div className="modal-head">
          <span className="brand-mark" style={{ width: 26, height: 26, borderRadius: 8 }}>
            <Icon name="plus" size={14} />
          </span>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{t("dlg.title")}</div>
            <div className="muted" style={{ fontSize: 11.5 }}>
              {t("dlg.subtitle")}
            </div>
          </div>
          <div className="spacer" />
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
            <Icon name="close" size={15} />
          </button>
        </div>

        <div className="modal-body stack" style={{ gap: 16 }}>
          <div className="field">
            <label>{t("dlg.template")}</label>
            <div className="tpl-grid">
              {templates.length === 0 ? (
                <div className="muted" style={{ fontSize: 12 }}>
                  {t("dlg.loadingTemplates")}
                </div>
              ) : (
                templates.map((item) => (
                  <button
                    key={item.key}
                    className={cx("tpl", item.key === selected && "active")}
                    onClick={() => setSelected(item.key)}
                    type="button"
                  >
                    <div className="tpl-name">
                      <Icon name={TYPE_ICON[item.type] ?? "grid"} size={14} />
                      {item.name}
                      {item.tags.includes("recommended") ? (
                        <span style={{ marginLeft: "auto" }}>
                          <Pill tone="accent">{t("common.recommended")}</Pill>
                        </span>
                      ) : null}
                    </div>
                    <div className="tpl-desc">{item.description}</div>
                    <div className="row" style={{ marginTop: 8, gap: 5, flexWrap: "wrap" }}>
                      <span className="tag">{t(TYPE_LABEL[item.type])}</span>
                      <span className="tag">{t("common.files", { n: item.file_count })}</span>
                      {item.resources.map((resource) => {
                        const labelKey = RESOURCE_LABEL[resource];
                        return (
                          <span className="tag" key={resource}>
                            {labelKey ? t(labelKey) : resource}
                          </span>
                        );
                      })}
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>

          <div className="grid grid-2" style={{ gap: 12 }}>
            <div className="field">
              <label htmlFor="p-name">{t("dlg.name")}</label>
              <input
                id="p-name"
                className="input"
                placeholder={t("dlg.namePlaceholder")}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="p-slug">{t("dlg.slug")}</label>
              <input
                id="p-slug"
                className="input"
                placeholder="customer-success"
                value={slug}
                onChange={(event) => setSlug(event.target.value)}
              />
              <span className="hint">
                {t("dlg.finalDomainPrefix")}{" "}
                <code className="mono">{(slug || "slug") + ".localtest.me"}</code>
              </span>
            </div>
          </div>

          <div className="field">
            <label htmlFor="p-desc">{t("dlg.description")}</label>
            <textarea
              id="p-desc"
              className="textarea"
              rows={2}
              placeholder={t("dlg.descriptionPlaceholder")}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>

          {template?.variables.length ? (
            <div className="grid grid-2" style={{ gap: 12 }}>
              {template.variables.map((variable) => (
                <div className="field" key={variable.key}>
                  <label htmlFor={`v-${variable.key}`}>{variable.label}</label>
                  <input
                    id={`v-${variable.key}`}
                    className="input"
                    value={variables[variable.key] ?? ""}
                    onChange={(event) =>
                      setVariables((prev) => ({ ...prev, [variable.key]: event.target.value }))
                    }
                  />
                  {variable.help ? <span className="hint">{variable.help}</span> : null}
                </div>
              ))}
            </div>
          ) : null}

          <label className="row" style={{ gap: 9, cursor: "pointer", fontSize: 12.5 }}>
            <input
              type="checkbox"
              checked={deploy}
              onChange={(event) => setDeploy(event.target.checked)}
              style={{ accentColor: "var(--accent)" }}
            />
            {t("dlg.deployNow")}
          </label>

          {error ? (
            <div className="pill pill-err" style={{ height: "auto", padding: "8px 10px", fontSize: 12 }}>
              {error}
            </div>
          ) : null}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-accent" onClick={submit} disabled={busy || !name.trim()}>
            {busy ? (
              <>
                <Icon name="refresh" size={14} />
                {t("dlg.submitting")}
              </>
            ) : (
              <>
                <Icon name="rocket" size={14} />
                {t("dlg.submit")}
              </>
            )}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}