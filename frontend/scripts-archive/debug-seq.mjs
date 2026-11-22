import { chromium } from "@playwright/test";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => {
  window.__log = [];
  const NativeWS = window.WebSocket;
  function WrappedWS(...args) {
    const ws = new NativeWS(...args);
    const url = String(args[0]);
    const tag = url.includes("7444") ? "relay" : url.includes("7443") ? "direct" : "vite";
    const origSend = ws.send.bind(ws);
    ws.send = (d) => { window.__log.push({ e: tag + ">send", d: String(d).slice(0, 70) }); return origSend(d); };
    ws.addEventListener("message", (ev) => window.__log.push({ e: tag + ">recv", d: String(ev.data).slice(0, 70) }));
    ws.addEventListener("close", (ev) => window.__log.push({ e: tag + ">close", c: ev.code }));
    return ws;
  }
  WrappedWS.prototype = NativeWS.prototype;
  WrappedWS.CONNECTING = 0; WrappedWS.OPEN = 1; WrappedWS.CLOSING = 2; WrappedWS.CLOSED = 3;
  window.WebSocket = WrappedWS;
  const of = window.fetch;
  window.fetch = async (...args) => {
    const res = await of(...args);
    const u = String(args[0]);
    if (u.includes("7443")) window.__log.push({ e: "fetch", u: u.slice(-40), s: res.status });
    return res;
  };
});
const p = await ctx.newPage();
for (const route of ["/app/ml", "/app/metrics"]) {
  console.log(`=== ${route} ===`);
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForTimeout(12000);
  const log = await p.evaluate(() => window.__log.splice(0));
  console.log(log.map(l => JSON.stringify(l)).join("\n"));
  console.log("TEXTLEN", await p.evaluate(() => document.body.innerText.length));
}
await b.close();
