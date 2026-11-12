"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Icon } from "@/components/Icon";
import { api } from "@/lib/api";
import { getToken } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";

type State =
  | { kind: "idle" }
  | { kind: "needsLogin" }
  | { kind: "done"; org: string; role: string }
  | { kind: "error"; message: string };

/** Accepts an invitation carried in the URL fragment (`#token=…`). */
export default function InvitePage() {
  const { t } = useI18n();
  const { refresh } = useSession();
  const [state, setState] = useState<State>({ kind: "idle" });
  const [token, setTokenValue] = useState("");

  useEffect(() => {
    const parsed = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const value = parsed.get("token") || "";
    setTokenValue(value);
    if (!value) {
      setState({ kind: "error", message: t("auth.inviteInvalid") });
      return;
    }
    if (!getToken()) {
      setState({ kind: "needsLogin" });
      return;
    }
    api
      .acceptInvitation(value)
      .then(async (result) => {
        await refresh();
        setState({
          kind: "done",
          org: result.org?.name ?? "",
          role: result.role_key,
        });
      })
      .catch((err: unknown) =>
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : t("auth.inviteInvalid"),
        }),
      );
  }, [refresh, t]);

  return (
    <div className="auth-shell">
      <div className="auth-panel auth-panel-compact">
        <div className="auth-brand">
          <span className="brand-mark" style={{ width: 30, height: 30, borderRadius: 9 }}>
            <Icon name="users" size={15} />
          </span>
          <b>{t("auth.inviteTitle")}</b>
        </div>

        {state.kind === "needsLogin" ? (
          <>
            <p className="auth-sub">{t("auth.inviteNeedsLogin")}</p>
            <Link
              className="btn btn-primary"
              href={`/login?next=${encodeURIComponent(`/invite#token=${token}`)}`}
            >
              {t("auth.signin")}
            </Link>
          </>
        ) : null}

        {state.kind === "done" ? (
          <>
            <div className="auth-ok">
              <Icon name="check" size={14} />
              <span>{t("auth.inviteJoined", { org: state.org, role: state.role })}</span>
            </div>
            <Link className="btn btn-primary" href="/access">
              {t("acc.title")}
            </Link>
          </>
        ) : null}

        {state.kind === "error" ? (
          <>
            <div className="auth-error">
              <Icon name="close" size={13} />
              <span>{state.message}</span>
            </div>
            <Link className="btn" href="/">
              {t("auth.enter")}
            </Link>
          </>
        ) : null}

        {state.kind === "idle" ? <div className="auth-spinner" /> : null}
      </div>
    </div>
  );
}
