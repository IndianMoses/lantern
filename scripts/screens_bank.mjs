// Captures the bank operations surface, the settings security and consent panels,
// the staff account page and the MFA login step (desktop and phone).
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const base = process.argv[2] || "http://127.0.0.1:8000";
const out = process.argv[3] || "evidence/screens";
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM || undefined });
async function login(page, email, password) {
  for (let attempt = 0; attempt < 4; attempt++) {
    await page.goto(`${base}/login`);
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', password);
    const [resp] = await Promise.all([page.waitForResponse((r) => r.url().endsWith("/api/auth/login")), page.click('button[type="submit"]')]);
    if (resp.status() === 429) { console.log("login rate limited, waiting 20s"); await page.waitForTimeout(20000); continue; }
    if (resp.status() !== 200) throw new Error(`login ${email} failed: ${resp.status()} ${await resp.text()}`);
    const body = await resp.json();
    if (body.mfa_required) return body;
    await page.waitForURL((u) => !u.pathname.endsWith("/login"));
    return body;
  }
  throw new Error("login kept being rate limited");
}
const shots = [];
async function shot(page, name) {
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: true });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1);
  shots.push([name, overflow]);
}
try {
  for (const [label, viewport] of [["desktop", { width: 1280, height: 900 }], ["phone", { width: 390, height: 844 }]]) {
    const ctx = await browser.newContext({ viewport });
    const page = await ctx.newPage();
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await login(page, "dana.whitfield@harbourline.example", "dana-demo-2026");
    await page.waitForURL((u) => u.pathname.startsWith("/bank"));
    await shot(page, `${label}-bank-overview`);
    await page.goto(`${base}/bank/integration`);
    await shot(page, `${label}-bank-integration`);
    await page.goto(`${base}/bank/audit`);
    await shot(page, `${label}-bank-audit`);
    await page.goto(`${base}/bank/account`);
    await page.waitForSelector("table");
    await page.getByRole("button", { name: /Set up MFA/ }).click();
    await page.waitForSelector("text=otpauth://");
    await shot(page, `${label}-bank-account-mfa-setup`);
    await ctx.close();

    const ctx2 = await browser.newContext({ viewport });
    const page2 = await ctx2.newPage();
    page2.on("pageerror", (e) => errors.push(String(e)));
    await login(page2, "alex.tan@demo.example", "alex-demo-2026");
    await page2.goto(`${base}/settings`);
    await page2.waitForSelector("text=Data sharing and consent");
    await shot(page2, `${label}-settings-consent-security`);
    await ctx2.close();
    console.log(label, "errors", errors);
  }
  // MFA login step: enrol Morgan through the API, then show the challenge screen.
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  // pick a staff account that is not yet enrolled (a previous run against the same database may have enrolled one)
  const candidates = [["morgan.clarke@northgate.example", "morgan-demo-2026"], ["nia.williams@pawfield.example", "nia-demo-2026"], ["jordan.reyes@northgate.example", "jordan-demo-2026"]];
  let staff = null;
  for (const [email, password] of candidates) {
    const body = await login(page, email, password);
    if (!body.mfa_required) { staff = { email, password }; break; }
  }
  if (!staff) throw new Error("every candidate staff account already has MFA enabled; restart with a fresh database");
  const setup = await page.evaluate(async () => {
    const me = await (await fetch("/api/auth/me")).json();
    const r = await fetch("/api/auth/mfa/setup", { method: "POST", headers: { "X-CSRF-Token": me.csrf_token } });
    return { status: r.status, ...(await r.json()) };
  });
  if (setup.status !== 200) throw new Error(`mfa setup failed: ${JSON.stringify(setup)}`);
  const code = await totp(setup.secret);
  const enabled = await page.evaluate(async (code) => {
    const me = await (await fetch("/api/auth/me")).json();
    const r = await fetch("/api/auth/mfa/enable", { method: "POST", headers: { "X-CSRF-Token": me.csrf_token, "Content-Type": "application/json" }, body: JSON.stringify({ code }) });
    const out = { status: r.status, body: await r.text() };
    await fetch("/api/auth/logout", { method: "POST", headers: { "X-CSRF-Token": me.csrf_token } });
    return out;
  }, code);
  if (enabled.status !== 200) throw new Error(`mfa enable failed: ${JSON.stringify(enabled)}`);
  const challenge = await login(page, staff.email, staff.password);
  if (!challenge.mfa_required) throw new Error("expected an MFA challenge after enrolment");
  await page.waitForSelector('input[autocomplete="one-time-code"]');
  await shot(page, "desktop-login-mfa-challenge");
  await page.fill('input[autocomplete="one-time-code"]', await totp(setup.secret));
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => u.pathname.startsWith("/insurer"));
  shots.push(["mfa-login-completed", false]);
  await ctx.close();
} finally {
  await browser.close();
}
for (const [n, o] of shots) console.log(n, "overflow", o);

// RFC 6238 TOTP in Node for the demo flow (same algorithm as the backend).
async function totp(secretB32) {
  const { createHmac } = await import("node:crypto");
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const c of secretB32.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Math.floor(Date.now() / 1000 / 30);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const h = createHmac("sha1", key).update(msg).digest();
  const off = h[h.length - 1] & 0xf;
  const bin = ((h[off] & 0x7f) << 24) | (h[off + 1] << 16) | (h[off + 2] << 8) | h[off + 3];
  return String(bin % 1_000_000).padStart(6, "0");
}
