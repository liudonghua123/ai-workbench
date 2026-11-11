"use client";

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { Icon, type IconName } from "./Icon";
import { cx, toneClass } from "@/lib/format";

/* ---------------------------------------------------------------- pills */

export function Pill({
  children,
  tone,
  live,
  icon,
}: {
  children: ReactNode;
  tone?: "ok" | "warn" | "err" | "idle" | "accent";
  live?: boolean;
  icon?: IconName;
}) {
  return (
    <span className={cx("pill", toneClass(tone), live && "pill-live")}>
      {icon ? <Icon name={icon} size={11} /> : <i className="dot" />}
      {children}
    </span>
  );
}

export function StatusPill({
  map,
  value,
}: {
  map: Record<string, { label: string; tone: "ok" | "warn" | "err" | "idle" | "accent" }>;
  value: string;
}) {
  const meta = map[value] ?? { label: value, tone: "idle" as const };
  return (
    <Pill tone={meta.tone} live={meta.tone === "warn" || value === "running"}>
      {meta.label}
    </Pill>
  );
}

/* ---------------------------------------------------------------- states */

export function Empty({
  icon = "spark",
  title,
  hint,
  action,
}: {
  icon?: IconName;
  title: string;
  hint?: string;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <div
        style={{
          width: 38,
          height: 38,
          borderRadius: 12,
          display: "grid",
          placeItems: "center",
          background: "var(--surface-3)",
          color: "var(--ink-3)",
        }}
      >
        <Icon name={icon} size={18} />
      </div>
      <div style={{ color: "var(--ink)", fontWeight: 600, fontSize: 13 }}>{title}</div>
      {hint ? <div style={{ maxWidth: "46ch" }}>{hint}</div> : null}
      {action}
    </div>
  );
}

export function Skeleton({ height = 16, width = "100%", radius = 8 }: { height?: number; width?: number | string; radius?: number }) {
  return <div className="skeleton" style={{ height, width, borderRadius: radius }} />;
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack" style={{ padding: 4 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} height={44} />
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- tabs */

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { key: T; label: string; count?: number }[];
  value: T;
  onChange: (key: T) => void;
}) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={value === tab.key}
          className={cx("tab", value === tab.key && "active")}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
          {typeof tab.count === "number" ? <span className="count">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- toast */

type ToastItem = { id: number; message: string; tone: "ok" | "err" };
type ToastApi = { push: (message: string, tone?: "ok" | "err") => void };

const ToastContext = createContext<ToastApi>({ push: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const push = useCallback((message: string, tone: "ok" | "err" = "ok") => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev, { id, message, tone }]);
    setTimeout(() => setItems((prev) => prev.filter((i) => i.id !== id)), 4200);
  }, []);

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-wrap">
        {items.map((item) => (
          <div key={item.id} className={cx("toast", item.tone === "err" && "toast-err")}>
            <Icon name={item.tone === "err" ? "close" : "check"} size={14} />
            <span>{item.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

/* ---------------------------------------------------------------- misc */

export function KV({ items }: { items: { k: string; v: ReactNode }[] }) {
  return (
    <dl className="kv">
      {items.map((item) => (
        <div key={item.k} style={{ display: "contents" }}>
          <dt>{item.k}</dt>
          <dd>{item.v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function CopyButton({ value, label = "复制" }: { value: string; label?: string }) {
  const { push } = useToast();
  return (
    <button
      className="icon-btn"
      title={label}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          push("已复制到剪贴板");
        } catch {
          push("复制失败，请手动选择", "err");
        }
      }}
    >
      <Icon name="copy" size={13} />
    </button>
  );
}
