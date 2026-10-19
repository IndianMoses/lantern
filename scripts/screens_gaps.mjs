// Captures the coverage analysis workspace after running an analysis (desktop and phone).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const out = process.argv[3] || "evidence/screens";
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
async function login(page, email, password) {
  await page.goto(`${base}/login`);
  await page.fill('input[type="email"]', email);
  await page.fill('input[type="password"]', password);
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => !u.pathname.endsWith("/login"));
}
try {
  for (const [label, viewport] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport });
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await login(page, "alex.tan@demo.example", "alex-demo-2026");
    await page.goto(`${base}/gaps`);
    await page.waitForLoadState("networkidle");
    await page.getByRole("button", { name: /Run analysis|Run again/ }).click();
    await page.waitForSelector(".finding-card", { timeout: 20000 });
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${out}/${label}-gaps-workspace.png`, fullPage: true });
    // open the first finding and select a graph node
    await page.locator(".finding-card .head button").first().click();
    await page.waitForTimeout(300);
    if (label === "desktop") {
      await page.locator("svg[role=application] g[role=button]").first().click();
      await page.waitForTimeout(300);
    }
    await page.screenshot({ path: `${out}/${label}-gaps-detail.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
    console.log(label, "overflow", overflow, "errors", errors);
    await ctx.close();
  }
} finally {
  await browser.close();
}
