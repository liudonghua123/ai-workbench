#!/usr/bin/env node
/**
 * `npm run dev` in frontend/ — starts Next and tells it where the control plane
 * is. The address comes from `scripts/env.mjs` (same resolution as the root
 * launcher), because `NEXT_PUBLIC_*` is inlined at compile time: the value has
 * to be in the environment *before* Next boots.
 *
 *   npm run dev                      # API on :8000
 *   WB_API_PORT=9000 npm run dev     # API on :9000
 *   npm run dev -- --port 3001       # web on :3001, extra args pass through
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { WEB_ROOT, describe, resolveTargets } from "../../scripts/env.mjs";

let targets;
try {
  targets = resolveTargets();
} catch (err) {
  console.error(`\n  ${err.message}\n`);
  process.exit(1);
}

const nextBin = resolve(WEB_ROOT, "node_modules", "next", "dist", "bin", "next");
if (!existsSync(nextBin)) {
  console.error("\n  next is not installed — run `npm install` in frontend/ first.\n");
  process.exit(1);
}

// The root launcher already printed the same block.
if (!process.env.WB_DEV_QUIET) console.log(`\n${describe(targets)}\n`);

// Args go after ours so `npm run dev -- --port 3001` still wins.
const child = spawn(
  process.execPath,
  [nextBin, "dev", "-p", targets.webPort, ...process.argv.slice(2)],
  {
    cwd: WEB_ROOT,
    stdio: "inherit",
    env: {
      ...process.env,
      NEXT_PUBLIC_API_BASE: targets.apiBase,
      NEXT_PUBLIC_API_ORIGIN: targets.origin,
      NEXT_PUBLIC_API_HOST: targets.apiHost,
      NEXT_PUBLIC_API_PORT: targets.apiPort,
    },
  },
);

const forward = (signal) => () => {
  if (!child.killed) child.kill(signal);
};
process.on("SIGINT", forward("SIGINT"));
process.on("SIGTERM", forward("SIGTERM"));
child.on("exit", (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});
