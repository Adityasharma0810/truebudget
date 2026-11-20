import { chromium } from "@playwright/test";
const b = await chromium.launch();
for (let i = 0; i < 5; i++) {
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const t0 = Date.now();
  await p.goto("http://localhost:8443/app/devices", { waitUntil: "domcontentloaded", timeout: 20000 });
  let ready = -1;
  for (let s = 0; s < 300; s++) {
    const t = await p.evaluate(() => document.body.innerText);
    if (t.includes("This host")) { ready = Date.now() - t0; break; }
    await p.waitForTimeout(100);
  }
  console.log(`load ${i + 1}: data at ${ready}ms`);
  await p.close();
}
await b.close();
