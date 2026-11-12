/**
 * End-to-end walk-through that also produces the README screenshots.
 *
 *   node scripts/shoot.mjs
 *
 * Creates one browser context per preference combination (locale / nav / theme)
 * so localStorage state never leaks between captures. Assumes the backend is on
 * :8000 and the frontend on :3000.
 *
 * The backend runs with WB_AUTH_ENABLED=true, so the script signs in with the
 * demo account (WB_USER / WB_PASS, default owner@acme.test) and pins that session
 * to WB_ORG. Every API call carries the bearer token and every browser context
 * seeds it into localStorage — the same path a human takes.
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const WEB = process.env.WEB_URL ?? "http://127.0.0.1:3000";
const API = process.env.API_URL ?? "http://127.0.0.1:8000/api/v1";
const OUT = fileURLToPath(new URL("../docs/screenshots/", import.meta.url));

const CREDS = {
  email: process.env.WB_USER ?? "owner@acme.test",
  password: process.env.WB_PASS ?? "workbench-demo-1",
  org: process.env.WB_ORG ?? "default",
};

const problems = [];
const steps = [];
// set while a capture deliberately provokes an error (the 401 redirect shot)
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

/**
 * Signs in over the API and returns the bearer token. The token is a session, so
 * the organization it is pinned to decides what the screenshots show — switch to
 * WB_ORG before capturing anything.
 */
async function signIn() {
  const res = await fetch(`${API}/auth/local/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: CREDS.email, password: CREDS.password }),
  }).catch(() => null);
  if (!res?.ok) {
    problems.push(`sign-in failed (${res?.status ?? "network"}) — run backend/tools/access_demo.py`);
    return "";
  }
  const { token } = await res.json();
  const headers = { authorization: `Bearer ${token}` };
  const orgs = await fetch(`${API}/orgs`, { headers })
    .then((r) => r.json())
    .catch(() => []);
  const org = orgs.find((item) => item.slug === CREDS.org) ?? orgs[0];
  if (org) {
    await fetch(`${API}/auth/switch-org`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ org_id: org.id }),
    });
    steps.push(`  · signed in as ${CREDS.email} → ${org.name} (${org.role_key})`);
  }
  return token;
}

/**
 * Sign in a different demo account and pin it to an org, returning its token.
 * Used for the data-scope capture: "what a developer sees" is a different view of
 * the same API, not a different app.
 */
async function signInAs(email, orgSlug = CREDS.org) {
  const res = await fetch(`${API}/auth/local/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password: CREDS.password }),
  }).catch(() => null);
  if (!res?.ok) {
    problems.push(`sign-in failed for ${email} (${res?.status ?? "network"})`);
    return "";
  }
  const { token } = await res.json();
  const headers = { authorization: `Bearer ${token}` };
  const orgs = await fetch(`${API}/orgs`, { headers }).then((r) => r.json()).catch(() => []);
  const org = orgs.find((item) => item.slug === orgSlug) ?? orgs[0];
  if (org) {
    await fetch(`${API}/auth/switch-org`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ org_id: org.id }),
    });
  }
  return token;
}

/** fetch wrapper that always carries the session token */
let AUTH = {};
const apiFetch = (path, init = {}) =>
  fetch(`${API}${path}`, { ...init, headers: { ...AUTH, ...(init.headers ?? {}) } });

async function openTab(page, projectId, tab) {
  await page.goto(`${WEB}/projects/${projectId}`, { waitUntil: "networkidle" });
  await page.getByRole("tab").nth(TAB[tab]).click();
  await page.waitForTimeout(500);
}

