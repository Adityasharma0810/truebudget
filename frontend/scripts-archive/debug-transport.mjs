import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:8443/app/devices", { waitUntil: "networkidle", timeout: 20000 });
await p.waitForTimeout(3000);
const st = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  const t = m.getTransport();
  return { mode: t.mode, status: t.status, hs: t.handshakeStatus, agentId: t.agentId, relayUrl: t.relayUrl, httpUrl: t.agentHttpUrl, lastError: t.lastError };
});
console.log("TRANSPORT:", JSON.stringify(st));
const snap = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  try {
    const r = await Promise.race([
      m.request("GET", "/state/snapshot"),
      new Promise((_, rej) => setTimeout(() => rej(new Error("12s timeout")), 12000)),
    ]);
    return { ok: true, devices: r.devices?.length, capacity: r.link?.capacityMbps };
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 200) };
  }
});
console.log("SNAPSHOT:", JSON.stringify(snap));
await b.close();
