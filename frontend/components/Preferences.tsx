"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { useI18n, type Locale } from "@/lib/i18n";
import { applyTheme, THEME_KEY, usePrefs, type NavMode, type ThemeMode } from "@/lib/prefs";
import { cx } from "@/lib/format";

const THEMES: { value: ThemeMode; labelKey: "theme.system" | "theme.light" | "theme.dark" }[] = [
  { value: "system", labelKey: "theme.system" },
  { value: "light", labelKey: "theme.light" },
  { value: "dark", labelKey: "theme.dark" },
];

const LOCALE_LABEL: Record<Locale, string> = { en: "English", zh: "中文" };

const NAVA: { value: NavMode; labelKey: "prefs.navRail" | "prefs.navTop" }[] = [
  { value: "rail", labelKey: "prefs.navRail" },
  { value: "top", labelKey: "prefs.navTop" },
];

export function Preferences() {
  const { t, locale, setLocale } = useI18n();
  const { nav, setNav } = usePrefs();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeMode>("system");
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const stored = localStorage.getItem(THEME_KEY) as ThemeMode | null;
    setTheme(stored ?? "system");

    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if ((localStorage.getItem(THEME_KEY) as ThemeMode | null) === "system") applyTheme("system");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const chooseTheme = (next: ThemeMode) => {
    setTheme(next);
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  };

  return (
    <div className="prefs" ref={rootRef}>
      <button
        className={cx("icon-btn", open && "active")}
        onClick={() => setOpen((prev) => !prev)}
        title={t("prefs.title")}
        aria-label={t("prefs.title")}
        aria-expanded={open}
      >
        <Icon name="settings" size={15} />
      </button>

      {open ? (
        <div className="prefs-pop" role="dialog" aria-label={t("prefs.title")}>
          <div className="prefs-row">
            <span className="prefs-label">{t("prefs.theme")}</span>
            <div className="seg">
              {THEMES.map((item) => (
                <button
                  key={item.value}
                  className={cx(theme === item.value && "active")}
                  onClick={() => chooseTheme(item.value)}
                >
                  {t(item.labelKey)}
                </button>
              ))}
            </div>
          </div>

          <div className="prefs-row">
            <span className="prefs-label">{t("prefs.language")}</span>
            <div className="seg">
              {(Object.keys(LOCALE_LABEL) as Locale[]).map((value) => (
                <button
                  key={value}
                  className={cx(locale === value && "active")}
                  onClick={() => setLocale(value)}
                >
                  {LOCALE_LABEL[value]}
                </button>
              ))}
            </div>
          </div>

          <div className="prefs-row">
            <span className="prefs-label">{t("prefs.navigation")}</span>
            <div className="seg">
              {NAVA.map((item) => (
                <button
                  key={item.value}
                  className={cx(nav === item.value && "active")}
                  onClick={() => setNav(item.value)}
                >
                  {t(item.labelKey)}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
