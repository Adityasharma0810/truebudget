import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
for (const route of ["/app", "/app/devices"]) {
  console.log(`=== goto ${route} ===`);
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  // Instrument all WebSockets created by the page from now on
  await p.evaluate(() => {
    const NativeWS = window.WebSocket;
    window.__wslog = [];
    window.WebSocket = function (...args) {
      const ws = new NativeWS(...args);
      window.__wslog.push({ url: String(args[0]), t: Date.now() % 100000 });
      const origSend = ws.send.bind(ws);
      ws.send = (d) => { window.__wslog.push({ sent: String(d).slice(0, 80), t: Date.now() % 100000 }); return origSend(d); };
      ws.addEventListener("message", (ev) => window.__wslog.push({ recv: String(ev.data).slice(0, 80), t: Date.now() % 100000 }));
      ws.addEventListener("close", (ev) => window.__wslog.push({ closed: ev.code, t: Date.now() % 100000 }));
      ws.addEventListener("error", () => window.__wslog.push({ error: true, t: Date.now() % 100000 }));
      return ws;
    };
    window.WebSocket.prototype = NativeWS.prototype;
    Object.assign(window.WebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
  });
  await p.waitForTimeout(9000);
  const log = await p.evaluate(() => window.__wslog);
  console.log(log.map(l => JSON.stringify(l)).join("\n"));
  const len = await p.evaluate(() => document.body.innerText.length);
  console.log("TEXTLEN", len);
}
await b.close();
