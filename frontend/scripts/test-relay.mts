// Minimal relay-protocol client: hello -> expect e2e_ready -> send request.
import { generateKeyPair, hex, SecureChannel, deriveKey, unhex } from "../src/lib/crypto/e2e.ts";

const ws = new WebSocket("ws://127.0.0.1:7444/ws/browser?agent_id=");
const pair = generateKeyPair();
const session = "testsession" + Date.now();
let channel: SecureChannel | null = null;
const t0 = Date.now();

ws.addEventListener("open", () => {
  ws.send(JSON.stringify({ type: "e2e_hello", session, browserPub: hex(pair.publicKey) }));
  console.log("sent hello at", Date.now() - t0, "ms");
});
ws.addEventListener("message", async (ev: MessageEvent) => {
  const msg = JSON.parse(String(ev.data));
  console.log("<<", msg.type, Date.now() - t0, "ms", JSON.stringify(msg).slice(0, 120));
  if (msg.type === "e2e_ready") {
    channel = new SecureChannel(deriveKey(pair.privateKey, unhex(msg.agentPub)));
    const env = await channel.seal({
      type: "request",
      id: "r1",
      method: "GET",
      path: "/api/v1/agent/info",
      accessToken: "",
    });
    ws.send(JSON.stringify({ type: "e2e", session, envelope: env }));
  }
  if (msg.type === "e2e" && channel) {
    const payload = await channel.open(msg.envelope);
    console.log("RESPONSE:", JSON.stringify(payload).slice(0, 220));
    process.exit(0);
  }
});
ws.addEventListener("close", (ev: CloseEvent) => console.log("closed", ev.code, ev.reason));
ws.addEventListener("error", () => console.log("error"));
setTimeout(() => {
  console.log("TIMEOUT");
  process.exit(1);
}, 10000);
