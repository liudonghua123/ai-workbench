"use client";

import { usePrefs } from "@/lib/prefs";
import { Rail } from "./Rail";
import { Topbar } from "./Topbar";

/**
 * Owns the outer frame so nav placement can switch between a sticky sidebar and
 * a horizontal top bar without the individual pages knowing about it.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const { nav } = usePrefs();

  return (
    <div className="shell" data-nav={nav}>
      {nav === "rail" ? <Rail /> : null}
      <div className="main">
        <Topbar />
        {children}
      </div>
    </div>
  );
}
