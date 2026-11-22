import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const slider = p.getByLabel("Maximum rate change");
console.log("disabled:", await slider.isDisabled(), "value:", await slider.inputValue());
await slider.focus();
for (let i = 0; i < 5; i++) await slider.press("ArrowRight");
await p.waitForTimeout(300);
console.log("after arrows value:", await slider.inputValue());
const shown = await p.evaluate(() => {
  const el = [...document.querySelectorAll(".setting-slider")].find(d => d.textContent.includes("Maximum rate change"));
  return el?.querySelector(".slider-value")?.textContent?.trim();
});
console.log("displayed:", shown);
await p.getByRole("button", { name: "Save settings" }).click();
await p.waitForTimeout(2500);
const after = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("server after:", after);
// revert
for (let i = 0; i < 5; i++) await slider.press("ArrowLeft");
await p.getByRole("button", { name: "Save settings" }).click();
await p.waitForTimeout(2000);
const reverted = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/settings")).rateChangePct;
});
console.log("reverted:", reverted);
await b.close();