async function pickProject() {
  const list = await apiFetch("/projects").then((r) => r.json());
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
  await apiFetch(`/projects/${projectId}/preview/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: "{}",
  }).catch(() => undefined);

  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const state = await apiFetch(`/projects/${projectId}/preview`).then((r) => r.json());
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
async function context(
  browser,
  { locale = "en", nav = "rail", theme = "light", token = "", phone = false } = {},
) {
  const ctx = await browser.newContext({
    viewport: phone ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    isMobile: phone,
    hasTouch: phone,
    colorScheme: theme === "dark" ? "dark" : "light",
  });
  await ctx.addInitScript(
    ({ locale, nav, theme, token }) => {
      localStorage.setItem("wb-locale", locale);
      localStorage.setItem("wb-nav", nav);
      localStorage.setItem("wb-theme", theme);
      if (token) localStorage.setItem("wb-token", token);
    },
    { locale, nav, theme, token },
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

  const token = await signIn();
  AUTH = token ? { authorization: `Bearer ${token}` } : {};

  const browser = await chromium.launch();
  const project = await pickProject();
  if (!project) throw new Error("no project to open");

  // ---------------------------------------------------------------- english
  {
    const { ctx, page } = await context(browser, { token });

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
    await apiFetch(`/projects/${project.id}/file`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        path: "docs/release-notes.md",
        content: `# Release notes\n\n- preview environment at ${project.preview?.fqdn ?? "<slug>-dev"}\n`,
      }),
    });
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
    const triggered = await apiFetch(`/projects/${project.id}/deployments`, {
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
    const { ctx, page } = await context(browser, { theme: "dark", token });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "19-dark", "dark theme");
    await ctx.close();
  }

  // -------------------------------------------------------------- top nav
  {
    const { ctx, page } = await context(browser, { nav: "top", token });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "20-nav-top", "nav in top bar");
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "21-nav-top-detail", "top nav + detail");
    await ctx.close();
  }

  // --------------------------------------------------------------- chinese
  {
    const { ctx, page } = await context(browser, { locale: "zh", token });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "22-dashboard-zh", "overview (zh)");
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "23-project-zh", "project detail (zh)");
    await page.goto(`${WEB}/agents`, { waitUntil: "networkidle" });
    await shot(page, "24-agents-zh", "agents (zh)");
    await page.goto(`${WEB}/settings`, { waitUntil: "networkidle" });
    await shot(page, "25-settings-zh", "system settings (zh)");

    // access console in Chinese, one tab per screenshot
    for (const [tab, index, name] of [
      ["members", 0, "37-access-zh-members"],
      ["roles", 1, "38-access-zh-roles"],
      ["sso", 2, "39-access-zh-sso"],
      ["audit", 3, "40-access-zh-audit"],
    ]) {
      await page.goto(`${WEB}/access`, { waitUntil: "networkidle" });
      await page.getByRole("tab").nth(index).click();
      await page.waitForTimeout(600);
      await shot(page, name, `access · ${tab} (zh)`);
    }
    await ctx.close();
  }

  // --------------------------------------------------------- access control
  {
    const { ctx, page } = await context(browser, { token });

    await page.goto(`${WEB}/access`, { waitUntil: "networkidle" });
    await shot(page, "26-access-members", "members + project grants");

    await page.getByRole("tab").nth(1).click();
    await page.waitForTimeout(600);
    await shot(page, "27-access-roles", "roles: a permission set, nothing more");

    // open the role editor so the permission grid is visible
    await page.getByRole("button", { name: "Edit" }).first().click();
    await page.waitForTimeout(500);
    await shot(page, "28-access-role-editor", "role editor driven by the catalog");

    await page.goto(`${WEB}/access`, { waitUntil: "networkidle" });
    await page.getByRole("tab").nth(2).click();
    await page.waitForTimeout(600);
    await shot(page, "29-access-sso", "five identity providers, one status column");

    await page.getByRole("button", { name: /Add connection|新增连接/ }).first().click();
    await page.waitForTimeout(500);
    await shot(page, "30-access-sso-form", "SSO form rendered from the schema");

    await page.goto(`${WEB}/access`, { waitUntil: "networkidle" });
    await page.getByRole("tab").nth(3).click();
    await page.waitForTimeout(600);
    await shot(page, "31-access-audit", "audit trail including denials");

    await page.getByRole("tab").nth(4).click();
    await page.waitForTimeout(600);
    await shot(page, "32-access-settings", "organization, sessions, password");

    // org switcher + account menu
    await page.goto(WEB, { waitUntil: "networkidle" });
    await page.locator(".session-menu .btn").first().click();
    await page.waitForTimeout(400);
    await shot(page, "33-org-switcher", "one session, several tenants");

    await ctx.close();
  }

  // ------------------------------------------------------------ dark console
  {
    const { ctx, page } = await context(browser, { theme: "dark", token });
    await page.goto(`${WEB}/access`, { waitUntil: "networkidle" });
    await shot(page, "35-access-dark", "access console, dark theme");
    await ctx.close();
  }

  // -------------------------------------------------- data scope (own only)
  {
    // alan is a developer with data_scope=own: he sees only the project that was
    // granted to him, and the page says so instead of listing an empty org
    const devToken = await signInAs("alan@acme.test");
    if (devToken) {
      const { ctx, page } = await context(browser, { token: devToken });
      await page.goto(`${WEB}/projects`, { waitUntil: "networkidle" });
      await shot(page, "41-scope-own", "own scope: only your projects plus grants");
      await ctx.close();
    }
  }

  // ---------------------------------------------------------------- phone
  {
    // the same app at 390×844: the rail becomes a bottom bar and every two-column
    // region stacks, so a capture here is the cheapest responsive regression test
    const { ctx, page } = await context(browser, { locale: "zh", phone: true, token });

    await page.goto(`${WEB}/projects`, { waitUntil: "networkidle" });
    await shot(page, "42-phone-projects", "phone: projects + bottom bar");

    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "43-phone-detail", "phone: project detail");

    await page.goto(`${WEB}/access`, { waitUntil: "networkidle" });
    await shot(page, "44-phone-access", "phone: access console");

    await ctx.close();
  }

  // ---------------------------------------------------------------- gateway
  {
    // These two are not Next pages: :80 is the reverse proxy. If it is not running
    // the shots are skipped with a note rather than capturing a connection error.
    const up = await fetch("http://localhost/__gateway/health")
      .then((res) => res.ok)
      .catch(() => false);
    if (!up) {
      problems.push("gateway not listening on :80 — gateway screenshots skipped");
    } else {
      const { ctx, page } = await context(browser, { token });

      await page.goto("http://localhost/__gateway/", { waitUntil: "networkidle" });
      await shot(page, "45-gateway", "gateway: every host it routes");

      // A project whose production port nothing listens on: the gateway's own page,
      // not a bare 502. In simulate mode that is every project, so it is stable —
      // and the 502 itself is the subject, so its console error is not a problem.
      quiet = true;
      await page.goto("http://customer-success.localtest.me/", { waitUntil: "networkidle" })
        .catch(() => undefined);
      await shot(page, "46-gateway-down", "a stopped project explains itself");
      quiet = false;

      // a tight crop of the address strip: the two URLs are the whole point
      await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "domcontentloaded" });
      await page.locator(".addr-row").waitFor({ timeout: 15_000 })
        .catch(() => problems.push("address strip missing on the project page"));
      await page.waitForTimeout(500);
      const head = await page.locator(".page-head").boundingBox().catch(() => null);
      if (head) {
        await page.screenshot({
          path: `${OUT}47-project-addresses.png`,
          clip: { x: head.x - 8, y: head.y - 8, width: head.width + 16, height: head.height + 16 },
        });
        steps.push("  ✓ 47-project-addresses     two hosts, no ports");
      } else {
        problems.push("could not crop the address strip");
      }
      await ctx.close();
    }
  }

  // ------------------------------------------------------------- signed out
  {
    // the 401s here are the point of the capture
    quiet = true;
    const { ctx, page } = await context(browser);
    // the login page keeps no long-lived connections, but Next's prefetching makes
    // networkidle unreliable here — wait for the panel itself instead
    await page.goto(`${WEB}/login`, { waitUntil: "domcontentloaded" });
    await page.locator(".auth-panel").waitFor({ timeout: 15_000 })
      .catch(() => problems.push("login panel never rendered"));
    await shot(page, "34-login", "sign in: SSO first, password second");

    // an expired session must land on the same page, not on a broken dashboard
    await page.goto(`${WEB}/access`, { waitUntil: "domcontentloaded" });
    await page.waitForURL(/\/login/, { timeout: 10_000 }).catch(() => problems.push("401 did not redirect to /login"));
    await shot(page, "36-auth-redirect", "a rejected session lands on /login");
    // close first: the 401 that triggered the redirect may still be in flight
    await ctx.close();
    quiet = false;
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
