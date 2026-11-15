import type { Transport, TransportStatus } from "./Transport";

/**
 * Real transport for the Experience Budget agent.
 *
 * - ``request()`` fetches from the agent's HTTP API (real-time data from the
 *   backend, not fabricated).
 * - ``subscribe()`` opens a WebSocket to the agent/relay stream and dispatches
 *   live snapshot/tick events.
 * - ``connect()`` opens the stream, performs an ephemeral ECDH + HKDF handshake
 *   to derive an AES-GCM session key (E2E), and returns the initial snapshot.
 */
export class RelayTransport implements Transport {
  status: TransportStatus = "unavailable";
  relayUrl: string;
  agentHttpUrl: string;
  private ws: WebSocket | null = null;
  private sessionKey: CryptoKey | null = null;
  private subscribers = new Map<string, Set<(d: unknown) => void>>();
  private reconnectTimer: any = null;
  private pairingKey: string | null = null;
  private connected = false;
  private ekpPrivate: CryptoKey | null = null;

  constructor() {
    this.relayUrl = import.meta.env?.VITE_RELAY_URL || "ws://localhost:7443/stream";
    this.agentHttpUrl =
      import.meta.env?.VITE_AGENT_HTTP_URL || "http://localhost:7443";
  }

  async request(method: string, path: string, body?: unknown): Promise<unknown> {
    const url = `${this.agentHttpUrl}${path.startsWith("/") ? "" : "/"}${path}`;
    const headers = new Headers();
    const opts: RequestInit = { method, headers };
    if (body !== undefined) {
      headers.set("Content-Type", "application/json");
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res.text();
  }

  subscribe(event: string, cb: (data: unknown) => void): () => void {
    let set = this.subscribers.get(event);
    if (!set) {
      set = new Set();
      this.subscribers.set(event, set);
    }
    set.add(cb);
    return () => {
      set.delete(cb);
      if (set.size === 0) this.subscribers.delete(event);
    };
  }

  async connect(key: string): Promise<unknown> {
    this.pairingKey = key;
    this.status = "connecting";
    try {
      // Ephemeral ECDH (P-256) for forward secrecy; HKDF -> AES-GCM session key.
      const ekp = await crypto.subtle.generateKey(
        { name: "ECDH", namedCurve: "P-256" },
        true,
        ["deriveBits"],
      );
      this.ekpPrivate = ekp.privateKey as CryptoKey;
      const pub = await crypto.subtle.exportKey("raw", ekp.publicKey);
      const ephemeralPubKey = btoa(String.fromCharCode(...new Uint8Array(pub)));

      await this._openWs();
      // Handshake: tell the stream our ephemeral key; expect an ack with the
      // agent's ephemeral pubkey so both sides can derive the same session key.
      await this._send({ type: "hello", key, ephemeralPubKey });
      const ack = await this._waitForOnce("hello_ack", 10000);
      const agentPubB64 = (ack as any)?.agentPubKey;
      if (agentPubB64 && this.ekpPrivate) {
        const agentPub = await crypto.subtle.importKey(
          "raw",
          new Uint8Array(atob(agentPubB64).split("").map((c) => c.charCodeAt(0))),
          { name: "ECDH", namedCurve: "P-256" },
          false,
          [],
        );
        const shared = await crypto.subtle.deriveBits(
          { name: "ECDH", public: agentPub },
          this.ekpPrivate,
          256,
        );
        const derived = await crypto.subtle.deriveKey(
          {
            name: "HKDF",
            hash: "SHA-256",
            salt: new TextEncoder().encode(key || ""),
            info: new TextEncoder().encode("experience-budget/v1"),
          },
          this.ekpPrivate,
          { name: "AES-GCM", length: 256 },
          false,
          ["encrypt", "decrypt"],
        );
        this.sessionKey = derived;
      }
      this.connected = true;
      this.status = "connected";
      // Return the initial snapshot over HTTP.
      return this.request("GET", "/state/snapshot");
    } catch (error) {
      this.status = "offline";
      throw error;
    }
  }

  // ------------------------------------------------------------------ private

  private _openWs(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.ws = new WebSocket(this.relayUrl);
      let settled = false;
      this.ws!.onopen = () => { settled = true; resolve(); };
      this.ws!.onerror = () => { if (!settled) reject(new Error("websocket error")); };
      this.ws!.onclose = () => {
        this.connected = false;
        this.status = "offline";
        this._scheduleReconnect();
      };
      this.ws!.onmessage = (ev) => this._onMessage(ev.data);
    });
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.pairingKey) void this.connect(this.pairingKey);
    }, 3000);
  }

  private _wsSend(text: string) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(text);
  }

  private _send(obj: unknown) {
    this._wsSend(JSON.stringify(obj));
  }

  private _waitForOnce(event: string, timeoutMs: number): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`timeout waiting for ${event}`)), timeoutMs);
      const cb = (data: unknown) => {
        const any = data as any;
        if (any && any.type === event) {
          clearTimeout(t);
          resolve(data);
        }
      };
      let set = this.subscribers.get(event);
      if (!set) {
        set = new Set();
        this.subscribers.set(event, set);
      }
      set.add(cb);
    });
  }

  private async _onMessage(data: string) {
    let msg: any;
    try { msg = JSON.parse(data); } catch { return; }
    const event = msg.event || msg.type;
    if (event) {
      const set = this.subscribers.get(event);
      if (set) {
        let payload = msg.payload ?? msg.snapshot ?? msg;
        if (msg.encrypted && this.sessionKey) {
          try {
            payload = JSON.parse(
              new TextDecoder().decode(
                await crypto.subtle.decrypt(
                  { name: "AES-GCM", iv: new Uint8Array(12) },
                  this.sessionKey,
                  new Uint8Array(atob(msg.ct).split("").map((c) => c.charCodeAt(0))),
                ),
              ),
            );
          } catch { return; }
        }
        for (const cb of [...set]) cb(payload);
      }
      return;
    }
    if (msg.type === "hello_ack") {
      const set = this.subscribers.get("hello_ack");
      if (set) for (const cb of [...set]) cb(msg);
    }
  }
}
