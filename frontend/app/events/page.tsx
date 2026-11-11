"use client";

import { useEffect, useMemo, useState } from "react";
import { Icon } from "@/components/Icon";
import { Empty, Pill, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { clockTime, cx, eventTone, relativeTime } from "@/lib/format";
import type { EventItem } from "@/lib/types";

const KINDS = [
  { key: "all", label: "全部" },
  { key: "project", label: "项目" },
  { key: "deployment", label: "部署" },
  { key: "agent", label: "Agent" },
  { key: "file", label: "文件" },
];

export default function EventsPage() {
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
          <h1 className="page-title">事件流</h1>
          <p className="page-desc">
            控制面的全部副作用都会留下事件：创建、装配资源、部署、Agent 运行、文件写入。
          </p>
        </div>
        <div className="spacer" />
        <Pill tone="idle" icon="pulse">
          {events?.length ?? 0} 条
        </Pill>
      </div>

      <div className="row" style={{ gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
        {KINDS.map((item) => (
          <button
            key={item.key}
            className={cx("btn btn-sm", kind === item.key ? "btn-primary" : "btn-ghost")}
            onClick={() => setKind(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <section className="card">
        <div className="card-body" style={{ paddingTop: 8, paddingBottom: 8 }}>
          {error ? (
            <Empty icon="close" title="无法读取事件" hint={error} />
          ) : events === null ? (
            <div className="stack">
              {Array.from({ length: 5 }).map((_, index) => (
                <Skeleton key={index} height={38} />
              ))}
            </div>
          ) : visible.length === 0 ? (
            <Empty icon="pulse" title="暂无事件" hint="创建项目或部署后会在这里出现记录。" />
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
                <span className="event-time" title={clockTime(event.created_at)}>
                  {relativeTime(event.created_at)}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="row muted" style={{ marginTop: 12, gap: 6, fontSize: 11.5 }}>
        <Icon name="clock" size={12} /> 事件保留在控制面数据库中，可作为审计与回放依据
      </div>
    </main>
  );
}
