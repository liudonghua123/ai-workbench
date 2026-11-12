"use client";

/**
 * Session context: who is logged in, which organization is active, and what they
 * may do.
 *
 * Two things this deliberately does *not* do:
 *
 * * It never caches authority. `can()` reads the permission set the server
 *   returned for this session, and the server re-derives it on every request —
 *   so a role change takes effect on the next page, not the next login.
 * * It is not a security boundary. Hiding a button is a courtesy; the API rejects
 *   the request anyway, and the denial shows up in the audit log.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { api, ApiError } from "./api";
import { clearToken, TOKEN_EVENT, UNAUTHORIZED_EVENT } from "./auth";
import type { Organization, SessionMe } from "./types";

type SessionApi = {
  me: SessionMe | null;
  loading: boolean;
  authEnabled: boolean;
  /** true when the API wants credentials we do not have */
  needsLogin: boolean;
  /** true when the control plane could not be reached at all */
  offline: boolean;
  activeOrg: Organization | null;
  permissions: string[];
  can: (permission: string) => boolean;
  /** true when the role reaches every project in the org (data scope = all) */
  seesAll: boolean;
  refresh: () => Promise<void>;
  switchOrg: (orgId: string) => Promise<void>;
  logout: () => Promise<void>;
};

const SessionContext = createContext<SessionApi | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<SessionMe | null>(null);
  const [loading, setLoading] = useState(true);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [offline, setOffline] = useState(false);
  // the public provider directory answers "is auth on?" even for an anonymous
  // browser — without it a 401 would leave us unable to tell sign-in apart from
  // an instance that simply runs without auth
  const [authRequired, setAuthRequired] = useState<boolean | null>(null);

  useEffect(() => {
    api
      .loginProviders()
      .then((directory) => setAuthRequired(directory.auth_enabled))
      .catch(() => setAuthRequired(null));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const next = await api.sessionMe();
      setMe(next);
      setNeedsLogin(false);
      setOffline(false);
    } catch (err) {
      setMe(null);
      // a 401 means "sign in"; a dropped connection or a 500 is a different
      // problem and must not bounce the user to a login form that cannot work
      const status = err instanceof ApiError ? err.status : 0;
      setNeedsLogin(status === 401 || status === 403);
      setOffline(status === 0 || status >= 500);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // a 401 anywhere means the token is gone or revoked — one prompt, not N errors
  useEffect(() => {
    const onUnauthorized = () => {
      clearToken();
      setMe(null);
      setNeedsLogin(true);
    };
    const onToken = () => refresh();
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    window.addEventListener(TOKEN_EVENT, onToken);
    return () => {
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
      window.removeEventListener(TOKEN_EVENT, onToken);
    };
  }, [refresh]);

  const switchOrg = useCallback(
    async (orgId: string) => {
      // the session row is pinned server-side, so a refresh is all that is needed
      await api.switchOrg(orgId);
      await refresh();
    },
    [refresh],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      /* logging out locally matters more than the server round trip */
    }
    clearToken();
    setMe(null);
    setNeedsLogin(true);
  }, []);

  const activeOrg = useMemo(() => {
    if (!me) return null;
    return me.active_org ?? me.organizations[0] ?? null;
  }, [me]);

  const permissions = useMemo(() => me?.principal.permissions ?? [], [me]);

  // Data scope is separate from permissions: a viewer may only *read*, yet a
  // viewer with scope=all reads every project. The API is the authority; this
  // flag only decides whether the UI says "yours" or "granted to you".
  const seesAll = useMemo(() => {
    const principal = me?.principal;
    if (!principal) return false;
    return (
      principal.anonymous ||
      principal.is_platform_admin ||
      (principal.data_scope ?? "all") === "all"
    );
  }, [me]);

  const can = useCallback(
    (permission: string) => {
      if (!me) return false;
      if (me.principal.anonymous || me.principal.is_platform_admin) return true;
      if (permissions.includes("*") || permissions.includes(permission)) return true;
      // a project-scoped binding can grant it; the API decides, we stay optimistic
      const domain = permission.split(":")[0];
      return permissions.includes(`${domain}:*`);
    },
    [me, permissions],
  );

  const value = useMemo<SessionApi>(
    () => ({
      me,
      loading,
      authEnabled: me?.auth_enabled ?? authRequired ?? false,
      needsLogin,
      offline,
      activeOrg,
      permissions,
      can,
      seesAll,
      refresh,
      switchOrg,
      logout,
    }),
    [me, loading, authRequired, needsLogin, offline, activeOrg, permissions, can, seesAll, refresh, switchOrg, logout],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionApi {
  const ctx = useContext(SessionContext);
  if (!ctx) {
    throw new Error("useSession must be used inside SessionProvider");
  }
  return ctx;
}
