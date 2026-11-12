"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Icon, type IconName } from "@/components/Icon";
import { useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { setToken } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import type { LoginProvider, ProviderDirectory } from "@/lib/types";

const PROVIDER_ICON: Record<string, IconName> = {
  cas: "shield",
  oauth2: "key",
  oidc: "lock",
  wecom: "pulse",
  dingtalk: "spark",
  mock: "ticket",
};

type Mode = "password" | "signup" | "token";

export default function LoginPage() {
  const { t } = useI18n();
  const { push } = useToast();
  const router = useRouter();
  const { me, authEnabled, loading, refresh } = useSession();

  const [next, setNext] = useState("/");
  const [directory, setDirectory] = useState<ProviderDirectory | null>(null);
  const [mode, setMode] = useState<Mode>("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [orgName, setOrgName] = useState("");
  const [token, setTokenValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .loginProviders()
      .then(setDirectory)
      .catch(() => setDirectory(null));
    // read the query from the browser instead of useSearchParams so the page can
    // still be statically rendered
    const target = new URLSearchParams(window.location.search).get("next");
    if (target) setNext(target);
  }, []);

  // the SSO callback reports failures through the URL fragment
  useEffect(() => {
    const hash = window.location.hash.replace(/^#/, "");
    if (!hash) return;
    const parsed = new URLSearchParams(hash);
    const message = parsed.get("error");
    if (message) setError(message);
  }, []);

  const signedIn = Boolean(me && !me.principal.anonymous);
  const providers = useMemo(() => directory?.providers ?? [], [directory]);

  const startSso = async (provider: LoginProvider) => {
    setBusy(true);
    setError("");
    try {
      const { authorize_url } = await api.startSso(provider.key, next);
      window.location.href = authorize_url;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.failed"));
      setBusy(false);
    }
  };

  const submitPassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api.localLogin(email, password);
      setToken(result.token);
      await refresh();
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.failed"));
    } finally {
      setBusy(false);
    }
  };

  const submitSignup = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api.signup({ email, password, name, org_name: orgName });
      setToken(result.token);
      push(t("auth.signup"), "ok");
      await refresh();
      router.replace(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.failed"));
    } finally {
      setBusy(false);
    }
  };

  const submitToken = async (event: React.FormEvent) => {
    event.preventDefault();
    setToken(token.trim());
    await refresh();
    router.replace(next);
  };

  return (
    <div className="auth-shell">
      <div className="auth-panel">
        <div className="auth-brand">
          <span className="brand-mark" style={{ width: 34, height: 34, borderRadius: 10 }}>
            <Icon name="route" size={17} />
          </span>
          <div>
            <b>AI Workbench</b>
            <span className="muted">{t("nav.brandSub")}</span>
          </div>
        </div>

        <h1 className="auth-title">{t("auth.title")}</h1>
        <p className="auth-sub">{t("auth.subtitle")}</p>

        {error ? (
          <div className="auth-error">
            <Icon name="close" size={13} />
            <span>{error}</span>
          </div>
        ) : null}

        {!loading && !authEnabled ? (
          <div className="auth-disabled">
            <Icon name="shield" size={16} />
            <div>
              <b>{t("auth.disabled")}</b>
              <p className="muted">{t("auth.disabledHint")}</p>
            </div>
            <Link className="btn btn-primary btn-sm" href="/">
              {t("auth.enter")}
            </Link>
          </div>
        ) : null}

        {signedIn ? (
          <div className="auth-signedin">
            <span className="muted">{t("auth.signedInAs")}</span>
            <b>{me?.user?.name || me?.principal.name}</b>
            <span className="mono muted">
              {me?.principal.email} · {me?.active_org?.name}
            </span>
            <Link className="btn btn-primary btn-sm" href={next}>
              {t("auth.continue", { name: me?.active_org?.name ?? "Workbench" })}
            </Link>
          </div>
        ) : null}

        {providers.length > 0 ? (
          <div className="auth-section">
            <div className="auth-section-head">{t("auth.sso")}</div>
            <div className="auth-providers">
              {providers.map((provider) => (
                <button
                  key={provider.key}
                  className="auth-provider"
                  onClick={() => startSso(provider)}
                  disabled={busy || !provider.ready}
                  title={provider.ready ? provider.name : t("auth.notReady")}
                >
                  <span className={`auth-provider-icon kind-${provider.kind}`}>
                    <Icon name={PROVIDER_ICON[provider.kind] ?? "shield"} size={16} />
                  </span>
                  <span className="auth-provider-body">
                    <b>{provider.name}</b>
                    <span className="mono muted">
                      {provider.kind.toUpperCase()}
                      {provider.org_name ? ` · ${provider.org_name}` : ""}
                    </span>
                  </span>
                  {provider.ready ? (
                    <Icon name="arrow-right" size={14} />
                  ) : (
                    <span className="pill pill-idle">{t("auth.notReady")}</span>
                  )}
                </button>
              ))}
            </div>
            {providers.some((provider) => provider.kind === "mock") ? (
              <p className="auth-note">{t("auth.mockNote")}</p>
            ) : null}
          </div>
        ) : null}

        <div className="auth-section">
          <div className="auth-section-head">
            {mode === "token" ? t("auth.token") : mode === "signup" ? t("auth.signup") : t("auth.local")}
          </div>

          {mode === "password" ? (
            <form className="auth-form" onSubmit={submitPassword}>
              <label>
                <span>{t("auth.email")}</span>
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  autoComplete="username"
                  required
                />
              </label>
              <label>
                <span>{t("auth.password")}</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  autoComplete="current-password"
                  required
                />
              </label>
              <button className="btn btn-primary" type="submit" disabled={busy}>
                {t("auth.signin")}
              </button>
            </form>
          ) : null}

          {mode === "signup" ? (
            <form className="auth-form" onSubmit={submitSignup}>
              <div className="auth-row">
                <label>
                  <span>{t("auth.email")}</span>
                  <input
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                  />
                </label>
                <label>
                  <span>{t("auth.name")}</span>
                  <input value={name} onChange={(event) => setName(event.target.value)} />
                </label>
              </div>
              <label>
                <span>{t("auth.password")}</span>
                <input
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  minLength={8}
                  autoComplete="new-password"
                  required
                />
              </label>
              <label>
                <span>{t("auth.orgName")}</span>
                <input
                  value={orgName}
                  onChange={(event) => setOrgName(event.target.value)}
                  placeholder={t("auth.orgNameHint")}
                />
              </label>
              <button className="btn btn-primary" type="submit" disabled={busy}>
                {t("auth.signup")}
              </button>
            </form>
          ) : null}

          {mode === "token" ? (
            <form className="auth-form" onSubmit={submitToken}>
              <label>
                <span>{t("auth.token")}</span>
                <input
                  value={token}
                  onChange={(event) => setTokenValue(event.target.value)}
                  placeholder="wbs_… / WB_AUTH_TOKENS"
                  autoComplete="off"
                  required
                />
              </label>
              <p className="auth-note">{t("auth.tokenHint")}</p>
              <button className="btn btn-primary" type="submit">
                {t("auth.enterToken")}
              </button>
            </form>
          ) : null}

          <div className="auth-switches">
            {mode !== "password" ? (
              <button type="button" className="linkbtn" onClick={() => setMode("password")}>
                {t("auth.usePassword")}
              </button>
            ) : null}
            {directory?.signup_enabled && mode !== "signup" ? (
              <button type="button" className="linkbtn" onClick={() => setMode("signup")}>
                {t("auth.noAccount")}
              </button>
            ) : null}
            {mode === "signup" ? (
              <button type="button" className="linkbtn" onClick={() => setMode("password")}>
                {t("auth.haveAccount")}
              </button>
            ) : null}
            {mode !== "token" ? (
              <button type="button" className="linkbtn" onClick={() => setMode("token")}>
                {t("auth.useToken")}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
