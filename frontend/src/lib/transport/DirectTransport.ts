import { RelayTransport } from "./RelayTransport";

/**
 * Direct LAN transport: connects to the agent's HTTP(S) endpoint with a
 * bearer session token.  The pairing-link TLS fingerprint is pinned when the
 * agent serves HTTPS; browsers still enforce HTTPS/mixed-content rules, so a
 * plain-HTTP page can only talk to a loopback agent.
 */
export class DirectTransport extends RelayTransport {
  constructor(fingerprint?: string, agentHost?: string) {
    const raw =
      agentHost ||
      import.meta.env?.VITE_AGENT_HTTP_URL ||
      "http://localhost:7443";
    const httpUrl = raw.replace(/\/$/, "");
    super({ mode: "direct", httpUrl, fingerprint });
  }

  async connect(key: string): Promise<unknown> {
    const url = new URL(this.agentHttpUrl);
    const page = typeof location !== "undefined" ? location : null;
    const loopback = ["localhost", "127.0.0.1", "::1", "0.0.0.0"].includes(
      url.hostname,
    );
    if (url.protocol === "https:" && page && page.protocol !== "https:" && !loopback) {
      throw new Error("Direct LAN requires a secure (HTTPS) page context.");
    }
    return super.connect(key);
  }

  get isDirect(): boolean {
    return true;
  }
}
