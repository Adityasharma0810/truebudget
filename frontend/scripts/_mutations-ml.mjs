import { chromium } from "@playwright/test";
import fs from "node:fs";

const BASE = "http://localhost:8443";
const AGENT = "http://localhost:7443/api/v1";
const results = [];
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  storageState: "/tmp/opencode/paired-state.json",
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
page.setDefaultTimeout(20000);
const problems = [];
page.on("pageerror", (e) => problems.push(`PAGEERROR ${e.message.slice(0, 300)}`));
page.on("console", (m) => {
  if (m.type() === "error") problems.push(`CONSOLE ${m.text().slice(0, 300)}`);
});
page.on("requestfailed", (r) =>
  problems.push(`FAILED ${r.method()} ${r.url()} :: ${r.failure()?.errorText}`),
);
page.on("response", async (r) => {
  if (r.url().includes(":7443") && r.status() >= 400) {
    let body = "";
    try {
      body = (await r.text()).slice(0, 240);
    } catch {}
    problems.push(`HTTP ${r.status()} ${r.request().method()} ${r.url()} :: ${body}`);
  }
});

async function token() {
  const raw = await page.evaluate(() => localStorage.getItem("eb-agents"));
  return JSON.parse(raw || "[]")[0]?.accessToken || "";
}
async function api(path, method = "GET", body) {
  const t = await token();
  return page.evaluate(
    async ({ path, method, body, t, AGENT }) => {
      const r = await fetch(AGENT + path, {
        method,
        headers: {
          Authorization: `Bearer ${t}`,
          ...(body ? { "Content-Type": "application/json" } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      let data = null;
      try {
        data = await r.json();
      } catch {}
      return { status: r.status, data };
    },
    { path, method, body, t, AGENT },
  );
}
async function goto(route, wait = 5000) {
  await page.goto(BASE + route, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.waitForTimeout(wait);
}
async function toasts() {
  return (await page.locator(".toast").allInnerTexts().catch(() => [])).join(" | ");
}
async function waitToast(re, timeout = 30000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    const t = await toasts();
    if (re.test(t)) return t;
    await page.waitForTimeout(300);
  }
  return await toasts();
}
async function step(name, fn) {
  problems.length = 0;
  const started = Date.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail, ms: Date.now() - started });
    console.log(`PASS ${name} :: ${detail}`);
  } catch (e) {
    results.push({ name, ok: false, error: e.message, ms: Date.now() - started });
    console.log(`FAIL ${name} :: ${e.message}`);
  }
  if (problems.length) {
    console.log(`  problems: ${problems.slice(0, 6).join(" || ")}`);
    results[results.length - 1].problems = problems.slice(0, 6);
  }
}

// ------------------------------------------------------------------- ML
await goto("/app/ml", 6000);
await step("ml.metrics.rendered", async () => {
  const text = await page.locator("body").innerText();
  const ml = (await api("/ml/metrics")).data;
  const warn = (await api("/qoe/warnings")).data;
  const checks = {
    accuracyCard: text.includes("Classifier accuracy"),
    confusion: text.includes("Where the classifier gets it right"),
    shadowNote: text.includes("Shadow inference"),
    warnerCard: text.includes("QoE degradation warner"),
    warnerModel: text.includes(warn?.modelKind ?? "qoe_lightgbm"),
    devicePrediction: (warn?.devices ?? []).some((d) => typeof d.pRed === "number"),
    timeline: text.includes("Model history"),
    liveInference: typeof ml?.inference?.totalMl === "number" && ml.inference.totalMl > 0,
  };
  const bad = Object.entries(checks).filter(([, v]) => !v).map(([k]) => k);
  return `accuracy=${ml?.accuracy} f1=${ml?.f1} shadowEvaluated=${ml?.inference?.shadow?.totals?.evaluated} warner=${warn?.modelKind} pRed=${warn?.devices?.[0]?.pRed} checks=${JSON.stringify(checks)}${bad.length ? ` MISSING=${bad.join(",")}` : ""}`;
});
let versionBefore = null;
await step("ml.retrain", async () => {
  const before = (await api("/ml/metrics")).data;
  versionBefore = before?.models?.[0]?.version ?? null;
  const trainedBefore = before?.trainedAt ?? null;
  const modelsBefore = before?.models?.length ?? 0;
  await page.getByRole("button", { name: /Retrain locally/ }).click({ force: true });
  await page.waitForTimeout(500);
  const started = Date.now();
  let after = null;
  while (Date.now() - started < 120000) {
    const task = page.getByRole("button", { name: /Retrain locally/ });
    const pending = await task.isDisabled().catch(() => false);
    after = (await api("/ml/metrics")).data;
    if (!pending && after?.trainedAt !== trainedBefore) break;
    await page.waitForTimeout(1500);
  }
  await page.waitForTimeout(1500);
  after = (await api("/ml/metrics")).data;
  const modelsAfter = after?.models?.length ?? 0;
  return `models ${modelsBefore}->${modelsAfter} newest=${after?.models?.[0]?.version} accuracy=${after?.accuracy} trainedAtChanged=${after?.trainedAt !== trainedBefore} toast="${await toasts()}"`;
});
await step("ml.rollback", async () => {
  const before = (await api("/ml/metrics")).data;
  const activeBefore = before?.models?.find((m) => m.status === "active")?.version;
  await page.getByRole("button", { name: /Roll back model/ }).click({ force: true });
  await page.waitForTimeout(2500);
  const after = (await api("/ml/metrics")).data;
  const activeAfter = after?.models?.find((m) => m.status === "active")?.version;
  return `active ${activeBefore} -> ${activeAfter} versionCount=${after?.models?.length} toast="${await toasts()}"`;
});

// ---------------------------------------------------------- experiments
await goto("/app/experiments", 4500);
await step("experiments.run", async () => {
  // One trial keeps the live run short; the harness still exercises all
  // three controllers and the same seeded trace.
  const trials = page.locator('input[type="number"]').nth(1);
  await trials.fill("1");
  const seed = page.getByLabel("Random seed");
  await seed.fill("4242");
  await page.getByRole("button", { name: /Run experiment/ }).click({ force: true });
  const started = Date.now();
  let exp = null;
  let sawRunning = false;
  while (Date.now() - started < 180000) {
    const list = (await api("/experiments")).data || [];
    exp = list[0];
    if (exp?.status === "running" || exp?.status === "queued") sawRunning = true;
    if (exp?.status === "completed" || exp?.status === "failed") break;
    await page.waitForTimeout(2000);
  }
  await page.waitForTimeout(3000);
  const text = await page.locator("body").innerText();
  return `id=${exp?.id} status=${exp?.status} sawRunning=${sawRunning} results=${exp?.results?.length} error=${exp?.error} uiCompleted=${text.includes("Completed")} uiTable=${text.includes("Call p95 RTT")}`;
});
await step("experiments.report", async () => {
  const format = page.getByLabel("Report format");
  await format.selectOption("json");
  const [download] = await Promise.all([
    page.waitForEvent("download", { timeout: 20000 }),
    page.getByRole("button", { name: /Download report/ }).click({ force: true }),
  ]);
  const suggested = download.suggestedFilename();
  const path = await download.path();
  let size = 0;
  if (path) size = fs.statSync(path).size;
  return `filename=${suggested} bytes=${size}`;
});

// ----------------------------------------------------------------- links
await goto("/app/links", 4000);
await step("links.evaluate", async () => {
  const text = await page.locator("body").innerText();
  const links = (await api("/links")).data || [];
  // Links is capability-gated; when the agent reports one uplink the button is
  // intentionally hidden.  If the gate is on, exercise the mutation.
  const button = page.getByRole("button", { name: /Evaluate paths/ });
  if (await button.count()) {
    await button.click({ force: true });
    await page.waitForTimeout(2000);
    return `links=${links.length} evaluated=true banner=${(await page.locator("body").innerText()).includes("Recommended")}`;
  }
  return `links=${links.length} gate=off body="${text.replace(/\n+/g, " | ").slice(0, 220)}"`;
});

const failed = results.filter((r) => !r.ok);
console.log(`\nSUMMARY ${results.length - failed.length}/${results.length} passed`);
fs.writeFileSync("/tmp/opencode/mutations-ml.json", JSON.stringify(results, null, 2));
await browser.close();
process.exit(failed.length ? 1 : 0);
