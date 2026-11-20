import { chromium } from "@playwright/test";
const b = await chromium.launch();
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
p.on("response", async (r) => {
  if (r.request().method() === "PUT") {
    console.log("PUT", r.url(), r.status(), (await r.text()).slice(0, 200));
  }
});
p.on("console", m => { if (m.type() === "error") console.log("ERR:", m.text().slice(0, 150)); });
await p.goto("http://localhost:8443/app/settings", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForFunction(() => document.body.innerText.includes("Mbps"), null, { timeout: 20000 });
const slider = p.getByLabel("Maximum rate change");
await slider.focus();
for (let i = 0; i < 5; i++) await slider.press("ArrowRight");
await p.waitForTimeout(300);
await p.getByRole("button", { name: "Save settings" }).click();
await p.waitForTimeout(3000);
await b.close();
