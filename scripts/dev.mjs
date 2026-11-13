#!/usr/bin/env node
/**
 * `npm run dev` at the repo root — control plane and console in one terminal.
 *
 *   npm run dev                 # API :8000 + web :3000, both restart on save
 *   WB_API_PORT=9000 npm run dev # API :9000, console follows automatically
 *   npm run dev --only=api      # backend only
 *   npm run dev --only=web      # console only
 *
 * The port is resolved once in `scripts/env.mjs` and then used twice: as
 * `uvicorn --port` and as `NEXT_PUBLIC_API_BASE` for the browser. Two places,
 * one value — a console pointing at a port the API never opened is the exact
 * failure this file exists to prevent.
 *
 * Extra args are forwarded to the child that makes sense (`--only=web` → Next),
 * so `npm run dev --only=web -- --port 3001` works too.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { BACKEND_ROOT, WEB_ROOT, describe, resolveTargets } from "./env.mjs";

const DEFAULT_PYTHON = process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python";

function findPython() {
  const candidates = [process.env.WB_PYTHON, resolve(BACKEND_ROOT, DEFAULT_PYTHON)].filter(Boolean);
  const found = candidates.find((path) => existsSync(path));
  if (found) return found;
  console.error(
    "\n  No Python environment for the backend.\n" +
      `  Expected ${resolve(BACKEND_ROOT, DEFAULT_PYTHON)}\n` +
      "  Run `uv sync` in backend/ (or set WB_PYTHON to an interpreter with the deps installed).\n",
  );
  process.exit(1);
}

let targets;
try {
  targets = resolveTargets();
} catch (err) {
  console.error(`\n  ${err.message}\n`);
  process.exit(1);
}

// `--only=api|web` picks a half; everything else belongs to the web dev server.
const only = (process.argv.find((arg) => arg.startsWith("--only=")) ?? "").split("=")[1] || "both";
if (!["both", "api", "web"].includes(only)) {
  console.error(`\n  --only=${only} is not a target. Use --only=api or --only=web.\n`);
  process.exit(1);
}
const passthrough = process.argv.slice(2).filter((arg) => !arg.startsWith("--only="));

const children = [];
let stopping = false;

/** Windows has no SIGTERM for a process tree: taskkill /T is the honest way. */
function terminate(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") {
    spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

function shutdown(code) {
  if (stopping) return;
  stopping = true;
  for (const child of children) terminate(child);
  process.exit(code);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

function track(child, label) {
  children.push(child);
  // One side dying is not a state worth serving: the console without an API is
  // a wall of error cards, the API without a console is a tab someone forgot.
  child.on("exit", (code) => {
    if (!stopping) {
      console.log(`\n  ${label} exited (${code ?? 0}) — shutting the other one down.`);
    }
    shutdown(code ?? 1);
  });
  child.on("error", (err) => {
    console.error(`  ${label} failed to start: ${err.message}`);
    shutdown(1);
  });
}

console.log(`\n${describe(targets)}\n`);

if (only !== "web") {
  const python = findPython();
  // Access logs stay on: during development the request stream is the feedback.
  const api = spawn(
    python,
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--host",
      targets.apiHost,
      "--port",
      targets.apiPort,
      "--reload",
    ],
    {
      cwd: BACKEND_ROOT,
      stdio: "inherit",
      env: {
        ...process.env,
        WB_API_HOST: targets.apiHost,
        WB_API_PORT: targets.apiPort,
      },
    },
  );
  track(api, "api");
}

if (only !== "api") {
  const web = spawn(process.execPath, [resolve(WEB_ROOT, "scripts", "dev.mjs"), ...passthrough], {
    cwd: WEB_ROOT,
    stdio: "inherit",
    env: {
      ...process.env,
      WB_API_HOST: targets.apiHost,
      WB_API_PORT: targets.apiPort,
      WB_WEB_PORT: targets.webPort,
      // banner already printed above
      WB_DEV_QUIET: "1",
    },
  });
  track(web, "web");
}
