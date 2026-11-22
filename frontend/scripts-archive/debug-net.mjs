import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const reqs = [];
p.on("request", r => { if (r.url().includes("7443") || r.url().includes("api")) reqs.push(`${r.method()} ${r.url()}`); });
p.on("response", r => { if (r.url().includes("7443")) reqs.push(`  -> ${r.status()} ${r.url()}`); });
p.on("websocket", ws => reqs.push(`WS open: ${ws.url()}`));
await p.goto("http://localhost:8443/app", { waitUntil: "networkidle", timeout: 20000 });
await p.waitForTimeout(6000);
console.log(reqs.join("\n"));
const state = await p.evaluate(() => {
  const out = { ws: [], snap: null };
  return out;
});
await b.close();
