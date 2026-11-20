import { chromium } from "@playwright/test";
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
await ctx.addInitScript(() => {
  window.__log = [];
  const NativeWS = window.WebSocket;
  function WrappedWS(...args) {
    const ws = new NativeWS(...args);
    const url = String(args[0]);
    window.__log.push({ e: "ws-open", url });
    ws.addEventListener("message", (ev) => window.__log.push({ e: "ws-msg", url: url.slice(-20), d: String(ev.data).slice(0, 60) }));
    ws.addEventListener("close", (ev) => window.__log.push({ e: "ws-close", url: url.slice(-20), c: ev.code }));
    ws.addEventListener("error", () => window.__log.push({ e: "ws-err", url: url.slice(-20) }));
    return ws;
  }
  WrappedWS.prototype = NativeWS.prototype;
  WrappedWS.CONNECTING = 0; WrappedWS.OPEN = 1; WrappedWS.CLOSING = 2; WrappedWS.CLOSED = 3;
  window.WebSocket = WrappedWS;
  const of = window.fetch;
  window.fetch = async (...args) => {
    const res = await of(...args);
    window.__log.push({ e: "fetch", u: String(args[0]).slice(-45), s: res.status });
    return res;
  };
});
const p = await ctx.newPage();
for (const route of ["/app", "/app/devices"]) {
  console.log(`=== goto ${route} ===`);
  await p.goto(`http://localhost:8443${route}`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForTimeout(9000);
  const log = await p.evaluate(() => window.__log.splice(0));
  console.log(log.map(l => JSON.stringify(l)).join("\n"));
  console.log("TEXTLEN", await p.evaluate(() => document.body.innerText.length));
}
await b.close();
