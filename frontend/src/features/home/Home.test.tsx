import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import Home from "./Home";

const MOCK_SNAPSHOT = {
  agent: {
    id: "eb-agent",
    name: "Experience Budget Agent",
    version: "0.1.0",
    intervalSeconds: 30,
    capabilities: [],
    emulated: false,
    seed: 42,
  },
  ts: new Date().toISOString(),
  mode: "adaptive",
  link: { capacityMbps: 100, uncertaintyMbps: 0, forecast: [], forecastConfidence: 1 },
  household: { qoe: 0.94, fairness: 0.98, worstDeviceId: null },
  devices: [],
  metrics: { latencyMs: 0, jitterMs: 0, lossPct: 0, throughputMbps: 0, queueDepthPkts: 0 },
  alerts: [],
  scenario: null,
  tick: 0,
  connected: true,
};
const MOCK_SETTINGS = {
  floorMbps: 2,
  rateChangePct: 20,
  dwellSeconds: 10,
  forecastThreshold: 0.7,
  classifierThreshold: 0.6,
  intervalSeconds: 30,
  retentionDays: 30,
  federated: false,
  demandLearning: false,
  flags: { conflicts: true, health: true },
};
const MOCK_CAPABILITIES = {
  flags: { conflicts: true, health: true },
  classes: ["call", "game", "stream", "bulk", "unknown"],
  scenarios: [],
  formulas: {},
  seed: 42,
};

class FakeWebSocket {
  static readonly OPEN = 1;
  static readonly CONNECTING = 0;
  readyState = FakeWebSocket.OPEN;
  onopen: ((event: unknown) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  onclose: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(public url: string) {
    queueMicrotask(() => this.onopen?.({}));
  }
  close() {
    this.onclose?.({});
  }
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Home", () => {
  it("renders the home console", async () => {
    expect(Home).toBeTypeOf("function");
    vi.stubGlobal("scrollTo", vi.fn());
    vi.stubGlobal("WebSocket", FakeWebSocket);
    window.history.replaceState({}, "", "/app");

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      const pathname = url.pathname.replace(/^\/api\/v1/, "");
      if (pathname === "/state/snapshot") {
        return new Response(JSON.stringify(MOCK_SNAPSHOT), {
          headers: { "content-type": "application/json" },
        });
      }
      if (pathname === "/settings") {
        return new Response(JSON.stringify(MOCK_SETTINGS), {
          headers: { "content-type": "application/json" },
        });
      }
      if (pathname === "/capabilities") {
        return new Response(JSON.stringify(MOCK_CAPABILITIES), {
          headers: { "content-type": "application/json" },
        });
      }
      if (pathname.startsWith("/metrics") || pathname.startsWith("/decisions") || pathname.startsWith("/intents")) {
        return new Response(JSON.stringify([]), {
          headers: { "content-type": "application/json" },
        });
      }
      return new Response(JSON.stringify({}), { headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByText(/Household experience/, {}, { timeout: 5000 }),
    ).toBeTruthy();
  }, 15000);
});
