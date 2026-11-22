import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
for (const route of ["/app", "/app/devices"]) {
  console.log(`=== goto ${route} ===`);
  const lines = [];
  p.on("response", async (r) => {
    if (r.url().includes("127.0.0.1:7443")) {
      let body = "";
      try { body = (await r.text()).slice(0, 120); } catch {}
      lines.push(`HTTP ${r.status()} ${r.url().slice(-40)} :: ${body}`);
    }
  });
  p.on("requestfailed", r => lines.push(`FAILED ${r.url().slice(-40)} :: ${r.failure()?.errorText}`));
  p.on("pageerror", e => lines.push(`PAGEERROR ${e.message.slice(0, 300)}`));
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForTimeout(9000);
  console.log(lines.slice(0, 15).join("\n"));
  const len = await p.evaluate(() => document.body.innerText.length);
  console.log("TEXTLEN", len);
}
await b.close();
