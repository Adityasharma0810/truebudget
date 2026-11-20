import { chromium } from "@playwright/test";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
const consoleErrors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
await page.goto("http://localhost:8443/app", { waitUntil: "networkidle", timeout: 30000 });
await page.waitForTimeout(4000);
const body = await page.locator("body").innerText();
console.log("URL:", page.url());
console.log("BODY_HEAD:", body.slice(0, 1500).replace(/\n+/g, " | "));
console.log("PAGE_ERRORS:", JSON.stringify(errors, null, 2));
console.log("CONSOLE_ERRORS:", JSON.stringify(consoleErrors.slice(0, 20), null, 2));
await page.screenshot({ path: "/tmp/opencode/app.png", fullPage: true });
await browser.close();
