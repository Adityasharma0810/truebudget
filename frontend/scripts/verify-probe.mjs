import { chromium } from "@playwright/test";
const BASE = "http://localhost:8443";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", e => errors.push(`PAGEERROR ${route}: ${e.message.slice(0, 200)}`));
page.on("console", m => { if (m.type() === "error") errors.push(`CONSOLE ${m.text().slice(0, 200)}`); });
let route = "";
const checks = [
  ["/app", ["This host", "Mbps"]],
  ["/app/devices", ["This host"]],
  ["/app/ml", ["accuracy", "RandomForest"]],
  ["/app/metrics", ["Round-trip latency", "Live measurements"]],
  ["/app/decisions", ["policy"]],
  ["/app/health", ["capacity"]],
  ["/app/demand", ["EWMA"]],
  ["/app/agent", ["aditya"]],
  ["/app/settings", ["Mbps"]],
];
for (const [r, expect] of checks) {
  route = r;
  await page.goto(`${BASE}${r}`, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForTimeout(20000);
  const text = await page.evaluate(() => document.body.innerText);
  const missing = expect.filter(e => !text.toLowerCase().includes(e.toLowerCase()));
  if (missing.length) {
    console.log(`MISS ${r} missing: ${missing.join(",")}`);
    await page.screenshot({ path: `tests/screenshots/miss-${r.replaceAll("/", "_")}.png` });
    console.log("  text sample:", text.slice(0, 300).replace(/\n+/g, " | "));
  } else console.log(`OK   ${r}`);
}
console.log(errors.slice(0, 10).join("\n") || "no console errors");
await browser.close();
