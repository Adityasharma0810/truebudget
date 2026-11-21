import { chromium } from "@playwright/test";

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  storageState: "/tmp/opencode/paired-state.json",
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const problems = [];
page.on("pageerror", (e) => problems.push(`PAGEERROR ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") problems.push(`CONSOLE ${m.text().slice(0, 200)}`);
});
await page.goto("http://localhost:8443/app/devices", {
  waitUntil: "domcontentloaded",
  timeout: 30000,
});
let found = null;
const start = Date.now();
while (Date.now() - start < 40000) {
  await page.waitForTimeout(1500);
  const text = await page.locator("body").innerText();
  const line = text
    .split("\n")
    .find(
      (l) =>
        l.includes("p(red)") ||
        l.includes("At risk") ||
        l.includes("Degraded now"),
    );
  if (line) {
    found = line;
    break;
  }
}
const warn = await page.evaluate(async () => {
  const agents = JSON.parse(localStorage.getItem("eb-agents") || "[]");
  const r = await fetch("http://localhost:7443/api/v1/qoe/warnings", {
    headers: { Authorization: `Bearer ${agents[0]?.accessToken || ""}` },
  });
  return r.json();
});
console.log("UI_WARN_LINE", found ?? "none");
console.log("API_WARN", JSON.stringify(warn));
if (found) await page.screenshot({ path: "/tmp/opencode/qoe-warn-ui.png" });
console.log("PROBLEMS", problems.join(" | ") || "none");
await browser.close();
process.exit(found ? 0 : 1);
