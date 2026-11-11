"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Icon } from "./Icon";
import { setToken, clearToken, getToken, UNAUTHORIZED_EVENT } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";

/**
 * Shown when the API answers 401, or when auth is enabled and this browser holds no
 * token. Mounted once in the shell so any page can trigger it — no per-page guards.
 */
export function TokenGate() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const onUnauthorized = () => {
      setError("");
      setOpen(true);
    };
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, []);

  if (!mounted || !open) return null;

  const submit = () => {
    const token = value.trim();
    if (!token) return;
    setToken(token);
    // a full reload is the honest way to re-run every in-flight query with the new header
    window.location.reload();
  };

  return createPortal(
    <div className="overlay" role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-label={t("gate.title")}>
        <div className="modal-head">
          <Icon name="shield" size={15} />
          <div>
            <div className="card-title">{t("gate.title")}</div>
            <div className="muted" style={{ fontSize: 11.5 }}>
              {t("gate.body")}
            </div>
          </div>
        </div>
        <div className="modal-body stack" style={{ gap: 10 }}>
          <div className="field">
            <label htmlFor="gate-token">{t("sys.token")}</label>
            <input
              id="gate-token"
              className="input mono"
              type="password"
              autoComplete="off"
              placeholder={t("gate.placeholder")}
              value={value}
              onChange={(event) => setValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submit();
              }}
            />
          </div>
          {error ? <div className="pill pill-err">{error}</div> : null}
          <div className="muted mono" style={{ fontSize: 10.5 }}>
            {t("gate.hint")}
          </div>
        </div>
        <div className="modal-foot">
          {getToken() ? (
            <button
              className="btn btn-ghost"
              onClick={() => {
                clearToken();
                window.location.reload();
              }}
            >
              {t("sys.tokenClear")}
            </button>
          ) : null}
          <div className="spacer" />
          <button className="btn btn-accent" onClick={submit} disabled={!value.trim()}>
            <Icon name="check" size={13} /> {t("gate.submit")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
