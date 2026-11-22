// Join a real Jitsi meeting with fake media so the browser sends real
// WebRTC/RTP traffic over the host NIC (real call-shaped flows for labelling).
import { chromium } from "@playwright/test";

const room = process.argv[2] || `ebtest${Date.now()}`;
const dwellS = Number(process.argv[3] || 90);
const b = await chromium.launch({
  args: [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--no-sandbox",
  ],
});
const p = await b.newPage({ viewport: { width: 1280, height: 800 } });
try {
  await p.goto(`https://meet.jit.si/${room}#config.prejoinPageEnabled=false`, {
    waitUntil: "domcontentloaded",
    timeout: 45000,
  });
  console.log("room opened:", room);
  await p.waitForTimeout(15000);
  // accept any dialog that might block media
  for (const name of ["Accept", "Allow", "Join", "I agree"]) {
    const btn = p.getByRole("button", { name }).first();
    if (await btn.count()) await btn.click({ timeout: 2000 }).catch(() => {});
  }
  console.log("dwelling", dwellS, "s to generate real RTP...");
  await p.waitForTimeout(dwellS * 1000);
} catch (e) {
  console.log("jitsi error:", String(e).slice(0, 200));
}
await b.close();
console.log("done");
