"use client";

import { useEffect, useRef } from "react";
import { Icon } from "./Icon";
import { cx } from "@/lib/format";

export function LogViewer({
  title,
  log,
  empty = "暂无日志",
  live,
}: {
  title: string;
  log: string;
  empty?: string;
  live?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [log]);

  return (
    <div>
      <div className="terminal-head">
        <Icon name="terminal" size={12} />
        <span className="mono">{title}</span>
        <div className="spacer" />
        {live ? (
          <span className="row" style={{ gap: 5, color: "var(--warn)" }}>
            <i
              style={{
                width: 5,
                height: 5,
                borderRadius: 9,
                background: "currentColor",
                animation: "pulse 1.6s infinite",
              }}
            />
            流式输出
          </span>
        ) : null}
      </div>
      <div className="terminal" ref={ref}>
        {log ? (
          log
            .split("\n")
            .filter((line) => line.length > 0)
            .map((line, index) => (
              <div
                key={index}
                className={cx(
                  line.includes("[error]") && "ln-err",
                  line.includes("[ok]") && "ln-ok",
                )}
              >
                {line}
              </div>
            ))
        ) : (
          <span style={{ color: "var(--term-dim)" }}>{empty}</span>
        )}
      </div>
    </div>
  );
}
