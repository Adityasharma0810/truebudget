// Label real flows via a paired browser session (mutations require pairing).
// Usage: node scripts/label-flows-e2e.mjs <pairing-link> <candidates.json>
// candidates.json: [{"flowId": "...", "cls": "bulk"}, ...]
import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";

const link = process.argv[2];
const candidatesFile = process.argv[3];
if (!link || !candidatesFile) {
  console.error("usage: node scripts/label-flows-e2e.mjs <pairing-link> <candidates.json>");
  process.exit(2);
}
const candidates = JSON.parse(readFileSync(candidatesFile, "utf-8"));
const base = "http://localhost:8443";

const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto(link.replace("https://example.com", base), { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(1500);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 20000 });
console.log("paired");

for (const c of candidates) {
  const res = await p.evaluate(async ({ flowId, cls }) => {
    const m = await import("/src/lib/api/index.ts");
    const fid = encodeURIComponent(flowId);
    const r1 = await m.request("POST", `/flows/${fid}/correct`, {
      confirmed_class: cls,
      suggested_class: "unknown",
    });
    const r2 = await m.request("POST", "/ml/corrections", {
      id: `corr-${flowId}`,
      status: "approved",
    });
    return { r1, r2 };
  }, { flowId: c.flowId, cls: c.cls });
  console.log(`labeled ${c.flowId.slice(0, 48)} -> ${c.cls}`, JSON.stringify(res.r1), JSON.stringify(res.r2));
}
await b.close();
console.log("done");
