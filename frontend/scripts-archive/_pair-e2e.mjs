import { chromium } from "@playwright/test";
import fs from "node:fs";

const KEY = process.env.EB_PAIR_KEY || "9032-670D-CAFC";
const parts = KEY.replace(/^EB-/, "").split("-");
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`PAGEERROR ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") problems.push(`CONSOLE ${m.text().slice(0, 300)}`);
});
page.on("requestfailed", (r) =>
  problems.push(`FAILED ${r.method()} ${r.url()} :: ${r.failure()?.errorText}`),
);
page.on("response", async (r) => {
  if (r.url().includes(":7443") && r.status() >= 400) {
    let body = "";
    try {
      body = (await r.text()).slice(0, 300);
    } catch {}
    problems.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()} :: ${body}`);
  }
});

await page.goto("http://localhost:8443/connect", {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});
await page.locator("select").selectOption("direct");
await page.waitForTimeout(300);
const inputs = page.locator('input[aria-label^="Key segment"]');
for (let i = 0; i < 3; i++) {
  await inputs.nth(i).fill(parts[i] ?? "");
}
console.log("key filled", parts);
await page.locator("button.full-width").click({ force: true });

try {
  await page.waitForURL("**/app", { timeout: 30000 });
  console.log("NAVIGATED /app");
} catch (e) {
  console.log("NAV FAILED:", e.message);
  const err = await page.locator('[role="alert"]').allInnerTexts().catch(() => []);
  console.log("ALERTS:", err);
}

await page.waitForTimeout(9000);
const text = await page.locator("body").innerText();
console.log("TEXTLEN", text.length);
console.log("TEXT:", text.replace(/\n+/g, " | ").slice(0, 900));
const storage = await page.evaluate(() => {
  const out = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    out[k] = localStorage.getItem(k);
  }
  return out;
});
console.log("STORAGE KEYS", Object.keys(storage));
fs.writeFileSync("/tmp/opencode/paired-storage.json", JSON.stringify(storage, null, 2));
await context.storageState({ path: "/tmp/opencode/paired-state.json" });
await page.screenshot({ path: "/tmp/opencode/paired-app.png", fullPage: false });
console.log("PROBLEMS:", problems.length ? problems.slice(0, 20).join("\n") : "none");
await browser.close();
