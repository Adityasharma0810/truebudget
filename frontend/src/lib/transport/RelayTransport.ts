import type { Transport, TransportStatus } from "./Transport";
import {
  deriveKey,
  generateKeyPair,
  hex,
  SecureChannel,
  unhex,
  type Envelope,
} from "../crypto/e2e";
import {
  getActiveAgent,
  saveAgent,
  updateAgent,
  type StoredAgent,
  type TransportMode,
} from "./session";

const API_PREFIX = "/api/v1";
function pageHostname(): string {
  try {
    return location.hostname || "localhost";
  } catch {
    return "localhost";
  }
}

function defaultRelayUrl(): string {
  const scheme =
    typeof location !== "undefined" && location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${pageHostname()}:7444`;
}

function defaultAgentHttpUrl(): string {
  const scheme =
    typeof location !== "undefined" && location.protocol === "https:" ? "https" : "http";
  return `${scheme}://${pageHostname()}:7443`;
}
const REQUEST_TIMEOUT_MS = 20_000;
const MAX_BACKOFF_MS = 30_000;
const RELAY_FALLBACK_MS = 8_000;

export class TransportError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "TransportError";
    this.status = status;
    this.code = code;
  }
}

interface PendingRequest {
  resolve: (value: { status: number; body: unknown }) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

interface RelayOptions {
  mode?: TransportMode;
  relayUrl?: string;
  httpUrl?: string;
  agentId?: string;
  publicKey?: string;
  fingerprint?: string;
  accessToken?: string;
  refreshToken?: string;
}

function normaliseRelayUrl(raw: string): string {
  let value = raw.trim();
  if (!value) return "";
  if (value.startsWith("https://")) value = `wss://${value.slice(8)}`;
  else if (value.startsWith("http://")) value = `ws://${value.slice(7)}`;
  else if (!value.startsWith("ws://") && !value.startsWith("wss://")) value = `wss://${value}`;
  return value.replace(/\/$/, "");
}

function relaySocketUrl(base: string, agentId: string): string {
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    url = new URL(defaultRelayUrl());
  }
  const path = url.pathname.replace(/\/$/, "");
  if (!path.endsWith("/ws/browser")) {
    url.pathname = path ? `${path}/ws/browser` : "/ws/browser";
  }
  url.search = "";
  url.searchParams.set("agent_id", agentId);
  return url.toString();
}

function httpToWs(raw: string): string {
  return raw.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
}

function randomId(): string {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = new Uint8Array(
    await crypto.subtle.digest("SHA-256", new Uint8Array(bytes).buffer)
  );
  return hex(digest);
}

/**
 * Transport for both real connection modes.
 *
 * Relay mode keeps a browser WebSocket to the relay and wraps every request,
 * response and event in the E2E channel (X25519 + HKDF + AES-256-GCM).  The
 * relay only forwards ciphertext.  Direct LAN mode uses the agent's HTTPS API
 * and WebSocket stream with a bearer session token.
 */
export class RelayTransport implements Transport {
  status: TransportStatus = "unavailable";
  handshakeStatus: "idle" | "hello" | "ready" | "failed" = "idle";
  mode: TransportMode;
  relayUrl: string;
  agentHttpUrl: string;
  agentId: string;
  publicKey: string;
  fingerprint: string;
  role = "";
  lastError = "";

  private ws: WebSocket | null = null;
  private subscribers = new Map<string, Set<(d: unknown) => void>>();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectAttempt = 0;
  private started = false;
  private pairingKey: string | null = null;
  private fallbackTimer: ReturnType<typeof setTimeout> | null = null;
  private accessToken = "";
  private refreshToken = "";
  private e2eSession = "";
  private channel: SecureChannel | null = null;
  private readyPromise: Promise<void> | null = null;
  private readyResolve: (() => void) | null = null;
  private readyReject: ((error: Error) => void) | null = null;
  private pending = new Map<string, PendingRequest>();
  private lastMessageAt = 0;

