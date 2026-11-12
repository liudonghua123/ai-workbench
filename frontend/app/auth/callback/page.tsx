"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Icon } from "@/components/Icon";
import { setToken } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

/**
 * Landing page for an SSO round trip.
 *
 * The API redirects here with `#token=…&next=…`. A fragment never reaches a
 * server, so the token stays out of access logs; we read it once, drop it from
 * the URL, and let the session provider re-resolve authority.
 */
export default function AuthCallbackPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { refresh } = useSession();
  const [error, setError] = useState("");

  useEffect(() => {
    const parsed = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const token = parsed.get("token");
    const next = parsed.get("next") || "/";
    if (!token) {
      setError(t("auth.failed"));
      return;
    }
    setToken(token);
    // strip the fragment before navigating so a refresh cannot replay it
    window.history.replaceState(null, "", window.location.pathname);
    refresh()
      .then(() => router.replace(next))
      .catch(() => setError(t("auth.failed")));
  }, [refresh, router, t]);

  return (
    <div className="auth-shell">
      <div className="auth-panel auth-panel-compact">
        <div className="auth-brand">
          <span className="brand-mark" style={{ width: 30, height: 30, borderRadius: 9 }}>
            <Icon name="route" size={15} />
          </span>
          <b>AI Workbench</b>
        </div>
        {error ? (
          <div className="auth-error">
            <Icon name="close" size={13} />
            <span>{error}</span>
          </div>
        ) : (
          <>
            <div className="auth-spinner" />
            <h1 className="auth-title">{t("auth.verifying")}</h1>
            <p className="auth-sub">{t("auth.verifyingHint")}</p>
          </>
        )}
        <a className="linkbtn" href="/login">
          {t("auth.backToLogin")}
        </a>
      </div>
    </div>
  );
}
