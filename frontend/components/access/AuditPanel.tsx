"use client";

/**
 * Audit tab: one row per decision worth explaining later — including the ones
 * that were refused. Filtering happens client-side because the page already
 * holds the last N rows; the API is the paged view.
 */

import { useMemo, useState } from "react";

import { Icon } from "@/components/Icon";
import { Empty, Pill } from "@/components/ui";
import { AUDIT_STATUS, auditActionLabel } from "@/lib/access";
import { relativeTime } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import type { AuditRow } from "@/lib/types";

export function AuditPanel({ rows }: { rows: AuditRow[] }) {
  const { t, locale } = useI18n();
  const [action, setAction] = useState("");
  const [status, setStatus] = useState("");

  const actions = useMemo(() => {
    const seen = new Map<string, string>();
    rows.forEach((row) => {
      if (!seen.has(row.action)) seen.set(row.action, auditActionLabel(row, locale));
    });
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [rows, locale]);

  const filtered = rows.filter(
    (row) => (!action || row.action === action) && (!status || row.status === status),
  );

  return (
    <div className="card">
      <div className="card-head">
        <Icon name="pulse" size={15} />
        <span className="card-title">{t("audit.title")}</span>
        <span className="count-badge">{filtered.length}</span>
        <span className="spacer" />
        <span className="muted" style={{ fontSize: 11.5 }}>
          {t("audit.subtitle")}
        </span>
        <select
          className="select select-sm"
          value={action}
          onChange={(event) => setAction(event.target.value)}
        >
          <option value="">{t("audit.filterAll")}</option>
          {actions.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
        <select
          className="select select-sm"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="">{t("audit.status")}</option>
          <option value="ok">{t("audit.ok")}</option>
          <option value="denied">{t("audit.denied")}</option>
          <option value="error">{t("audit.error")}</option>
        </select>
      </div>

      {filtered.length === 0 ? (
        <Empty icon="pulse" title={t("audit.none")} />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{t("audit.when")}</th>
                <th>{t("audit.actor")}</th>
                <th>{t("audit.action")}</th>
                <th>{t("audit.target")}</th>
                <th>{t("audit.ip")}</th>
                <th>{t("audit.status")}</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const meta = AUDIT_STATUS[row.status] ?? { key: "audit.ok" as const, tone: "ok" as const };
                return (
                  <tr key={row.id}>
                    <td className="muted" title={row.created_at}>
                      {relativeTime(row.created_at, locale)}
                    </td>
                    <td>
                      <b>{row.actor_name || "—"}</b>
                    </td>
                    <td>
                      <span className="mono" style={{ fontSize: 11.5 }}>
                        {auditActionLabel(row, locale)}
                      </span>
                    </td>
                    <td>
                      {row.resource_type ? (
                        <span className="chip" title={JSON.stringify(row.detail ?? {}, null, 0)}>
                          {row.resource_type}
                          {row.resource_id ? (
                            <span className="mono muted"> {row.resource_id.slice(0, 10)}</span>
                          ) : null}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td className="mono muted" style={{ fontSize: 11 }}>
                      {row.ip || "—"}
                    </td>
                    <td>
                      <Pill tone={meta.tone}>{t(meta.key)}</Pill>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
