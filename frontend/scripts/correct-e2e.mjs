import { chromium } from "@playwright/test";
const link = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto(link, { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(1200);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 15000 });
await p.goto("http://localhost:8443/app/tree", { waitUntil: "domcontentloaded" });
await p.waitForFunction(() => document.body.innerText.includes("This host"), null, { timeout: 20000 });
// open the device detail and find a flow row to click
await p.getByRole("button", { name: "Table" }).first().click();
await p.waitForTimeout(1200);
await p.getByRole("button", { name: "Inspect" }).first().click();
await p.waitForTimeout(1200);
// pick a flow in the detail view
const flowRow = p.locator("button, tr").filter({ hasText: /call|stream|bulk|game|other/ }).last();
const clicked = await flowRow.click({ timeout: 8000 }).then(() => true).catch(() => false);
console.log("opened a flow detail:", clicked);
if (clicked) {
  await p.waitForTimeout(1000);
  const sel = p.getByLabel("Correct traffic class");
  const has = await sel.count();
  if (has) {
    await sel.selectOption("call");
    await p.getByRole("button", { name: "Correct label" }).click();
    await p.waitForTimeout(2500);
    const corrections = await p.evaluate(async () => {
      const m = await import("/src/lib/api/index.ts");
      const ml = await m.request("GET", "/ml/metrics");
      return ml.corrections?.slice(0, 2);
    });
    console.log("latest corrections:", JSON.stringify(corrections));
  } else {
    console.log("no correction select visible (flow may be idle)");
  }
}
await b.close();
