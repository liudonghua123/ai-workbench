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

const TABS = ["overview", "ide", "deploy", "data", "settings"];

async function pickProject() {
  const res = await fetch(`${API}/projects`);
  const list = await res.json();
  return (
    list.find((p) => p.type === "fullstack" && p.deployment_count > 0) ??
    list.find((p) => p.type === "fullstack") ??
    list[0]
  );
}

function watch(page) {
  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));
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

    for (const [index, name] of TABS.entries()) {
      await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
      await page.getByRole("tab").nth(index).click();
      await shot(page, `0${4 + index}-project-${name}`, name);
    }

    for (const [path, name, note] of [
      ["/agents", "09-agents", "agents"],
      ["/mcp", "10-mcp", "mcp servers"],
      ["/skills", "11-skills", "skills"],
      ["/events", "12-events", "events"],
      ["/settings", "20-settings", "system settings"],
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
      await page.getByRole("tab").nth(2).click();
      await page.waitForTimeout(2200);
      const terminal = await page.locator(".terminal").first().innerText().catch(() => "");
      if (!terminal.includes("[")) problems.push("deploy log stayed empty — SSE may be broken");
      await shot(page, "21-deploy-live", "deployment streaming over SSE");
    }

    // preferences popover
    await page.goto(WEB, { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "Preferences" }).click();
    await shot(page, "13-preferences", "preferences popover");

    await ctx.close();
  }

  // ------------------------------------------------------------ dark theme
  {
    const { ctx, page } = await context(browser, { theme: "dark" });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "14-dark", "dark theme");
    await ctx.close();
  }

  // -------------------------------------------------------------- top nav
  {
    const { ctx, page } = await context(browser, { nav: "top" });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "15-nav-top", "nav in top bar");
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "16-nav-top-detail", "top nav + detail");
    await ctx.close();
  }

  // --------------------------------------------------------------- chinese
  {
    const { ctx, page } = await context(browser, { locale: "zh" });
    await page.goto(WEB, { waitUntil: "networkidle" });
    await shot(page, "17-dashboard-zh", "overview (zh)");
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    await shot(page, "18-project-zh", "project detail (zh)");
    await page.goto(`${WEB}/agents`, { waitUntil: "networkidle" });
    await shot(page, "19-agents-zh", "agents (zh)");
    await page.goto(`${WEB}/settings`, { waitUntil: "networkidle" });
    await shot(page, "22-settings-zh", "system settings (zh)");
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
