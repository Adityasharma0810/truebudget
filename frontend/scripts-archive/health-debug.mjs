import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
for (const route of ["/app", "/app/devices", "/app/ml", "/app/metrics", "/app/decisions", "/app/health"]) {
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "networkidle", timeout: 20000 });
  await p.waitForTimeout(8000);
  const text = await p.evaluate(() => document.body.innerText);
  const has = text.toLowerCase().includes("capacity");
  console.log(route, text.length, has ? "HAS" : "MISS");
  if (!has) {
    // dump the section around "hypotheses"
    const i = text.indexOf("hypotheses");
    console.log("  section:", text.slice(Math.max(0, i - 200), i + 400).replace(/\n+/g, " | "));
  }
}
await b.close();
