import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import Home from "./Home";

const MOCK_SNAPSHOT = {
  agent: { id: "eb-agent", name: "Experience Budget Agent", version: "0.1.0", intervalSeconds: 30, capabilities: [], emulated: false, seed: 42 },
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
const MOCK_SETTINGS = { mode: "adaptive", shapingFactor: 0.8, slowIntervalSeconds: 30, fastIntervalSeconds: 2 };
const MOCK_CAPABILITIES = { flags: {}, classes: [], scenarios: [], formulas: {}, seed: 42 };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Home", () => {
  it("renders the home console", async () => {
    expect(Home).toBeTypeOf("function");
    vi.stubGlobal("scrollTo", vi.fn());
    window.history.replaceState({}, "", "/app");

    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = new URL(String(input));
      const pathname = url.pathname;
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
      return new Response(JSON.stringify({}), { headers: { "content-type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(
      await screen.findByText(/Household experience/, {}, { timeout: 5000 }),
    ).toBeTruthy();
  }, 15000);
});
