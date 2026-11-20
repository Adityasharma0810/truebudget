import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
// mimic the verify sequence up to health
for (const route of ["/app", "/app/devices", "/app/ml", "/app/metrics", "/app/decisions", "/app/health"]) {
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "networkidle", timeout: 20000 });
  await p.waitForTimeout(8000);
  const t = await p.evaluate(() => document.body.innerText);
  console.log(route, t.length, /capacity/i.test(t) ? "HAS" : "NO", /did not answer|failed|error/i.test(t) ? "ERRTEXT" : "");
  if (route === "/app/health") console.log(t.slice(t.indexOf("A healthier"), t.indexOf("A healthier") + 900));
}
await b.close();
