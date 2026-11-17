import type { Transport, TransportStatus } from "./Transport";

const API_PREFIX = "/api/v1";

/**
 * Transport for the Experience Budget agent.
 *
 * - ``request()`` talks to the agent's real HTTP API under ``/api/v1``.
 * - ``subscribe()`` lazily opens the agent's WebSocket stream and dispatches
 *   real ``snapshot`` / ``tick`` events.
 * - ``connect()`` opens the stream and returns the current snapshot.
 *
 * The agent does not require a pairing handshake to stream read-only state,
 * so a missing ``hello_ack`` must never block the console.
 */
export class RelayTransport implements Transport {
  status: TransportStatus = "unavailable";
  relayUrl: string;
  agentHttpUrl: string;
  private ws: WebSocket | null = null;
  private subscribers = new Map<string, Set<(d: unknown) => void>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pairingKey: string | null = null;
  private connected = false;
  private started = false;

  constructor() {
    const base = import.meta.env?.VITE_RELAY_URL || "ws://localhost:7443";
    this.relayUrl = base;
    this.agentHttpUrl = import.meta.env?.VITE_AGENT_HTTP_URL || "http://localhost:7443";
  }

  async request(method: string, path: string, body?: unknown): Promise<unknown> {
    const url = this._apiUrl(path);
    const headers = new Headers();
    const opts: RequestInit = { method, headers };
    if (body !== undefined) {
      headers.set("Content-Type", "application/json");
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(url, opts);
    if (!res.ok) {
      const text = await res.text();
      let message = text;
      try {
        const parsed = JSON.parse(text);
        message = parsed.message || parsed.detail?.message || text;
      } catch {
        /* keep raw text */
      }
      throw new Error(`HTTP ${res.status}: ${message}`);
    }
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
    this._ensureStream();
    return () => {
      set.delete(cb);
      if (set.size === 0) this.subscribers.delete(event);
    };
  }

  async connect(key: string): Promise<unknown> {
    this.pairingKey = key;
    this.status = "connecting";
    await this._ensureStream();
    try {
      const snapshot = await this.request("GET", "/state/snapshot");
      this.status = "connected";
      return snapshot;
    } catch (error) {
      this.status = "offline";
      throw error;
    }
  }

  // ------------------------------------------------------------------ private

  private _apiUrl(path: string): string {
    const base = this.agentHttpUrl.replace(/\/$/, "");
    const suffix = path.startsWith("/") ? path : `/${path}`;
    return suffix.startsWith(API_PREFIX) ? `${base}${suffix}` : `${base}${API_PREFIX}${suffix}`;
  }

  private _wsUrl(): string {
    const raw = this.relayUrl || this.agentHttpUrl;
    let url: URL;
    try {
      url = new URL(raw);
    } catch {
      url = new URL(this.agentHttpUrl);
    }
    if (url.protocol === "http:") url.protocol = "ws:";
    if (url.protocol === "https:") url.protocol = "wss:";
    const path = url.pathname.replace(/\/$/, "");
    if (!path || path === "") {
      url.pathname = `${API_PREFIX}/stream`;
    } else if (!path.startsWith(API_PREFIX)) {
      url.pathname = `${API_PREFIX}${path}`;
    }
    return url.toString();
  }

  private _ensureStream(): Promise<void> {
    this.started = true;
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return Promise.resolve();
    }
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      try {
        this.ws = new WebSocket(this._wsUrl());
      } catch {
        this.status = "offline";
        this._scheduleReconnect();
        finish();
        return;
      }
      this.status = this.status === "connected" ? "connected" : "connecting";
      this.ws.onopen = () => {
        this.connected = true;
        this.status = "connected";
        finish();
      };
      this.ws.onerror = () => {
        if (!this.connected) this.status = "offline";
        finish();
      };
      this.ws.onclose = () => {
        this.connected = false;
        this.status = "offline";
        finish();
        this._scheduleReconnect();
      };
      this.ws.onmessage = (ev) => this._onMessage(String(ev.data));
      // Never leave the UI waiting on a socket that cannot connect.
      setTimeout(finish, 3000);
    });
  }

  private _scheduleReconnect() {
    if (this.reconnectTimer || (!this.started && !this.pairingKey)) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.subscribers.size || this.pairingKey) void this._ensureStream();
    }, 3000);
  }

  private _dispatch(event: string, payload: unknown) {
    const set = this.subscribers.get(event);
    if (!set) return;
    for (const cb of [...set]) {
      try {
        cb(payload);
      } catch {
        /* a subscriber must not break the stream */
      }
    }
  }

  private _onMessage(data: string) {
    let msg: any;
    try {
      msg = JSON.parse(data);
    } catch {
      return;
    }
    const event = msg.event || msg.type;
    if (msg.snapshot) this._dispatch("snapshot", msg.snapshot);
    if (event) {
      const payload =
        event === "tick" ? msg : (msg.payload ?? msg.snapshot ?? msg);
      this._dispatch(event, payload);
    }
    if (event === "hello_ack") this._dispatch("hello_ack", msg);
  }
}
