// Visual check: signs in with demo accounts and captures the main screens at
// desktop and phone widths. Run: node scripts/screenshots.mjs [baseUrl] [outDir]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const out = process.argv[3] || "evidence/screens";
mkdirSync(out, { recursive: true });

async function login(page, email, password) {
  await page.goto(`${base}/login`);
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"), { timeout: 15000 });
}

async function shot(page, name) {
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  console.log("captured", name);
}

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
const report = { horizontalOverflow: [] };
try {
  for (const [label, viewport] of [["desktop", { width: 1280, height: 860 }], ["phone", { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    await login(page, "alex.tan@demo.example", "alex-demo-2026");
    for (const [name, path] of [["overview", "/"], ["policies", "/policies"], ["gaps", "/gaps"], ["claims", "/claims"], ["assistant", "/assistant"], ["documents", "/documents"], ["settings", "/settings"]]) {
      await page.goto(`${base}${path}`);
      await page.waitForLoadState("networkidle");
      await shot(page, `${label}-${name}`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
      if (overflow) report.horizontalOverflow.push(`${label}-${name}`);
    }
    // claim detail via navigation from the claims list (router, not reload)
    await page.goto(`${base}/claims`);
    await page.waitForLoadState("networkidle");
    await page.locator("table tr.clickable").first().click();
    await page.waitForURL(/\/claims\/[0-9a-f-]+$/, { timeout: 10000 });
    await page.waitForLoadState("networkidle");
    await shot(page, `${label}-claim-detail`);
    // language switch on the overview must update server rendered labels without reload
    await page.goto(`${base}/`);
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: "中文" }).first().click();
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(800);
    await shot(page, `${label}-overview-zh`);
    report[`${label}-zh-sidebar`] = await page.evaluate(() => Array.from(document.querySelectorAll(".nav a, .bottom-nav a")).map((a) => a.textContent.trim()));
    report[`${label}-zh-actions`] = await page.evaluate(() => Array.from(document.querySelectorAll(".link-row strong")).map((a) => a.textContent.trim()));
    await page.getByRole("button", { name: "EN" }).first().click();
    await page.waitForTimeout(500);
    await ctx.close();
  }
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  await login(page, "jordan.reyes@northgate.example", "jordan-demo-2026");
  for (const [name, path] of [["insurer-dashboard", "/insurer"], ["insurer-queue", "/insurer/claims"], ["insurer-team", "/insurer/team"]]) {
    await page.goto(`${base}${path}`);
    await page.waitForLoadState("networkidle");
    await shot(page, name);
  }
  await page.goto(`${base}/insurer/claims`);
  await page.waitForLoadState("networkidle");
  await page.locator("table tr.clickable").first().click();
  await page.waitForURL(/\/insurer\/claims\/[0-9a-f-]+$/, { timeout: 10000 });
  await page.waitForLoadState("networkidle");
  await shot(page, "insurer-workspace");
  await ctx.close();
  const lp = await browser.newPage({ viewport: { width: 1280, height: 860 } });
  await lp.goto(`${base}/login`);
  await lp.waitForLoadState("networkidle");
  await shot(lp, "login");
} finally {
  await browser.close();
}
console.log(JSON.stringify(report, null, 2));
