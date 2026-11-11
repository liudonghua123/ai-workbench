"use client";

/**
 * Layout preferences that affect the DOM structure (currently nav placement).
 * Theme is applied directly to `<html>` and lives in `components/Preferences`.
 */

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type NavMode = "rail" | "top";
export type ThemeMode = "system" | "light" | "dark";

export const THEME_KEY = "wb-theme";
export const NAV_KEY = "wb-nav";

type PrefsApi = { nav: NavMode; setNav: (next: NavMode) => void };

const PrefsContext = createContext<PrefsApi>({ nav: "rail", setNav: () => undefined });

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [nav, setNavState] = useState<NavMode>("rail");

  useEffect(() => {
    const stored = localStorage.getItem(NAV_KEY);
    if (stored === "rail" || stored === "top") setNavState(stored);
  }, []);

  const setNav = (next: NavMode) => {
    setNavState(next);
    localStorage.setItem(NAV_KEY, next);
  };

  const api = useMemo(() => ({ nav, setNav }), [nav]);

  return <PrefsContext.Provider value={api}>{children}</PrefsContext.Provider>;
}

export function usePrefs(): PrefsApi {
  return useContext(PrefsContext);
}

export function applyTheme(mode: ThemeMode) {
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const resolved = mode === "system" ? (prefersDark ? "dark" : "light") : mode;
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
}

/** Runs before hydration so the first paint already has the right theme. */
export const themeScript = `(function(){try{var m=localStorage.getItem("${THEME_KEY}")||"system";var d=window.matchMedia("(prefers-color-scheme: dark)").matches;var r=m==="system"?(d?"dark":"light"):m;document.documentElement.dataset.theme=r;document.documentElement.style.colorScheme=r;var n=localStorage.getItem("${NAV_KEY}");if(n==="rail"||n==="top")document.documentElement.dataset.nav=n;}catch(e){}})();`;