  constructor(options: RelayOptions = {}) {
    const stored = getActiveAgent();
    this.mode =
      options.mode ??
      ((import.meta.env?.VITE_TRANSPORT as TransportMode | undefined) === "direct"
        ? "direct"
        : "relay");
    this.relayUrl = normaliseRelayUrl(
      options.relayUrl ??
        import.meta.env?.VITE_RELAY_URL ??
        (this.mode === "relay" ? defaultRelayUrl() : "")
    );
    this.agentHttpUrl = (
      options.httpUrl ??
      import.meta.env?.VITE_AGENT_HTTP_URL ??
      defaultAgentHttpUrl()
    ).replace(/\/$/, "");
    this.agentId = options.agentId ?? stored?.agentId ?? "";
    this.publicKey = options.publicKey ?? stored?.publicKey ?? "";
    this.fingerprint = options.fingerprint ?? stored?.fingerprint ?? "";
    this.accessToken = options.accessToken ?? stored?.accessToken ?? "";
    this.refreshToken = options.refreshToken ?? stored?.refreshToken ?? "";
    this.role = stored?.role ?? "";
    if (this.mode === "direct" && stored?.mode === "direct") {
      this.agentHttpUrl = stored.httpUrl || this.agentHttpUrl;
      this.agentId = stored.agentId;
    }
    if (this.mode === "relay" && stored?.relayUrl) this.relayUrl = stored.relayUrl;
  }

  get transportMode(): TransportMode {
    return this.mode;
  }

  get statusLabel(): string {
    return this.status;
  }

  get currentSessionId(): string {
    return this.e2eSession;
  }

  // ------------------------------------------------------------------ public API

  async request(method: string, path: string, body?: unknown): Promise<unknown> {
    const apiPath = path.startsWith(API_PREFIX) ? path : `${API_PREFIX}${path.startsWith("/") ? path : `/${path}`}`;
    if (this.mode === "direct") return this._httpRequest(method, apiPath, body);
    return this._e2eRequest(method, apiPath, body);
  }

  subscribe(event: string, cb: (data: unknown) => void): () => void {
    let set = this.subscribers.get(event);
    if (!set) {
      set = new Set();
      this.subscribers.set(event, set);
    }
    set.add(cb);
    this.started = true;
    void this._ensureSocket().catch(() => {
      /* the UI shows offline and retries automatically */
    });
    return () => {
      set.delete(cb);
      if (set.size === 0) this.subscribers.delete(event);
    };
  }

  async connect(key: string): Promise<unknown> {
    this.pairingKey = key || null;
    this.status = "connecting";
    this.lastError = "";
    try {
      if (this.mode === "direct") return await this._connectDirect(key);
      return await this._connectRelay(key);
    } catch (error) {
      this.status = "offline";
      this.lastError = error instanceof Error ? error.message : String(error);
      throw error;
    }
  }

  async disconnect(): Promise<void> {
    this.started = false;
    this.pairingKey = null;
    this._clearReconnect();
    this._closeSocket();
    this.channel = null;
    this.e2eSession = "";
    this.handshakeStatus = "idle";
    this.status = "offline";
  }

  // ------------------------------------------------------------------ direct mode

  private async _connectDirect(key: string): Promise<unknown> {
    if (key) {
      const paired = (await this._httpRequest("POST", `${API_PREFIX}/pair`, { key }, false)) as {
        agentId?: string;
        agentPublicKey?: string;
        accessToken?: string;
        refreshToken?: string;
        role?: string;
        expiresAt?: string;
      };
      this.agentId = paired.agentId || this.agentId;
      this.publicKey = paired.agentPublicKey || this.publicKey;
      this.accessToken = paired.accessToken || "";
      this.refreshToken = paired.refreshToken || "";
      this.role = paired.role || "owner";
      saveAgent({
        agentId: this.agentId,
        name: this.agentId,
        mode: "direct",
        httpUrl: this.agentHttpUrl,
        publicKey: this.publicKey,
        fingerprint: this.fingerprint,
        accessToken: this.accessToken,
        refreshToken: this.refreshToken,
        role: this.role,
        expiresAt: paired.expiresAt,
      });
    }
    await this._ensureSocket();
    const snapshot = await this.request("GET", "/state/snapshot");
    this.status = "connected";
    return snapshot;
  }

