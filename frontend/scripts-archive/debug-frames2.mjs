import { chromium } from "@playwright/test";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => {
  window.__log = [];
  const NativeWS = window.WebSocket;
  function WrappedWS(...args) {
    const ws = new NativeWS(...args);
    const url = String(args[0]);
    if (!url.includes("7444")) return ws;
    const origSend = ws.send.bind(ws);
    ws.send = (d) => { window.__log.push({ e: "send", t: Date.now() % 100000, d: String(d).slice(0, 260) }); return origSend(d); };
    ws.addEventListener("message", (ev) => window.__log.push({ e: "recv", t: Date.now() % 100000, d: String(ev.data).slice(0, 260) }));
    ws.addEventListener("close", (ev) => window.__log.push({ e: "close", t: Date.now() % 100000, c: ev.code }));
    return ws;
  }
  WrappedWS.prototype = NativeWS.prototype;
  WrappedWS.CONNECTING = 0; WrappedWS.OPEN = 1; WrappedWS.CLOSING = 2; WrappedWS.CLOSED = 3;
  window.WebSocket = WrappedWS;
});
const p = await ctx.newPage();
await p.goto("http://localhost:8443/app/devices", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(10000);
const log = await p.evaluate(() => window.__log.splice(0));
console.log(log.map(l => JSON.stringify(l)).join("\n"));
await b.close();
