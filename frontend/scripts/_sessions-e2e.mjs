import { chromium } from "@playwright/test";

const key = process.env.EB_PAIR_KEY;
if (!key) {
  console.error("EB_PAIR_KEY is required");
  process.exit(2);
}
const parts = key.replace(/^EB-/, "").split("-");
const API = "http://localhost:7443/api/v1";
const browser = await chromium.launch({ headless: true });
const ctxA = await browser.newContext({
  storageState: "/tmp/opencode/paired-state.json",
  viewport: { width: 1440, height: 1000 },
});
const ctxB = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const pageA = await ctxA.newPage();
const pageB = await ctxB.newPage();
const problems = [];
for (const [tag, page] of [
  ["A", pageA],
  ["B", pageB],
]) {
  page.on("pageerror", (e) => problems.push(`${tag} PAGEERROR ${e.message}`));
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(`${tag} CONSOLE ${m.text().slice(0, 200)}`);
  });
}

// ---- pair the second browser through the UI
await pageB.goto("http://localhost:8443/connect", {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});
await pageB.locator("select").selectOption("direct");
await pageB.waitForTimeout(300);
const inputs = pageB.locator('input[aria-label^="Key segment"]');
for (let i = 0; i < 3; i++) await inputs.nth(i).fill(parts[i] ?? "");
await pageB.locator("button.full-width").click({ force: true });
await pageB.waitForURL("**/app", { timeout: 30000 });
await pageB.waitForTimeout(2500);
const tokenB = await pageB.evaluate(() => {
  const agents = JSON.parse(localStorage.getItem("eb-agents") || "[]");
  return agents[0]?.accessToken || "";
});
const sessionB = await (
  await fetch(`${API}/session`, { headers: { Authorization: `Bearer ${tokenB}` } })
).json();
console.log("B paired", sessionB.sessionId, sessionB.role);

// ---- open the first browser's agent page and revoke B by row
await pageA.goto("http://localhost:8443/app/agent", {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});
await pageA.waitForTimeout(2500);
const tokenA = await pageA.evaluate(() => {
  const agents = JSON.parse(localStorage.getItem("eb-agents") || "[]");
  return agents[0]?.accessToken || "";
});
const sessionsBefore = await (
  await fetch(`${API}/sessions`, { headers: { Authorization: `Bearer ${tokenA}` } })
).json();
const rowB = sessionsBefore.find((s) => s.id === sessionB.sessionId);
console.log(
  "sessions before",
  sessionsBefore.map((s) => `${s.role}:${s.id.slice(0, 8)}:${s.lastSeen}`),
);
console.log("target row B", rowB ? rowB.lastSeen : "MISSING");
const row = pageA.locator(".session-row").filter({ hasText: rowB.lastSeen }).first();
await row.waitFor({ timeout: 15000 });
const rowText = (await row.innerText()).replace(/\n+/g, " | ");
await row.getByRole("button", { name: /Revoke/ }).click();
await pageA.waitForTimeout(400);
await pageA.getByRole("button", { name: /Confirm revoke/ }).click();
await pageA.waitForTimeout(3000);

const afterStatus = await fetch(`${API}/session`, {
  headers: { Authorization: `Bearer ${tokenB}` },
}).then((r) => r.status);
const sessionsAfter = await (
  await fetch(`${API}/sessions`, { headers: { Authorization: `Bearer ${tokenA}` } })
).json();
const stillListed = sessionsAfter.some((s) => s.id === sessionB.sessionId);
const audit = await (
  await fetch(`${API}/audit`, { headers: { Authorization: `Bearer ${tokenA}` } })
).json();
const revokeAudit = (audit.items ?? audit).find((e) => e.action === "session.revoke");

// ---- what does the revoked browser show?
await pageB.waitForTimeout(6000);
const bodyB = (await pageB.locator("body").innerText()).replace(/\n+/g, " | ").slice(0, 500);
const bUrl = pageB.url();

console.log("A row text", rowText);
console.log("B token after revoke", afterStatus);
console.log("B still listed", stillListed, "audit", revokeAudit ? "present" : "missing");
console.log("B url", bUrl);
console.log("B body", bodyB);
console.log("PROBLEMS", problems.length ? problems.slice(0, 10).join(" | ") : "none");
await pageA.screenshot({ path: "/tmp/opencode/sessions-after-revoke.png", fullPage: false });
await browser.close();
process.exit(afterStatus === 401 && !stillListed && revokeAudit ? 0 : 1);
