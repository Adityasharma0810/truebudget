import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const seq = ["/app/metrics", "/app/decisions", "/app/health"];
for (const route of seq) {
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForTimeout(20000);
  const t = await p.evaluate(() => document.body.innerText);
  console.log(route, "TEXTLEN", t.length, /capacity/i.test(t) ? "HAS-capacity" : "NO-capacity");
}
await b.close();
