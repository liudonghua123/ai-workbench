"use client";

import { useEffect, useState } from "react";
import { Icon } from "./Icon";

type Mode = "light" | "dark" | "system";
const KEY = "wb-theme";

function apply(mode: Mode) {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = mode === "system" ? (prefersDark ? "dark" : "light") : mode;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
}

export function ThemeToggle() {
  const [mode, setMode] = useState<Mode>("system");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const stored = (localStorage.getItem(KEY) as Mode | null) ?? "system";
    setMode(stored);
    apply(stored);
    setReady(true);

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if ((localStorage.getItem(KEY) as Mode | null) === "system" || !localStorage.getItem(KEY)) {
        apply("system");
      }
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const cycle = () => {
    const next: Mode = mode === "system" ? "light" : mode === "light" ? "dark" : "system";
    setMode(next);
    localStorage.setItem(KEY, next);
    apply(next);
  };

  const icon = mode === "system" ? "monitor" : mode === "light" ? "sun" : "moon";
  const label = mode === "system" ? "跟随系统" : mode === "light" ? "浅色" : "深色";

  return (
    <button className="icon-btn" onClick={cycle} title={`主题：${label}`} aria-label={`主题：${label}`}>
      <Icon name={ready ? icon : "monitor"} size={15} />
    </button>
  );
}

export const themeScript = `(function(){try{var m=localStorage.getItem("${KEY}")||"system";var d=window.matchMedia("(prefers-color-scheme: dark)").matches;var r=m==="system"?(d?"dark":"light"):m;document.documentElement.dataset.theme=r;document.documentElement.style.colorScheme=r;}catch(e){}})();`;
