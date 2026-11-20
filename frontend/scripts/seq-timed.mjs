import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const checks = [
  ["/app", "This host"],
  ["/app/devices", "This host"],
  ["/app/ml", "RandomForest"],
  ["/app/metrics", "Round-trip latency"],
  ["/app/decisions", "policy"],
  ["/app/health", "capacity"],
  ["/app/demand", "EWMA"],
  ["/app/agent", "aditya"],
  ["/app/settings", "Mbps"],
];
for (const [route, needle] of checks) {
  const t0 = Date.now();
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  let when = -1;
  for (let s = 0; s < 250; s++) {
    const t = await p.evaluate(() => document.body.innerText);
    if (t.toLowerCase().includes(needle.toLowerCase())) { when = Date.now() - t0; break; }
    await p.waitForTimeout(100);
  }
  console.log(`${when < 0 ? "FAIL>25s" : when + "ms"} ${route}`);
}
await b.close();
