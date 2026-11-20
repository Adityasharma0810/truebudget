import { chromium } from "@playwright/test";
const link = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto(link, { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(1200);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 15000 });
await p.goto("http://localhost:8443/app/intent", { waitUntil: "domcontentloaded" });
await p.waitForFunction(() => document.body.innerText.includes("priority"), null, { timeout: 20000 });
// submit the boost form
const before = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return (await m.request("GET", "/intents")).length;
});
await p.getByRole("button", { name: /Boost|Save|Create|Apply/ }).first().click();
await p.waitForTimeout(3000);
const after = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  const list = await m.request("GET", "/intents");
  return { count: list.length, latest: list[list.length - 1] };
});
console.log("intents before:", before, "after:", after.count);
console.log("latest intent:", JSON.stringify(after.latest?.status ?? after.latest), after.count > before ? "INTENT CREATED" : "NO NEW INTENT");
// cancel it
if (after.count > before) {
  await p.getByRole("button", { name: "Cancel boost" }).first().click();
  await p.waitForTimeout(2500);
  const post = await p.evaluate(async () => {
    const m = await import("/src/lib/api/index.ts");
    const list = await m.request("GET", "/intents");
    return list.map(i => i.status);
  });
  console.log("statuses after cancel:", JSON.stringify(post));
}
await b.close();
