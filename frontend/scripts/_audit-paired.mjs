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
];

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  storageState: "/tmp/opencode/paired-state.json",
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
let currentProblems = [];
page.on("pageerror", (e) => currentProblems.push(`PAGEERROR ${e.message.slice(0, 400)}`));
page.on("console", (m) => {
  if (m.type() === "error") currentProblems.push(`CONSOLE ${m.text().slice(0, 400)}`);
});
page.on("requestfailed", (r) =>
  currentProblems.push(`FAILED ${r.method()} ${r.url()} :: ${r.failure()?.errorText}`),
);
page.on("response", async (r) => {
  if ((r.url().includes(":7443") || r.url().includes(":7444")) && r.status() >= 400) {
    let body = "";
    try {
      body = (await r.text()).slice(0, 240);
    } catch {}
    currentProblems.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()} :: ${body}`);
  }
});

for (const route of routes) {
  currentProblems = [];
  const responses = [];
  const resHandler = (r) => {
    if (r.url().includes(":7443") && r.request().method() === "GET") {
      responses.push(`${r.status()} ${new URL(r.url()).pathname}${new URL(r.url()).search}`);
    }
  };
  page.on("response", resHandler);
  await page.goto(`http://localhost:8443${route}`, {
    waitUntil: "domcontentloaded",
    timeout: 30000,
  });
  await page.waitForTimeout(4500);
  const text = await page.locator("body").innerText();
  const heading = await page.locator("h1").first().innerText().catch(() => "");
  const errors = await page.locator(".error-state, [role=alert]").allInnerTexts().catch(() => []);
  console.log(`\n=== ${route} :: text=${text.length} heading="${heading.replace(/\n/g, " ")}"`);
  console.log(`   GETs: ${[...new Set(responses)].join(" | ")}`);
  if (errors.length) console.log(`   INLINE: ${errors.join(" | ").slice(0, 400)}`);
  if (currentProblems.length) console.log(`   PROBLEMS:\n     ${currentProblems.join("\n     ")}`);
  page.off("response", resHandler);
}
await browser.close();