  private async _httpRequest(
    method: string,
    path: string,
    body?: unknown,
    allowRefresh = true
  ): Promise<unknown> {
    const headers = new Headers({ Accept: "application/json" });
    if (this.accessToken) headers.set("Authorization", `Bearer ${this.accessToken}`);
    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(body);
    }
    let response: Response;
    try {
      response = await fetch(`${this.agentHttpUrl}${path}`, init);
    } catch (error) {
      throw new TransportError(
        0,
        "agent_unreachable",
        `The agent at ${this.agentHttpUrl} is unreachable. ${
          error instanceof Error ? error.message : ""
        }`.trim()
      );
    }
    if (response.status === 401 && allowRefresh && this.refreshToken) {
      await this._refreshToken();
      return this._httpRequest(method, path, body, false);
    }
    const text = await response.text();
    let parsed: unknown = text;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      /* keep text */
    }
    if (!response.ok) {
      const detail = (parsed as { message?: string; code?: string; detail?: { message?: string } }) || {};
      throw new TransportError(
        response.status,
        detail.code || "request_failed",
        detail.message || detail.detail?.message || text || `HTTP ${response.status}`
      );
    }
    return parsed;
  }

  private async _refreshToken(): Promise<void> {
    if (!this.refreshToken) throw new TransportError(401, "auth_required", "Pair this browser again.");
    const refreshed = (await this._httpRequest(
      "POST",
      `${API_PREFIX}/session/refresh`,
      { refreshToken: this.refreshToken },
      false
    )) as { accessToken?: string; refreshToken?: string };
    if (!refreshed.accessToken) throw new TransportError(401, "auth_required", "Pair this browser again.");
    this.accessToken = refreshed.accessToken;
    this.refreshToken = refreshed.refreshToken || this.refreshToken;
    if (this.agentId) {
      updateAgent(this.agentId, {
        accessToken: this.accessToken,
        refreshToken: this.refreshToken,
      });
    }
  }

  // ------------------------------------------------------------------ relay mode

  private async _connectRelay(key: string): Promise<unknown> {
    if (!this.agentId) {
      throw new Error(
        "This pairing link is missing the agent id. Open the full link printed by `eb-agent pair`."
      );
    }
    await this._ensureSocket();
    if (key) {
      const paired = (await this._e2eRequest("POST", `${API_PREFIX}/pair`, { key }, false)) as {
        agentId?: string;
        agentPublicKey?: string;
        accessToken?: string;
        refreshToken?: string;
        role?: string;
        expiresAt?: string;
      };
      if (paired.agentId && paired.agentId !== this.agentId) {
        throw new Error("The agent answered with a different agent id.");
      }
      if (paired.agentPublicKey) {
        if (this.publicKey && paired.agentPublicKey !== this.publicKey) {
          throw new Error("The agent's public key does not match the pairing link.");
        }
        this.publicKey = paired.agentPublicKey;
      }
      this.accessToken = paired.accessToken || "";
      this.refreshToken = paired.refreshToken || "";
      this.role = paired.role || "owner";
      saveAgent({
        agentId: this.agentId,
        name: this.agentId,
        mode: "relay",
        relayUrl: this.relayUrl,
        publicKey: this.publicKey,
        fingerprint: this.fingerprint,
        accessToken: this.accessToken,
        refreshToken: this.refreshToken,
        role: this.role,
        expiresAt: paired.expiresAt,
      });
    }
    const snapshot = await this.request("GET", "/state/snapshot");
    this.status = "connected";
    return snapshot;
  }

  private async _e2eRequest(
    method: string,
    path: string,
    body?: unknown,
    allowRefresh = true
  ): Promise<unknown> {
    await this._ensureSocket();
    const id = randomId();
    const result = await new Promise<{ status: number; body: unknown }>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new TransportError(0, "timeout", "The agent did not answer in time."));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      void this._sendSealed({
        type: "request",
        id,
        method,
        path,
        body,
        accessToken: this.accessToken,
      }).catch((error) => {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error instanceof Error ? error : new Error(String(error)));
      });
    });
    if (result.status === 401 && allowRefresh && this.refreshToken) {
      await this._refreshToken();
      return this._e2eRequest(method, path, body, false);
    }
    if (result.status >= 400) {
      const detail = (result.body as { message?: string; code?: string }) || {};
      throw new TransportError(
        result.status,
        detail.code || "request_failed",
        detail.message || `Agent returned HTTP ${result.status}`
      );
    }
    return result.body;
  }

  private async _sendSealed(payload: unknown): Promise<void> {
    const ws = this.ws;
    const channel = this.channel;
    if (!ws || ws.readyState !== WebSocket.OPEN || !channel) {
      throw new TransportError(0, "not_connected", "The encrypted channel is not ready.");
    }
    const envelope = await channel.seal(payload);
    ws.send(JSON.stringify({ type: "e2e", session: this.e2eSession, envelope }));
  }

  // ------------------------------------------------------------------ sockets

  private async _ensureSocket(): Promise<void> {
    if (this.mode === "direct") {
      if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
        return;
      }
      return this._openDirectSocket();
    }
    if (this.channel && this.ws?.readyState === WebSocket.OPEN) return;
    if (this.readyPromise) return this.readyPromise;
    this.readyPromise = new Promise<void>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
    this._openRelaySocket();
    return this.readyPromise;
  }

  private _openDirectSocket(): Promise<void> {
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (!settled) {
          settled = true;
          resolve();
        }
      };
      try {
        this.ws = new WebSocket(`${httpToWs(this.agentHttpUrl)}${API_PREFIX}/stream`);
      } catch {
        this.status = "offline";
        this._scheduleReconnect();
        finish();
        return;
      }
      this.ws.onopen = () => {
        this.status = "connected";
        finish();
      };
      this.ws.onerror = () => {
        this.status = "offline";
        finish();
      };
      this.ws.onclose = () => {
        this.status = "offline";
        finish();
        this._scheduleReconnect();
      };
      this.ws.onmessage = (event) => this._onDirectMessage(String(event.data));
      setTimeout(finish, 3000);
    });
  }

  private _openRelaySocket(): void {
    const url = relaySocketUrl(this.relayUrl || defaultRelayUrl(), this.agentId);
    this.handshakeStatus = "hello";
    // A fresh browser that never paired may reach a relay where its agent id
    // is unknown.  A paired browser whose handshake stalls (lost e2e_ready,
    // relay hiccup) would otherwise hang forever: arm the fallback timer in
    // both cases.  The timer is armed once, not per attempt, so reconnect
    // retries cannot postpone the fallback forever.
    if (!this.fallbackTimer) {
      this.fallbackTimer = setTimeout(() => {
        this.fallbackTimer = null;
        if (this.handshakeStatus !== "ready" && this.mode === "relay") {
          this._fallbackToDirect();
        }
      }, RELAY_FALLBACK_MS);
    }
    try {
      this.ws = new WebSocket(url);
    } catch (error) {
      this._failReady(error instanceof Error ? error : new Error(String(error)));
      this._scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.reconnectAttempt = 0;
      this.lastMessageAt = Date.now();
      const pair = generateKeyPair();
      this._browserPrivateKey = pair.privateKey;
      this.e2eSession = randomId();
      this.channel = null;
      this.ws?.send(
        JSON.stringify({
          type: "e2e_hello",
          session: this.e2eSession,
          browserPub: hex(pair.publicKey),
        })
      );
    };
    this.ws.onerror = () => {
      this.lastError = "relay WebSocket error";
      this.status = "offline";
    };
    this.ws.onclose = () => {
      this.status = "offline";
      this.handshakeStatus = "failed";
      this._rejectPending(new Error("The relay connection closed."));
      this._failReady(new Error("The relay connection closed."));
      this._scheduleReconnect();
    };
    this.ws.onmessage = (event) => void this._onRelayMessage(String(event.data));
  }

  private _browserPrivateKey: Uint8Array | null = null;

  private _fallbackToDirect(): void {
    if (this.mode !== "relay") return;
    this.mode = "direct";
    this.handshakeStatus = "idle";
    this._rejectPending(new Error("Switched to the local agent connection."));
    this._failReady(new Error("Relay handshake did not complete; using the local agent API."));
    this._clearReconnect();
    this._closeSocket();
    this._openDirectSocket();
  }

  private _closeSocket(): void {
    if (this.fallbackTimer) {
      clearTimeout(this.fallbackTimer);
      this.fallbackTimer = null;
    }
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      try {
        ws.onclose = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.close();
      } catch {
        /* ignore */
      }
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectTimer || (!this.started && !this.pairingKey)) return;
    if (this.mode === "relay" && !this.agentId) return;
    this.reconnectAttempt += 1;
    const base = Math.min(MAX_BACKOFF_MS, 500 * 2 ** Math.min(this.reconnectAttempt, 6));
    const delay = base * (0.5 + Math.random() * 0.5);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      void this._ensureSocket().catch(() => this._scheduleReconnect());
    }, delay);
  }

  private _clearReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private _rejectPending(error: Error): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(error);
      this.pending.delete(id);
    }
  }

  private _failReady(error: Error): void {
    this.handshakeStatus = "failed";
    this.readyReject?.(error);
    this.readyReject = null;
    this.readyResolve = null;
    this.readyPromise = null;
  }

  // ------------------------------------------------------------------ messages

  private async _onRelayMessage(raw: string): Promise<void> {
    this.lastMessageAt = Date.now();
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw);
    } catch {
      return;
    }
    const kind = String(message.type || "");
    if (kind === "e2e_ready") {
      await this._onE2EReady(message);
      return;
    }
    if (kind === "e2e") {
      await this._onSealed(message);
      return;
    }
    if (kind === "presence") {
      this._dispatch("presence", message);
      if (message.online === false && this.status === "connected") this.status = "offline";
      return;
    }
    if (kind === "agents") {
      // The relay publishes the online agents when this browser has no id.
      const agents = Array.isArray(message.agents) ? (message.agents as string[]) : [];
      if (!this.agentId && agents.length) {
        this.agentId = String(agents[0]);
        this._dispatch("presence", { agentId: this.agentId, online: true, auto: true });
      }
      return;
    }
    if (kind === "pong") {
      return;
    }
  }

  private async _onE2EReady(message: Record<string, unknown>): Promise<void> {
    const agentPub = String(message.agentPub || "");
    if (!agentPub || !this._browserPrivateKey) {
      this._failReady(new Error("The agent did not send a valid public key."));
      return;
    }
    if (this.publicKey && this.publicKey.toLowerCase() !== agentPub.toLowerCase()) {
      this._failReady(
        new Error(
          "The agent's E2E public key does not match the pairing link. Refusing to connect."
        )
      );
      return;
    }
    const keyFingerprint = await sha256Hex(unhex(agentPub));
    if (this.publicKey) {
      const expected = await sha256Hex(unhex(this.publicKey));
      if (expected !== keyFingerprint) {
        this._failReady(new Error("The agent's E2E key fingerprint does not match."));
        return;
      }
    }
    this.publicKey = agentPub;
    this.fingerprint = this.fingerprint || keyFingerprint;
    this.channel = new SecureChannel(deriveKey(this._browserPrivateKey, unhex(agentPub)));
    this._browserPrivateKey = null;
    this.handshakeStatus = "ready";
    this.status = "connected";
    this.lastError = "";
    if (this.fallbackTimer) {
      clearTimeout(this.fallbackTimer);
      this.fallbackTimer = null;
    }
    this._startHeartbeat();
    this.readyResolve?.();
    this.readyResolve = null;
    this.readyReject = null;
    this.readyPromise = null;
  }

  private async _onSealed(message: Record<string, unknown>): Promise<void> {
    if (String(message.session || "") !== this.e2eSession || !this.channel) return;
    const envelope = message.envelope as Envelope | undefined;
    if (!envelope) return;
    let payload: unknown;
    try {
      payload = await this.channel.open(envelope);
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : "E2E authentication failed";
      return;
    }
    if (!payload || typeof payload !== "object") return;
    const data = payload as Record<string, unknown>;
    if (data.type === "response") {
      const id = String(data.id || "");
      const pending = this.pending.get(id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.pending.delete(id);
      pending.resolve({ status: Number(data.status || 0), body: data.body });
      return;
    }
    if (data.type === "event") {
      const event = String(data.event || "message");
      this._dispatch(event, data.payload);
      return;
    }
  }

  private _onDirectMessage(raw: string): void {
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(raw);
    } catch {
      return;
    }
    const event = String(message.event || message.type || "");
    if (message.snapshot) this._dispatch("snapshot", message.snapshot);
    if (event && event !== "snapshot") {
      const payload = event === "tick" ? message : (message.payload ?? message.snapshot ?? message);
      this._dispatch(event, payload);
    }
  }

  private _startHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (this.mode !== "relay" || !this.channel) return;
      if (Date.now() - this.lastMessageAt > 90_000) {
        this.ws?.close();
        return;
      }
      void this._sendSealed({ type: "ping", ts: Date.now() / 1000 }).catch(() => {
        /* reconnect handles a dead socket */
      });
    }, 30_000);
  }

  private _dispatch(event: string, payload: unknown): void {
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
}

export type { StoredAgent };
