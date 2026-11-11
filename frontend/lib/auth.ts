/**
 * Client-side token store.
 *
 * Deliberately tiny: the backend decides authority, the browser only carries the
 * token. A 401 anywhere in the app dispatches `wb:unauthorized`, which the token gate
 * listens for — so a revoked token surfaces as one prompt instead of N broken pages.
 */

const TOKEN_KEY = "wb-token";
export const UNAUTHORIZED_EVENT = "wb:unauthorized";
export const TOKEN_EVENT = "wb:token-changed";

export function getToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(token: string): void {
  if (typeof window === "undefined") return;
  try {
    const value = token.trim();
    if (value) window.localStorage.setItem(TOKEN_KEY, value);
    else window.localStorage.removeItem(TOKEN_KEY);
    window.dispatchEvent(new Event(TOKEN_EVENT));
  } catch {
    /* private mode — the token simply won't persist */
  }
}

export function clearToken(): void {
  setToken("");
}

export function notifyUnauthorized(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
}

/** EventSource cannot set headers, so SSE falls back to `?token=`. */
export function tokenQuery(url: string): string {
  const token = getToken();
  if (!token) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}
