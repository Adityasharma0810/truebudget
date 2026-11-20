import { chromium } from "@playwright/test";
const BASE = "http://localhost:8443";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const checks = [
  ["/app", ["This host", "Mbps"]],
  ["/app/devices", ["This host"]],
  ["/app/ml", ["accuracy", "RandomForest"]],
  ["/app/metrics", ["Round-trip latency"]],
  ["/app/decisions", ["policy"]],
  ["/app/health", ["capacity"]],
  ["/app/demand", ["EWMA"]],
  ["/app/agent", ["aditya"]],
  ["/app/settings", ["Mbps"]],
];
for (const [route, expect] of checks) {
  const t0 = Date.now();
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  let when = -1, text = "";
  for (let s = 0; s < 300; s++) {
    text = await page.evaluate(() => document.body.innerText);
    if (expect.every(e => text.toLowerCase().includes(e.toLowerCase()))) { when = Date.now() - t0; break; }
    await page.waitForTimeout(100);
  }
  const cap = /v0\.1\.0/.test(text) ? "cap-ok" : "NO-CAP";
  const err = /did not answer|Something went wrong|failed/i.test(text) ? "ERR" : "";
  console.log(`${when < 0 ? "FAIL" : String(when).padStart(5) + "ms"} ${route} ${cap} ${err}`);
  if (when < 0) console.log("  sample:", text.slice(0, 400).replace(/\n+/g, " | "));
}
await browser.close();
