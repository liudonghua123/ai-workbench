"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { clockTime, cx, eventTone, relativeTime } from "@/lib/format";
import { useI18n, type MessageKey } from "@/lib/i18n";
import type { EventItem } from "@/lib/types";

const KINDS: { key: string; labelKey: MessageKey }[] = [
  { key: "all", labelKey: "common.all" },
  { key: "project", labelKey: "event.kindProject" },
  { key: "deployment", labelKey: "event.kindDeployment" },
  { key: "agent", labelKey: "event.kindAgent" },
  { key: "file", labelKey: "event.kindFile" },
];

export default function EventsPage() {
  const { t, locale } = useI18n();
  const [events, setEvents] = useState<EventItem[] | null>(null);
  const [kind, setKind] = useState("all");
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .events(120)
      .then(setEvents)
      .catch((err) => setError(err.message));
  }, []);

  const visible = useMemo(() => {
    if (!events) return [];
    if (kind === "all") return events;
    return events.filter((event) => event.kind.includes(kind));
  }, [events, kind]);

  return (
    <main className="content">
      <div className="page-head">
        <div>
          <h1 className="page-title">{t("event.title")}</h1>
          <p className="page-desc">{t("event.desc")}</p>
        </div>
        <div className="spacer" />
        <Pill tone="idle" icon="pulse">
          {t("event.count", { n: events?.length ?? 0 })}
        </Pill>
      </div>

      <div className="row" style={{ gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        {KINDS.map((item) => (
          <button
            key={item.key}
            className={cx("btn btn-sm", kind === item.key ? "btn-primary" : "btn-ghost")}
            onClick={() => setKind(item.key)}
          >
            {t(item.labelKey)}
          </button>
        ))}
      </div>

      <section className="card">
        <div className="card-body" style={{ paddingTop: 8, paddingBottom: 8 }}>
          {error ? (
            <Empty icon="close" title={t("event.readFailed")} hint={error} />
          ) : events === null ? (
            <div className="stack">
              {Array.from({ length: 5 }).map((_, index) => (
                <Skeleton key={index} height={38} />
              ))}
            </div>
          ) : visible.length === 0 ? (
            <Empty icon="pulse" title={t("event.empty")} hint={t("event.emptyHint")} />
          ) : (
            visible.map((event) => (
              <div className="event-row" key={event.id} style={{ padding: "10px 0" }}>
                <span className="event-dot" style={{ background: eventTone(event.kind) }} />
                <span style={{ minWidth: 0, flex: 1 }}>
                  <span style={{ display: "block" }}>{event.message}</span>
                  <span className="row" style={{ gap: 8, marginTop: 2 }}>
                    <span className="mono muted" style={{ fontSize: 10.5 }}>
                      {event.kind}
                    </span>
                    {event.project_id ? (
                      <span className="mono muted" style={{ fontSize: 10.5 }}>
                        · {event.project_id}
                      </span>
                    ) : null}
                  </span>
                </span>
                <span className="event-time" title={clockTime(event.created_at, locale)}>
                  {relativeTime(event.created_at, locale)}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="row muted" style={{ marginTop: 12, gap: 6, fontSize: 11.5 }}>
        <Icon name="clock" size={12} /> {t("event.footNote")}
      </div>
    </main>
  );
}
