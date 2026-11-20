import { chromium } from "@playwright/test";
const link = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto(link, { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(1200);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 15000 });
await p.goto("http://localhost:8443/app/ml", { waitUntil: "domcontentloaded" });
await p.waitForFunction(() => document.body.innerText.includes("RandomForest") || document.body.innerText.includes("rule baseline"), null, { timeout: 20000 });
const before = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return await m.request("GET", "/ml/metrics");
});
console.log("before: trained =", before.trained, "kind =", before.modelKind, "nTrain =", before.nTrain);
await p.getByRole("button", { name: "Retrain locally" }).click();
await p.waitForTimeout(15000);
const after = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return await m.request("GET", "/ml/metrics");
});
console.log("after: trained =", after.trained, "kind =", after.modelKind, "accuracy =", after.accuracy, "nTrain =", after.nTrain, "trainedAt =", after.trainedAt);
console.log(after.trained && (after.nTrain !== before.nTrain || after.trainedAt !== before.trainedAt) ? "RETRAIN OK" : "RETRAIN CHECK");
await b.close();
