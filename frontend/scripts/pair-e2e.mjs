import { chromium } from "@playwright/test";
const link = process.argv[2];
if (!link) { console.error("usage: node scripts/pair-e2e.mjs '<pairing link>'"); process.exit(1); }
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on("pageerror", e => console.log("PAGEERROR:", e.message.slice(0, 200)));
await p.goto(link, { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(1500);
console.log("URL now:", p.url());
const t = await p.evaluate(() => document.body.innerText);
console.log("page has key prefilled:", /EB-/.test(t));
// click the connect button
const btn = p.getByRole("button", { name: /Connect|Pair/ }).last();
await btn.click();
await p.waitForTimeout(4000);
const t2 = await p.evaluate(() => document.body.innerText);
console.log("after connect URL:", p.url());
console.log("error shown:", /missing|invalid|expired|failed/i.test(t2) ? t2.split("\n").filter(l => /missing|invalid|expired|failed/i.test(l))[0] : "none");
// now try a mutation
const mut = await p.evaluate(async () => {
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
console.log("mutation:", JSON.stringify(mut));
await b.close();
