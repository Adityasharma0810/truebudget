// E2E: correct a flow's traffic class from the Tree page and verify it lands
// in the ML review queue (POST /flows/{id}/correct -> /ml/metrics corrections).
// Usage: node scripts/correct-e2e.mjs <pairing-link>
import { chromium } from "@playwright/test";

const link = process.argv[2];
if (!link) {
  console.error("usage: node scripts/correct-e2e.mjs <pairing-link>");
  process.exit(2);
}
const base = "http://localhost:8443";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
p.on("pageerror", (e) => errors.push(String(e)));
p.on("console", (m) => m.type() === "error" && errors.push(m.text()));

// pair first: mutations require a paired browser session
await p.goto(link.replace("https://example.com", base), {
  waitUntil: "domcontentloaded",
  timeout: 20000,
});
await p.waitForTimeout(1500);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 20000 });
console.log("paired, on /app");

await p.goto(`${base}/app/tree`, { waitUntil: "domcontentloaded", timeout: 20000 });
// wait until at least one device row renders (table view later needs devices)
await p.waitForFunction(
  () => document.body.innerText.match(/active flows|devices ·/),
  null,
  { timeout: 30000 },
);
await p.waitForTimeout(2000);

// switch to Table view and open the first device drawer
await p.getByRole("button", { name: "Table" }).first().click();
await p.waitForTimeout(800);
await p.getByRole("button", { name: "Inspect" }).first().click();
await p.waitForTimeout(1200);

// find a flow list item (class label may be "unknown")
const flowItem = p.locator(".flow-list-item").first();
const hasFlow = (await flowItem.count()) > 0;
console.log("flow list item found:", hasFlow);
if (!hasFlow) {
  console.log("RESULT: FAIL (no flows on device)");
  await b.close();
  process.exit(1);
}
await flowItem.click();
await p.waitForTimeout(800);

// flow detail should show the correction select
const sel = p.getByLabel("Correct traffic class");
if (!(await sel.count())) {
  console.log("RESULT: FAIL (no correction select in flow detail)");
  await b.close();
  process.exit(1);
}
const before = await p.locator(".flow-list-item.flow-selected strong").first().innerText();
console.log("flow class before correction:", before);
await sel.selectOption("call");
await p.getByRole("button", { name: "Correct label" }).click();
await p.waitForTimeout(2500);

// verify via the API that a pending correction now exists
const corrections = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  const ml = await m.request("GET", "/ml/metrics");
  return ml.corrections?.slice(0, 3);
});
console.log("latest corrections:", JSON.stringify(corrections));
const pending = (corrections ?? []).some(
  (c) => c.status === "pending" && c.to === "call",
);
console.log("RESULT:", pending ? "PASS" : "FAIL");
if (errors.length) console.log("console errors:", errors.slice(0, 5));
await p.screenshot({ path: "shots/correct-e2e.png", fullPage: false });
await b.close();
process.exit(pending ? 0 : 1);
