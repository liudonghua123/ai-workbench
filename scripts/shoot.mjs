/**
 * End-to-end walk-through that also produces the README screenshots.
 *
 *   node scripts/shoot.mjs
 *
 * Creates one browser context per preference combination (locale / nav / theme)
 * so localStorage state never leaks between captures. Assumes the backend is on
 * :8000 and the frontend on :3000.
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const WEB = process.env.WEB_URL ?? "http://127.0.0.1:3000";
const API = process.env.API_URL ?? "http://127.0.0.1:8000/api/v1";
const OUT = fileURLToPath(new URL("../docs/screenshots/", import.meta.url));

const problems = [];
const steps = [];
// set while a capture deliberately provokes an error (the 401 token-gate shot)
let quiet = false;

// project page tab order (see app/projects/[id]/page.tsx)
const TAB = {
  overview: 0,
  code: 1,
  preview: 2,
  version: 3,
  deploy: 4,
  data: 5,
  conventions: 6,
  settings: 7,
};

async function openTab(page, projectId, tab) {
  await page.goto(`${WEB}/projects/${projectId}`, { waitUntil: "networkidle" });
  await page.getByRole("tab").nth(TAB[tab]).click();
  await page.waitForTimeout(500);
}

async function pickProject() {
  const res = await fetch(`${API}/projects`);
  const list = await res.json();
  return (
    list.find((p) => p.type === "fullstack" && p.deployment_count > 0) ??
    list.find((p) => p.type === "fullstack") ??
    list[0]
  );
}

/**
 * Make sure the preview really answers before pointing a camera at it.
 * Restarts unconditionally: a stale `running` row from a previous run is worse than
 * a wasted second, and a fresh one also exercises the run → SSE → iframe path.
 */
