// Verify that each page actually renders live agent data (not placeholders).
import { chromium } from "@playwright/test";

const BASE = "http://localhost:8443";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

async function textOf(route, waitMs = 3000) {
  await page.goto(`${BASE}${route}`, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForTimeout(waitMs);
  return page.evaluate(() => document.body.innerText);
}

const checks = [
  {
    route: "/app",
    name: "Home",
    expect: ["This host", "Mbps"],
  },
  { route: "/app/devices", name: "Devices", expect: ["This host"] },
  { route: "/app/ml", name: "ML", expect: ["accuracy", "RandomForest"] },
  { route: "/app/metrics", name: "Metrics", expect: ["Round-trip latency", "Live measurements"] },
  { route: "/app/decisions", name: "Decisions", expect: ["policy"] },
  { route: "/app/health", name: "Health", expect: ["capacity"] },
  { route: "/app/demand", name: "Demand", expect: ["EWMA"] },
  { route: "/app/agent", name: "Agent", expect: ["aditya"] },
  { route: "/app/settings", name: "Settings", expect: ["Mbps"] },
];

for (const c of checks) {
  const text = await textOf(c.route);
  const missing = c.expect.filter((e) => !text.toLowerCase().includes(e.toLowerCase()));
  console.log(
    `${missing.length ? "MISS " + c.route : "OK   " + c.route}` +
      (missing.length ? ` missing: ${missing.join(", ")}` : "")
  );
  if (missing.length) {
    console.log("  ---- page text sample ----");
    console.log(text.slice(0, 1500).replace(/\n{2,}/g, "\n"));
  }
}
await browser.close();
