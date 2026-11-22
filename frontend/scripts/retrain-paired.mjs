// Pair and trigger a classifier retrain via the in-page API.
import { chromium } from "@playwright/test";
const link = process.argv[2];
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
await p.goto(link.replace("https://example.com", "http://localhost:8443"), { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(2000);
await p.getByRole("button", { name: /Connect|Pair/ }).last().click();
await p.waitForURL(/\/app/, { timeout: 20000 });
console.log("paired");
const res = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return await m.request("POST", "/ml/retrain");
});
console.log("retrain result:", JSON.stringify(res, null, 1));
const metrics = await p.evaluate(async () => {
  const m = await import("/src/lib/api/index.ts");
  return await m.request("GET", "/ml/metrics");
});
console.log("after: trained =", metrics.trained, "| kind =", metrics.modelKind, "| source =", metrics.trainingSource);
console.log("accuracy =", metrics.accuracy, "f1 =", metrics.f1, "baseline =", metrics.baseline);
console.log("labels =", JSON.stringify(metrics.labels), "matrix =", JSON.stringify(metrics.matrix));
await b.close();
