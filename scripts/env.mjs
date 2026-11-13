/**
 * Dev-time configuration shared by the two `npm run dev` entry points: the repo
 * root one (control plane + console together) and the one in `frontend/`
 * (console only, backend started by hand).
 *
 * Both read the backend address from here, so they cannot drift. Resolution
 * order, first match wins:
 *
 *   1. NEXT_PUBLIC_API_BASE      full URL, the escape hatch (remote API, tunnel)
 *   2. WB_API_HOST / WB_API_PORT
 *   3. backend/.env → repo .env → frontend/.env → frontend/.env.local
 *   4. 127.0.0.1:8000
 *
 * Process environment always beats a file, so `WB_API_PORT=9000 npm run dev`
 * wins over whatever `.env` says without editing anything.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
export const WEB_ROOT = resolve(REPO_ROOT, "frontend");
export const BACKEND_ROOT = resolve(REPO_ROOT, "backend");

export const DEFAULT_API_HOST = "127.0.0.1";
export const DEFAULT_API_PORT = "8000";
export const DEFAULT_WEB_PORT = "3000";

/**
 * Minimal `KEY=VALUE` reader — enough for the files we ship, no dependency.
 * Comments, blank lines, `export ` prefixes and one layer of quotes are handled;
 * an empty value is skipped so it cannot erase a lower-priority layer.
 */
export function readEnvFile(path) {
  const out = {};
  if (!existsSync(path)) return out;
  for (const raw of readFileSync(path, "utf8").split(/\r?\n/)) {
    let line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) line = line.slice(7).trim();
    const eq = line.indexOf("=");
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if (/^".*"$/.test(value) || /^'.*'$/.test(value)) value = value.slice(1, -1);
    if (key && value !== "") out[key] = value;
  }
  return out;
}

function layeredFiles() {
  // Lower priority first: later assignments overwrite earlier ones.
  return Object.assign(
    {},
    readEnvFile(resolve(BACKEND_ROOT, ".env")),
    readEnvFile(resolve(REPO_ROOT, ".env")),
    readEnvFile(resolve(WEB_ROOT, ".env")),
    readEnvFile(resolve(WEB_ROOT, ".env.local")),
  );
}

export function parsePort(value, name) {
  const text = String(value).trim();
  const ok = /^\d+$/.test(text) && Number(text) >= 1 && Number(text) <= 65535;
  if (!ok) {
    throw new Error(
      `${name}="${value}" is not a port. Expected an integer between 1 and 65535.`,
    );
  }
  return text;
}

export function resolveTargets() {
  const files = layeredFiles();

  const pick = (...keys) => {
    for (const key of keys) {
      const fromEnv = process.env[key];
      if (fromEnv && fromEnv.trim()) return fromEnv.trim();
      const fromFile = files[key];
      if (fromFile && String(fromFile).trim()) return String(fromFile).trim();
    }
    return "";
  };

  const apiHost = pick("WB_API_HOST") || DEFAULT_API_HOST;
  const apiPort = parsePort(pick("WB_API_PORT", "WB_BACKEND_PORT") || DEFAULT_API_PORT, "WB_API_PORT");
  const webPort = parsePort(pick("WB_WEB_PORT", "PORT") || DEFAULT_WEB_PORT, "WB_WEB_PORT");

  const explicitBase = pick("NEXT_PUBLIC_API_BASE");
  const origin = explicitBase
    ? explicitBase.replace(/\/api\/v1\/?$/i, "").replace(/\/+$/, "")
    : `http://${apiHost}:${apiPort}`;
  const apiBase = explicitBase
    ? explicitBase.replace(/\/+$/, "")
    : `${origin}/api/v1`;

  return {
    apiHost,
    apiPort,
    webPort,
    apiBase,
    origin,
    explicitBase,
    /** For the startup banner: which knob the operator actually used. */
    source: explicitBase ? "NEXT_PUBLIC_API_BASE" : "WB_API_HOST / WB_API_PORT",
  };
}

/** `http://127.0.0.1:8000/api/v1` — printed identically by both launchers. */
export function describe({ apiBase, webPort, source, explicitBase }) {
  const where = explicitBase ? "" : ` (default ${DEFAULT_API_HOST}:${DEFAULT_API_PORT})`;
  return [
    `  api   ${apiBase}`,
    `        from ${source}${where}`,
    `  web   http://localhost:${webPort}`,
    "",
    "  WB_API_PORT=9000 npm run dev   # point the console somewhere else",
  ].join("\n");
}
