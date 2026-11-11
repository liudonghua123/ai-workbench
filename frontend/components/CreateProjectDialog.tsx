"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useRouter } from "next/navigation";
import { Icon } from "./Icon";
import { Pill, useToast } from "./ui";
import { api } from "@/lib/api";
import { cx, TYPE_ICON, TYPE_LABEL } from "@/lib/format";
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
      setError("请填写应用名称");
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
      push(`已创建 ${project.name} · ${project.url}`);
      onClose();
      router.push(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败");
    } finally {
      setBusy(false);
    }
  };

  return createPortal(
    <div className="overlay" onClick={(event) => event.target === event.currentTarget && onClose()}>
      <div className="modal modal-lg" role="dialog" aria-modal="true" aria-label="创建应用">
        <div className="modal-head">
          <span className="brand-mark" style={{ width: 26, height: 26, borderRadius: 8 }}>
            <Icon name="plus" size={14} />
          </span>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>创建应用</div>
            <div className="muted" style={{ fontSize: 11.5 }}>
              选择模板 → 自动装配存储、数据库与域名
            </div>
          </div>
          <div className="spacer" />
          <button className="icon-btn" onClick={onClose} aria-label="关闭">
            <Icon name="close" size={15} />
          </button>
        </div>

        <div className="modal-body stack" style={{ gap: 16 }}>
          <div className="field">
            <label>模板</label>
            <div className="tpl-grid">
              {templates.length === 0 ? (
                <div className="muted" style={{ fontSize: 12 }}>
                  正在读取模板…
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
                          <Pill tone="accent">推荐</Pill>
                        </span>
                      ) : null}
                    </div>
                    <div className="tpl-desc">{item.description}</div>
                    <div className="row" style={{ marginTop: 8, gap: 5, flexWrap: "wrap" }}>
                      <span className="tag">{TYPE_LABEL[item.type]}</span>
                      <span className="tag">{item.file_count} files</span>
                      {item.resources.map((resource) => (
                        <span className="tag" key={resource}>
                          {resource === "storage" ? "对象存储" : "数据库"}
                        </span>
                      ))}
                    </div>
                  </button>
                ))
              )}
            </div>
          </div>

          <div className="grid grid-2" style={{ gap: 12 }}>
            <div className="field">
              <label htmlFor="p-name">应用名称</label>
              <input
                id="p-name"
                className="input"
                placeholder="例如：客户成功控制台"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor="p-slug">子域名前缀（可选）</label>
              <input
                id="p-slug"
                className="input"
                placeholder="customer-success"
                value={slug}
                onChange={(event) => setSlug(event.target.value)}
              />
              <span className="hint">
                最终域名 <code className="mono">{(slug || "slug") + ".localtest.me"}</code>
              </span>
            </div>
          </div>

          <div className="field">
            <label htmlFor="p-desc">描述</label>
            <textarea
              id="p-desc"
              className="textarea"
              rows={2}
              placeholder="一句话说明这个应用做什么，会写入 README 与项目元数据"
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
            创建后立即部署（写入部署记录并执行健康检查）
          </label>

          {error ? (
            <div className="pill pill-err" style={{ height: "auto", padding: "8px 10px", fontSize: 12 }}>
              {error}
            </div>
          ) : null}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="btn btn-accent" onClick={submit} disabled={busy || !name.trim()}>
            {busy ? (
              <>
                <Icon name="refresh" size={14} />
                正在装配…
              </>
            ) : (
              <>
                <Icon name="rocket" size={14} />
                创建应用
              </>
            )}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}