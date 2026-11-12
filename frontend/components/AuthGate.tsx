"use client";

/**
 * Redirects to the sign-in page when the API says this browser has no valid
 * session. Mounted once in the shell, so no page needs its own guard.
 *
 * It only reacts to *authentication* failures: a network error or a 500 keeps the
 * current page (with its own error state) rather than bouncing the user to a
 * login form that cannot work either. `SessionProvider` makes that distinction.
 */

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useSession } from "@/lib/session";

export function AuthGate() {
  const router = useRouter();
  const { authEnabled, needsLogin, loading } = useSession();

  useEffect(() => {
    if (loading || !authEnabled || !needsLogin) return;
    const next = `${window.location.pathname}${window.location.search}`;
    router.replace(`/login?next=${encodeURIComponent(next)}`);
  }, [loading, authEnabled, needsLogin, router]);

  return null;
}
