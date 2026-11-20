import { chromium } from "@playwright/test";

const routes = [
  "/app",
  "/app/tree",
  "/app/metrics",
  "/app/devices",
  "/app/intent",
  "/app/decisions",
  "/app/conflicts",
  "/app/health",
  "/app/lab",
  "/app/experiments",
  "/app/ml",
  "/app/demand",
  "/app/links",
  "/app/settings",
  "/app/agent",
  "/app/about",
  "/connect",
  "/install",
  "/welcome",
  "/about",
];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const pageErrors = [];
const consoleErrors = [];
const failedRequests = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(msg.text());
});
page.on("response", (res) => {
  const url = res.url();
  if (url.includes("/api/") && res.status() >= 400) {
    failedRequests.push(`${res.status()} ${res.request().method()} ${url}`);
  }
});
page.on("requestfailed", (req) => {
  if (req.url().includes("/api/") || req.url().includes("/stream")) {
    failedRequests.push(`FAILED ${req.method()} ${req.url()} ${req.failure()?.errorText}`);
  }
});

const results = [];
for (const route of routes) {
  pageErrors.length = 0;
  consoleErrors.length = 0;
  failedRequests.length = 0;
  let status = "OK";
  let body = "";
  try {
    await page.goto(`http://localhost:8443${route}`, {
      waitUntil: "networkidle",
      timeout: 30000,
    });
    await page.waitForTimeout(2500);
    body = await page.locator("body").innerText();
    if (!body.trim()) status = "EMPTY BODY";
    if (pageErrors.length) status = "PAGE ERROR";
    if (consoleErrors.length) status = "CONSOLE ERROR";
    if (failedRequests.length) status = "API ERROR";
  } catch (error) {
    status = `NAV ERROR: ${String(error).slice(0, 200)}`;
  }
  results.push({
    route,
    status,
    bodyHead: body.slice(0, 220).replace(/\n+/g, " | "),
    pageErrors: [...pageErrors],
    consoleErrors: [...new Set(consoleErrors)].slice(0, 8),
    failedRequests: [...new Set(failedRequests)].slice(0, 8),
  });
  await page.screenshot({
    path: `/tmp/opencode/ui-${route.replace(/[^a-z0-9]+/gi, "_") || "root"}.png`,
    fullPage: true,
  });
}

console.log(JSON.stringify(results, null, 2));
await browser.close();
