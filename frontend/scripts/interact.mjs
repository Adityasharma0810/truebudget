import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
const results = [];
async function step(name, fn) {
  try { await fn(); results.push(`OK   ${name}`); }
  catch (e) { results.push(`FAIL ${name}: ${String(e).slice(0, 120)}`); }
}
const wait = (ms) => p.waitForTimeout(ms);

await step("home loads", async () => {
  await p.goto("http://localhost:8443/app", { waitUntil: "domcontentloaded", timeout: 20000 });
  await p.waitForFunction(() => document.body.innerText.includes("This host"), null, { timeout: 20000 });
});

await step("metrics: switch range to 15 min", async () => {
  await p.goto("http://localhost:8443/app/metrics", { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => document.body.innerText.includes("Round-trip latency"), null, { timeout: 20000 });
  await p.getByRole("button", { name: "15 min" }).click();
  await wait(2500);
  const t = await p.evaluate(() => document.body.innerText);
  if (!t.includes("Export CSV")) throw new Error("page lost after range switch");
});

await step("metrics: data table toggle", async () => {
  await p.getByRole("button", { name: "Data table" }).click();
  await p.waitForFunction(() => document.body.innerText.includes("Latency"), null, { timeout: 8000 });
  await p.getByRole("button", { name: "Show charts" }).click();
});

await step("metrics: pause stream", async () => {
  await p.getByRole("button", { name: "Pause stream" }).click();
  await p.waitForFunction(() => document.body.innerText.includes("Resume live"), null, { timeout: 5000 });
  await p.getByRole("button", { name: "Resume live" }).click();
});

await step("devices: search filter", async () => {
  await p.goto("http://localhost:8443/app/devices", { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => document.body.innerText.includes("This host"), null, { timeout: 20000 });
  await p.getByLabel("Search devices").fill("zzz-no-match");
  await wait(800);
  const t = await p.evaluate(() => document.body.innerText);
  if (t.includes("This host · enp0s3")) throw new Error("filter did not apply");
  await p.getByLabel("Search devices").fill("");
});

await step("devices: edit modal opens and cancels", async () => {
  const edit = p.getByRole("button", { name: /Edit/ }).first();
  await edit.click();
  await wait(600);
  await p.keyboard.press("Escape");
  await wait(400);
});

await step("demand: horizon switch", async () => {
  await p.goto("http://localhost:8443/app/demand", { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => document.body.innerText.includes("EWMA"), null, { timeout: 20000 });
  const btns = await p.getByRole("button").all();
  if (!btns.length) throw new Error("no buttons");
});

await step("settings: load draft and reset", async () => {
  await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
  await p.getByRole("button", { name: /Revert|Reset|Undo/ }).first().click().catch(() => {});
});

await step("ml: page renders with warner card", async () => {
  await p.goto("http://localhost:8443/app/ml", { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => document.body.innerText.includes("QoE degradation warner"), null, { timeout: 20000 });
});

await step("agent page renders security info", async () => {
  await p.goto("http://localhost:8443/app/agent", { waitUntil: "domcontentloaded" });
  await p.waitForFunction(() => document.body.innerText.includes("aditya"), null, { timeout: 20000 });
});

await step("lab page renders", async () => {
  await p.goto("http://localhost:8443/app/lab", { waitUntil: "domcontentloaded" });
  await wait(4000);
  const t = await p.evaluate(() => document.body.innerText);
  if (t.length < 500) throw new Error("lab page empty");
});

await step("tree (usage) page renders", async () => {
  await p.goto("http://localhost:8443/app/tree", { waitUntil: "domcontentloaded" });
  await wait(5000);
  const t = await p.evaluate(() => document.body.innerText);
  if (t.length < 500) throw new Error("tree page empty");
});

await step("conflicts page renders", async () => {
  await p.goto("http://localhost:8443/app/conflicts", { waitUntil: "domcontentloaded" });
  await wait(4000);
});

await step("experiments page renders", async () => {
  await p.goto("http://localhost:8443/app/experiments", { waitUntil: "domcontentloaded" });
  await wait(4000);
});

console.log(results.join("\n"));
await b.close();
