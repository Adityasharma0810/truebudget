import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on("pageerror", e => console.log("PAGEERROR:", e.message.slice(0, 200)));
p.on("console", m => console.log("CONSOLE:", m.type(), m.text().slice(0, 150)));
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const slider = p.getByLabel("Maximum rate change");
await slider.focus();
for (let i = 0; i < 5; i++) await slider.press("ArrowRight");
await p.waitForTimeout(300);
// Click and observe toasts
await p.getByRole("button", { name: "Save settings" }).click();
await p.waitForTimeout(1500);
const toasts = await p.evaluate(() => document.body.innerText);
console.log("toast-area:", toasts.split("\n").filter(l => /saved|error|fail|success/i.test(l)).slice(0, 4));
await p.waitForTimeout(2000);
await b.close();
