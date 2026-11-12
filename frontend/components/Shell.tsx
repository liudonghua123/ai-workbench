"use client";

import { usePathname } from "next/navigation";

import { usePrefs } from "@/lib/prefs";
import { SessionProvider } from "@/lib/session";
import { AuthGate } from "./AuthGate";
import { Rail } from "./Rail";
import { Topbar } from "./Topbar";

/** Routes that own the whole viewport — the app chrome would only get in the way. */
const BARE_ROUTES = ["/login", "/auth/callback", "/invite"];

/**
 * Owns the outer frame so nav placement can switch between a sticky sidebar and
 * a horizontal top bar without the individual pages knowing about it.
 *
 * `AuthGate` lives inside the provider because it reacts to session state — it
 * bounces to `/login` when the API rejects the credentials, instead of letting
 * every panel render its own 401.
 */
export function Shell({ children }: { children: React.ReactNode }) {
  const { nav } = usePrefs();
  const pathname = usePathname() ?? "/";
  const bare = BARE_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`),
  );

  if (bare) {
    return (
      <SessionProvider>
        <div className="bare-shell">{children}</div>
      </SessionProvider>
    );
  }

  return (
    <SessionProvider>
      <div className="shell" data-nav={nav}>
        {nav === "rail" ? <Rail /> : null}
        <div className="main">
          <Topbar />
          {children}
        </div>
        <AuthGate />
      </div>
    </SessionProvider>
  );
}
