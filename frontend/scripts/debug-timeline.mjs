import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on("console", m => { if (m.type() === "error") console.log("CONSOLE-ERR:", m.text().slice(0, 200)); });
p.on("pageerror", e => console.log("PAGEERROR:", e.message.slice(0, 200)));
await p.goto("http://localhost:8443/app/devices", { waitUntil: "domcontentloaded", timeout: 20000 });
for (const t of [1000, 3000, 6000, 9000, 13000, 18000]) {
  await p.waitForTimeout(t === 1000 ? 1000 : t - (t === 3000 ? 1000 : t === 6000 ? 3000 : t === 9000 ? 6000 : t === 13000 ? 9000 : 13000));
  const st = await p.evaluate(async () => {
    const m = await import("/src/lib/api/index.ts");
    const tr = m.getTransport();
    return { mode: tr.mode, status: tr.status, hs: tr.handshakeStatus, ws: tr.ws?.readyState, len: document.body.innerText.length };
  });
  console.log(`t=${t}ms`, JSON.stringify(st));
}
await b.close();
