import { chromium } from "@playwright/test";
const key = process.argv[2];
if (!key) { console.error("usage: node scripts/pair-browser.mjs EB-XXXX-XXXX-XXXX"); process.exit(1); }
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
// Persist storage so the pairing survives in this context only (demo).
const p = await ctx.newPage();
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const out = await p.evaluate(async (key) => {
  const m = await import("/src/lib/api/index.ts");
  const t = m.getTransport();
  // The real pairing path on the transport:
  try {
    await t.connect(key);
    return { ok: true, role: t.role, agentId: t.agentId };
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 300) };
  }
}, key);
console.log("pair:", JSON.stringify(out));
const mutation = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  const cur = await m.request("GET", "/settings");
  const old = cur.rateChangePct;
  const target = old >= 50 ? old - 5 : old + 5;
  await m.request("PUT", "/settings", { ...cur, rateChangePct: target });
  const after = (await m.request("GET", "/settings")).rateChangePct;
  await m.request("PUT", "/settings", { ...cur, rateChangePct: old });
  const reverted = (await m.request("GET", "/settings")).rateChangePct;
  return { old, target, after, reverted };
});
console.log("mutation round-trip:", JSON.stringify(mutation));
await b.close();