async function waitForPreview(projectId, timeoutMs = 45_000) {
  await fetch(`${API}/projects/${projectId}/preview/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  }).catch(() => undefined);

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await fetch(`${API}/projects/${projectId}/preview`).then((r) => r.json());
    if (state.status === "running") return true;
    if (state.status === "failed") {
      problems.push(`preview failed: ${state.latest?.error ?? "unknown"}`);
      return false;
    }
    await new Promise((r) => setTimeout(r, 900));
  }
  return false;
}

function watch(page) {
  page.on("console", (msg) => {
    if (quiet) return;
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => {
    if (quiet) return;
    problems.push(`pageerror: ${err.message}`);
  });
}

/** Fresh context with the given preferences already in localStorage. */
async function context(browser, { locale = "en", nav = "rail", theme = "light" } = {}) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "light",
  });
  await ctx.addInitScript(
    ({ locale, nav, theme }) => {
      localStorage.setItem("wb-locale", locale);
      localStorage.setItem("wb-nav", nav);
      localStorage.setItem("wb-theme", theme);
    },
    { locale, nav, theme },
  );
  const page = await ctx.newPage();
  watch(page);
  return { ctx, page };
}

async function shot(page, name, note) {
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: false });
  steps.push(`  ✓ ${name.padEnd(24)} ${note ?? ""}`);
}

const run = async () => {
  if (!existsSync(OUT)) await mkdir(OUT, { recursive: true });

  const browser = await chromium.launch();
  const project = await pickProject();
  if (!project) throw new Error("no project to open");

  // ---------------------------------------------------------------- english
  {
    const { ctx, page } = await context(browser);

    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "01-dashboard", "overview (en)");

    await page.goto(`${WEB}/templates`, { waitUntil: "networkidle" });
    await shot(page, "02-templates", "templates");

    await page.goto(`${WEB}/projects`, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: /New app|新建应用/ }).first().click();
    await shot(page, "03-create", "create dialog");
    await page.keyboard.press("Escape");
    await page.reload({ waitUntil: "networkidle" });

    // ---- project page: overview → code → preview → version → deploy → data
    await openTab(page, project.id, "overview");
    await shot(page, "04-project-overview", "overview");

    await openTab(page, project.id, "code");
    await page.locator(".code-tree button", { hasText: "App.tsx" }).first().click();
    await page.waitForTimeout(600);
    await shot(page, "05-project-code", "code editor + tree");

    await openTab(page, project.id, "preview");
    // the panel only mounts the iframe once the preview really is running, so drive
    // it through the API and wait for `running` before capturing — otherwise the
    // frame shows the browser's dead-port page and the shot is a white rectangle.
    const started = await waitForPreview(project.id);
    if (!started) problems.push(`preview for ${project.slug} never reached running`);
    await openTab(page, project.id, "preview");
    await page.locator(".preview-frame").waitFor({ timeout: 8000 }).catch(() => problems.push("preview iframe missing"));
    await page.waitForTimeout(1500);
    await shot(page, "06-project-preview", "debug environment on <slug>-dev");

    // one uncommitted edit so the version panel has something to show
    const file = await fetch(`${API}/projects/${project.id}/file?path=README.md`).then((r) => r.json());
    await fetch(`${API}/projects/${project.id}/file`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        path: "docs/release-notes.md",
        content: `# Release notes\n\n- preview environment at ${project.preview?.fqdn ?? "<slug>-dev"}\n`,
      }),
    });
    void file;
    await openTab(page, project.id, "version");
    await page.locator(".change-row").first().click().catch(() => undefined);
    await page.waitForTimeout(500);
    await shot(page, "07-project-version", "version control + diff");

    await openTab(page, project.id, "conventions");
    await shot(page, "08-project-conventions", "type spec + conformance report");

    await openTab(page, project.id, "deploy");
    await shot(page, "09-project-deploy", "deploy history");

    await openTab(page, project.id, "data");
    await shot(page, "10-project-data", "data console");

    await openTab(page, project.id, "settings");
    await shot(page, "11-project-settings", "project settings");

    for (const [path, name, note] of [
      ["/agents", "12-agents", "agents"],
      ["/mcp", "13-mcp", "mcp servers"],
      ["/skills", "14-skills", "skills"],
      ["/events", "15-events", "events"],
      ["/settings", "16-settings", "system settings"],
    ]) {
      await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
      await shot(page, name, note);
    }

    // live deploy: trigger one through the API, then watch the SSE stream fill the log
    const triggered = await fetch(`${API}/projects/${project.id}/deployments`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });
    if (triggered.ok) {
      await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
      await page.getByRole("tab").nth(4).click();
      await page.waitForTimeout(2200);
      const terminal = await page.locator(".terminal").first().innerText().catch(() => "");
      if (!terminal.includes("[")) problems.push("deploy log stayed empty — SSE may be broken");
      await shot(page, "17-deploy-live", "deployment streaming over SSE");
    }

    // preferences popover
    await page.goto(WEB, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Preferences" }).click();
    await shot(page, "18-preferences", "preferences popover");

    await ctx.close();
  }

  // ------------------------------------------------------------ dark theme
  {
    const { ctx, page } = await context(browser, { theme: "dark" });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "19-dark", "dark theme");
    await ctx.close();
  }

  // -------------------------------------------------------------- top nav
  {
    const { ctx, page } = await context(browser, { nav: "top" });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "20-nav-top", "nav in top bar");
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "21-nav-top-detail", "top nav + detail");
    await ctx.close();
  }

  // --------------------------------------------------------------- chinese
  {
    const { ctx, page } = await context(browser, { locale: "zh" });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "22-dashboard-zh", "overview (zh)");
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "23-project-zh", "project detail (zh)");
    await page.goto(`${WEB}/agents`, { waitUntil: "networkidle" });
    await shot(page, "24-agents-zh", "agents (zh)");
    await page.goto(`${WEB}/settings`, { waitUntil: "networkidle" });
    await shot(page, "25-settings-zh", "system settings (zh)");
    await ctx.close();
  }

  // ------------------------------------------------------------ token gate
  // Forced with a route stub instead of flipping WB_AUTH_ENABLED, so the capture
  // is deterministic and the rest of the run keeps talking to a public API.
  {
    quiet = true;
    const { ctx, page } = await context(browser);
    await ctx.route(`${API}/**`, (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "missing or invalid token" }),
      }),
    );
    await page.goto(`${WEB}/projects`, { waitUntil: "networkidle" });
    await page.getByRole("dialog").waitFor({ timeout: 8000 }).catch(() => problems.push("token gate never opened"));
    await shot(page, "26-token-gate", "401 anywhere opens the token gate");
    quiet = false;
    await ctx.close();
  }

  await browser.close();

  console.log(steps.join("\n"));
  if (problems.length) {
    console.log(`\n${problems.length} problem(s):`);
    for (const p of [...new Set(problems)]) console.log(`  ! ${p}`);
    process.exitCode = 1;
  } else {
    console.log("\nno console/page errors");
  }
};

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
