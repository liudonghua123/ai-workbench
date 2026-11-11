/**
 * End-to-end walk-through that also produces the README screenshots.
 *
 *   node scripts/shoot.mjs            # full run
 *   node scripts/shoot.mjs --errors   # also print console/page errors
 *
 * Assumes backend on :8000 and frontend on :3000.
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";

const WEB = process.env.WEB_URL ?? "http://127.0.0.1:3000";
const API = process.env.API_URL ?? "http://127.0.0.1:8000/api/v1";
const OUT = new URL("../docs/screenshots/", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1");

const problems = [];
const steps = [];

async function shot(page, name, note) {
  await page.waitForTimeout(650);
  await page.screenshot({ path: `${OUT}${name}.png`, fullPage: false });
  steps.push(`  ✓ ${name.padEnd(22)} ${note ?? ""}`);
}

async function pickProject() {
  const res = await fetch(`${API}/projects`);
  const list = await res.json();
  // prefer a fullstack project that already has a deployment
  return (
    list.find((p) => p.type === "fullstack" && p.deployment_count > 0) ??
    list.find((p) => p.type === "fullstack") ??
    list[0]
  );
}

const run = async () => {
  if (!existsSync(OUT)) await mkdir(OUT, { recursive: true });

  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "light",
  });

  page.on("console", (msg) => {
    if (msg.type() === "error") problems.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (err) => problems.push(`pageerror: ${err.message}`));

  // 1 — dashboard
  await page.goto(WEB, { waitUntil: "networkidle" });
  await shot(page, "01-dashboard", "工作台总览");

  // 2 — templates
  await page.goto(`${WEB}/templates`, { waitUntil: "networkidle" });
  await shot(page, "02-templates", "模板目录");

  // 3 — create project dialog
  await page.goto(`${WEB}/projects`, { waitUntil: "networkidle" });
  const createBtn = page.getByRole("button", { name: /新建应用|创建应用/ }).first();
  if (await createBtn.count()) {
    await createBtn.click();
    await shot(page, "03-create", "创建项目向导");
    await page.keyboard.press("Escape");
  }

  // 4 — project detail tabs
  const project = await pickProject();
  if (!project) throw new Error("no project to open");

  for (const [tab, name] of [
    ["概览", "04-project-overview"],
    ["代码 / IDE", "05-project-ide"],
    ["部署", "06-project-deploy"],
    ["数据", "07-project-data"],
    ["设置", "08-project-settings"],
  ]) {
    await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
    const btn = page.getByRole("tab", { name: tab, exact: false }).first();
    if (await btn.count()) await btn.click();
    await shot(page, name, tab);
  }

  // 5 — registries
  for (const [path, name, note] of [
    ["/agents", "09-agents", "Agent 管理"],
    ["/mcp", "10-mcp", "MCP 管理"],
    ["/skills", "11-skills", "Skills 管理"],
    ["/events", "12-events", "事件流"],
  ]) {
    await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
    await shot(page, name, note);
  }

  // 6 — dark mode check
  await page.goto(WEB, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
    localStorage.setItem("wb-theme", "dark");
  });
  await shot(page, "13-dark", "深色主题");

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
