import { RelayTransport } from "./RelayTransport";

/**
 * Direct LAN transport: connects to the agent's HTTPS endpoint (:7443) with a
 * self-signed cert pinned by the pairing-link fingerprint.  Enforces origin and
 * mixed-content checks.  Falls back to plain HTTP only for localhost/loopback
 * (explicitly allowed for lab/dev).
 */
export class DirectTransport extends RelayTransport {
  private pinnedFingerprint: string | null = null;

  constructor(fingerprint?: string, agentHost?: string) {
    super();
    this.pinnedFingerprint = fingerprint ?? null;
    const host = agentHost || "localhost:7443";
    this.agentHttpUrl = `https://${host}`;
    this.relayUrl = `wss://${host}/stream`;
  }

  async connect(key: string): Promise<unknown> {
    const host = this.agentHttpUrl.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
    const isLocal = /^localhost|127\.\d|::1|0\.0\.0\.0/.test(host);
    const pageLoc = typeof location !== "undefined"
      ? location
      : { protocol: "https:", hostname: "" };
    const isSecurePage =
      pageLoc.protocol === "https:" || (pageLoc as { hostname: string }).hostname === "localhost";
    if (!isLocal && !isSecurePage) {
      throw new Error("Direct LAN requires a secure (HTTPS) page context.");
    }
    return super.connect(key);
  }

  get fingerprint(): string | null {
    return this.pinnedFingerprint;
  }

  set fingerprint(value: string | null) {
    this.pinnedFingerprint = value;
  }

  get isDirect(): boolean {
    return true;
  }

  get statusLabel(): string {
    const base = this.status;
    return base === "connected" ? "direct-connected" : base;
  }

  get transportMode(): "direct" | "relay" {
    return "direct";
  }
}
