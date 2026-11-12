"use client";

/**
 * The role editor's permission grid — rendered entirely from the catalog the
 * server returned, grouped by domain, with a per-group select-all.
 *
 * Two behaviours worth knowing:
 *
 * * Selecting a checkbox removes a wildcard. Otherwise ticking one box on the
 *   `owner` role would quietly widen it to `*` instead of narrowing it.
 * * The wildcard row is shown read-only: `*` is a property of the owner role,
 *   and letting a custom role carry it would defeat the role editor.
 */

import { useMemo } from "react";

import { Icon } from "@/components/Icon";
import { expandGrants, permissionLabel } from "@/lib/access";
import { cx } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { PermissionCatalog, PermissionDef } from "@/lib/types";

export function PermissionPicker({
  catalog,
  value,
  onChange,
  disabled = false,
}: {
  catalog: PermissionCatalog | null;
  value: string[];
  onChange: (next: string[]) => void;
  disabled?: boolean;
}) {
  const { locale, t } = useI18n();
  const selected = useMemo(() => new Set(value), [value]);
  const wildcard = selected.has("*");
  const resolved = useMemo(() => expandGrants(value, catalog), [value, catalog]);

  if (!catalog) {
    return <div className="hint">{t("common.loading")}</div>;
  }

  const toggle = (key: string) => {
    const next = new Set(selected);
    next.delete("*");
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange([...next]);
  };

  const toggleGroup = (items: PermissionDef[]) => {
    const next = new Set(selected);
    next.delete("*");
    const complete = items.every((item) => next.has(item.key));
    items.forEach((item) => {
      if (complete) next.delete(item.key);
      else next.add(item.key);
    });
    onChange([...next]);
  };

  return (
    <div className={cx("perm-picker", disabled && "is-disabled")}>
      {wildcard ? (
        <div className="perm-wildcard">
          <Icon name="shield" size={14} />
          <span>{t("role.ownerFixed")}</span>
        </div>
      ) : null}

      {catalog.groups.map((group) => {
        const complete = group.items.every((item) => resolved.has(item.key));
        const some = group.items.some((item) => resolved.has(item.key));
        return (
          <section key={group.key} className="perm-group">
            <header className="perm-group-head">
              <span className="perm-group-name">
                {locale === "zh" ? group.label_zh : group.label}
              </span>
              <span className="perm-group-meta">
                {group.items.filter((item) => resolved.has(item.key)).length}/{group.items.length}
              </span>
              {disabled ? null : (
                <button
                  type="button"
                  className="linkbtn"
                  onClick={() => toggleGroup(group.items)}
                  disabled={wildcard}
                >
                  {complete ? t("role.clear") : t("role.selectAll")}
                </button>
              )}
            </header>
            <div className="perm-grid">
              {group.items.map((item) => {
                const on = resolved.has(item.key);
                return (
                  <label
                    key={item.key}
                    className={cx("perm-item", on && "on", some && !complete && "partial")}
                    title={locale === "zh" ? item.description_zh : item.description}
                  >
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={disabled || wildcard}
                      onChange={() => toggle(item.key)}
                    />
                    <span className="perm-item-body">
                      <b>{permissionLabel(catalog, item.key, locale)}</b>
                      <span className="mono">{item.key}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

/** Compact read-only summary used on role cards. */
export function PermissionChips({
  catalog,
  grants,
  limit = 6,
}: {
  catalog: PermissionCatalog | null;
  grants: string[];
  limit?: number;
}) {
  const { locale, t } = useI18n();
  const resolved = useMemo(() => expandGrants(grants, catalog), [grants, catalog]);
  const keys = useMemo(() => [...resolved].sort(), [resolved]);

  if (keys.length === 0) return <span className="muted">—</span>;

  const shown = keys.slice(0, limit);
  return (
    <div className="perm-chips">
      {shown.map((key) => (
        <span key={key} className="chip chip-perm" title={key}>
          {permissionLabel(catalog, key, locale)}
        </span>
      ))}
      {keys.length > limit ? (
        <span className="chip chip-more" title={keys.slice(limit).join("\n")}>
          {t("role.expand", { n: keys.length - limit })}
        </span>
      ) : null}
    </div>
  );
}
