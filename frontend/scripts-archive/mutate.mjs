import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const before = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("before:", before);
// Move the "Maximum rate change" slider via keyboard (accessible range input)
const slider = p.getByLabel("Maximum rate change");
await slider.focus();
const target = Number(before) >= 50 ? Number(before) - 5 : Number(before) + 5;
// set value directly through the input's native setter, then fire React's onChange
for (let i = 0; i < Math.abs(target - before); i++) await slider.press(typeof target === "number" && target > before ? "ArrowRight" : "ArrowLeft");
await p.waitForTimeout(500);
await p.getByRole("button", { name: /Save|Apply/ }).first().click();
await p.waitForTimeout(2500);
const after = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("after:", after, "target:", target, after === target ? "ROUND-TRIP OK" : "MISMATCH");
// revert
for (let i = 0; i < Math.abs(target - before); i++) await slider.press(target > before ? "ArrowLeft" : "ArrowRight");
await p.getByRole("button", { name: /Save|Apply/ }).first().click();
await p.waitForTimeout(2000);
const reverted = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("reverted:", reverted, reverted === before ? "REVERT OK" : "REVERT MISMATCH");
await b.close();
