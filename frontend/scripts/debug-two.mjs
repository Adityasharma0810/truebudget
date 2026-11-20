import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on("websocket", ws => {
  ws.on("close", () => console.log(`WS CLOSED: ${ws.url()}`));
  console.log(`WS OPEN: ${ws.url()}`);
});
p.on("response", r => { if (r.url().includes("7443")) console.log(`HTTP ${r.status()} ${r.url().slice(0, 80)}`); });
p.on("pageerror", e => console.log(`PAGEERROR ${e.message.slice(0, 200)}`));
for (const route of ["/app", "/app/devices"]) {
  console.log(`--- goto ${route} ---`);
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "networkidle", timeout: 20000 });
  await p.waitForTimeout(8000);
  const t = await p.evaluate(() => document.body.innerText.length);
  console.log(`TEXTLEN ${t}`);
}
await b.close();
