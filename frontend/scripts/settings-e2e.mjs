import { chromium } from "@playwright/test";
const link = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
// pair first
await p.goto(link, { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(1200);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 15000 });
// settings slider + save
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded" });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const slider = p.getByLabel("Maximum rate change");
const before = await slider.inputValue();
await slider.focus();
for (let i = 0; i < 5; i++) await slider.press("ArrowRight");
await p.getByRole("button", { name: "Save settings" }).click();
await p.waitForTimeout(2500);
const after = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log(`slider ${before} -> save -> server ${after}`, Number(after) === Number(before) + 5 ? "UI SAVE OK" : "MISMATCH");
// revert
for (let i = 0; i < 5; i++) await slider.press("ArrowLeft");
await p.getByRole("button", { name: "Save settings" }).click();
await p.waitForTimeout(2000);
const reverted = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("reverted:", reverted, Number(reverted) === Number(before) ? "REVERT OK" : "REVERT MISMATCH");
await b.close();
