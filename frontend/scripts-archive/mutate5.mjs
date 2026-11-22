import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const out = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  const cur = await m.request("GET", "/settings");
  const target = { ...cur, rateChangePct: 25 };
  try {
    const r = await m.request("PUT", "/settings", target);
    return { ok: true, r };
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 200) };
  }
});
console.log("PUT result:", JSON.stringify(out).slice(0, 300));
const after = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("server rateChangePct now:", after);
await b.close();
