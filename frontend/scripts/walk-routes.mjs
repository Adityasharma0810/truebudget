// Walk every frontend route, collect console/page errors and failed requests,
// and screenshot each page for review.
import { chromium } from "@playwright/test";

const BASE = "http://localhost:8443";
const routes = [
  "/app",
  "/app/tree",
  "/app/metrics",
  "/app/devices",
  "/app/intent",
  "/app/decisions",
  "/app/conflicts",
  "/app/health",
  "/app/demand",
  "/app/links",
  "/app/agent",
  "/app/lab",
  "/app/experiments",
  "/app/ml",
  "/app/settings",
  "/welcome",
  "/connect",
];

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

const problems = [];
page.on("console", (msg) => {
  if (msg.type() === "error" || msg.type() === "warning") {
    problems.push(`[console.${msg.type()}] ${msg.text().slice(0, 300)}`);
  }
});
page.on("pageerror", (err) => problems.push(`[pageerror] ${err.message.slice(0, 400)}`));
page.on("requestfailed", (req) =>
  problems.push(`[requestfailed] ${req.method()} ${req.url()} :: ${req.failure()?.errorText}`)
);
page.on("response", (res) => {
  if (res.status() >= 400) problems.push(`[http ${res.status()}] ${res.url()}`);
});

for (const route of routes) {
  try {
    await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 20000 });
    await page.waitForTimeout(2500);
    const slug = route.replace(/\//g, "_") || "root";
    await page.screenshot({ path: `tests/screenshots/walk${slug}.png`, fullPage: false });
    console.log(`OK ${route}`);
  } catch (err) {
    problems.push(`[goto ${route}] ${err.message.slice(0, 200)}`);
    console.log(`FAIL ${route}: ${err.message.slice(0, 120)}`);
  }
}

console.log("\n=== PROBLEMS ===");
for (const p of [...new Set(problems)]) console.log(p);
await browser.close();
