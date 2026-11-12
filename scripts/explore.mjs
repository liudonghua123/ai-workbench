/**
 * Interaction walk-through for hands-on QA (not for README screenshots).
 *
 *   node scripts/explore.mjs [--mobile]
 *
 * Drives the real UI the way a person would: clicks, types, submits. Captures a
 * screenshot per step into .qa/ so the result can be eyeballed, and reports every
 * console error plus any element that overflows the viewport horizontally.
 *
 * --mobile switches to a 390×844 viewport (iPhone-ish) to smoke-test responsive
 * layouts, which the README captures never exercise.
 */
import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

const WEB = process.env.WEB_URL ?? "http://127.0.0.1:3000";
const API = process.env.API_URL ?? "http://127.0.0.1:8000/api/v1";
const OUT = fileURLToPath(new URL("../.qa/", import.meta.url));
const MOBILE = process.argv.includes("--mobile");
const TAG = MOBILE ? "m" : "d";

const CREDS = {
  email: process.env.WB_USER ?? "owner@acme.test",
  password: process.env.WB_PASS ?? "workbench-demo-1",
  org: process.env.WB_ORG ?? "default",
};

const problems = [];
const notes = [];

async function signIn() {
  const res = await fetch(`${API}/auth/local/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email: CREDS.email, password: CREDS.password }),
  }).catch(() => null);
  if (!res?.ok) throw new Error(`sign-in failed: ${res?.status ?? "network"}`);
  const { token } = await res.json();
  const headers = { authorization: `Bearer ${token}` };
  const orgs = await fetch(`${API}/orgs`, { headers }).then((r) => r.json());
  const org = orgs.find((o) => o.slug === CREDS.org) ?? orgs[0];
  if (org) {
    await fetch(`${API}/auth/switch-org`, {
      method: "POST",
      headers: { ...headers, "content-type": "application/json" },
      body: JSON.stringify({ org_id: org.id }),
    });
  }
  return token;
}

/**
 * Anything wider than the viewport means the page scrolls sideways on a phone —
 * the single most common responsive bug, and invisible in a 1440px screenshot.
 */
async function overflow(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const docScroll = document.documentElement.scrollWidth;
    const offenders = [];
    if (docScroll > vw + 1) {
      for (const el of document.querySelectorAll("body *")) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (r.right > vw + 2 || r.left < -2) {
          const style = getComputedStyle(el);
          if (style.visibility === "hidden" || style.display === "none") continue;
          offenders.push({
            tag: el.tagName.toLowerCase(),
            cls: (el.className || "").toString().slice(0, 60),
            left: Math.round(r.left),
            right: Math.round(r.right),
          });
        }
      }
    }
    return { vw, docScroll, offenders: offenders.slice(0, 6) };
  });
}

/** Tap target audit: interactive things smaller than 32px are hard to hit. */
async function smallTargets(page) {
  return page.evaluate(() => {
    const found = [];
    for (const el of document.querySelectorAll("button, a, [role=tab], input, select")) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.height < 30 || r.width < 24) {
        found.push({
          tag: el.tagName.toLowerCase(),
          text: (el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 24),
          w: Math.round(r.width),
          h: Math.round(r.height),
        });
      }
    }
    return found.slice(0, 8);
  });
}

const run = async () => {
  if (!existsSync(OUT)) await mkdir(OUT, { recursive: true });
  const token = await signIn();

  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: MOBILE ? { width: 390, height: 844 } : { width: 1440, height: 900 },
    deviceScaleFactor: MOBILE ? 2 : 1,
    isMobile: MOBILE,
    hasTouch: MOBILE,
    locale: "zh-CN",
  });
  await ctx.addInitScript((t) => {
    localStorage.setItem("wb-locale", "zh");
    localStorage.setItem("wb-nav", "rail");
    localStorage.setItem("wb-theme", "light");
    if (t) localStorage.setItem("wb-token", t);
  }, token);

  const page = await ctx.newPage();
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`console: ${m.text().slice(0, 160)}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message.slice(0, 160)}`));

  let n = 0;
  const shot = async (name) => {
    n += 1;
    await page.waitForTimeout(450);
    await page.screenshot({ path: `${OUT}${TAG}${String(n).padStart(2, "0")}-${name}.png`, fullPage: false });
  };

  const visit = async (path) => {
    await page.goto(`${WEB}${path}`, { waitUntil: "networkidle" });
    const o = await overflow(page);
    if (o.docScroll > o.vw + 1) {
      problems.push(
        `overflow ${path}: doc ${o.docScroll} > vw ${o.vw} · ` +
          o.offenders.map((x) => `${x.tag}.${x.cls}[${x.left},${x.right}]`).join(" | "),
      );
    }
    return o;
  };

  const list = await fetch(`${API}/projects`, { headers: { authorization: `Bearer ${token}` } })
    .then((r) => r.json())
    .catch(() => []);
  const project = list[0];
  notes.push(`projects visible: ${list.length}`);

  // ---------------------------------------------------------------- pages
  const pages = [
    ["/", "dashboard"],
    ["/projects", "projects"],
    ["/templates", "templates"],
    ["/agents", "agents"],
    ["/mcp", "mcp"],
    ["/skills", "skills"],
    ["/events", "events"],
    ["/settings", "settings"],
    ["/access", "access"],
  ];
  if (project) pages.splice(2, 0, [`/projects/${project.id}`, "project"]);
  for (const [path, name] of pages) {
    await visit(path);
    await shot(name);
  }

  // ------------------------------------------------------- project tabs
  if (project) {
    const tabs = page.getByRole("tab");
    const count = await tabs.count();
    notes.push(`project tabs: ${count}`);
    for (let i = 0; i < count; i += 1) {
      await page.goto(`${WEB}/projects/${project.id}`, { waitUntil: "networkidle" });
      await tabs.nth(i).click().catch(() => undefined);
      await page.waitForTimeout(600);
      const o = await overflow(page);
      if (o.docScroll > o.vw + 1) {
        problems.push(
          `overflow project tab ${i}: ${o.docScroll} > ${o.vw} · ` +
            o.offenders.map((x) => `${x.tag}.${x.cls}[${x.left},${x.right}]`).join(" | "),
        );
      }
      await shot(`project-tab${i}`);
    }
  }

  // ------------------------------------------------------- tap targets
  await page.goto(`${WEB}/projects`, { waitUntil: "networkidle" });
  const small = await smallTargets(page);
  if (small.length) {
    notes.push(`small targets on /projects: ${small.map((s) => `${s.tag}(${s.text})${s.w}×${s.h}`).join(", ")}`);
  }

  await browser.close();

  console.log(notes.map((s) => `  · ${s}`).join("\n"));
  console.log(`\n${n} screenshots → .qa/`);
  if (problems.length) {
    console.log(`\n${problems.length} problem(s):`);
    for (const p of [...new Set(problems)]) console.log(`  ! ${p}`);
  } else {
    console.log("\nno overflow, no console errors");
  }
};

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
