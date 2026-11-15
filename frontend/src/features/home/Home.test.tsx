import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { queryClient, getTransport } from "../../lib/api";
import Home from "./Home";

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("Home", () => {
  it("renders the home console", async () => {
    expect(typeof getTransport).toBe("function");
    expect(Home).toBeTypeOf("function");
    vi.stubGlobal("scrollTo", vi.fn());
    window.history.replaceState({}, "", "/app");

    // Seed the shell data so the smoke test is not dependent on backend availability.
    queryClient.setQueryData(["/state/snapshot"], {
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
    });
    queryClient.setQueryData(["/settings"], { mode: "adaptive", shapingFactor: 0.8, slowIntervalSeconds: 30, fastIntervalSeconds: 2 });
    queryClient.setQueryData(["/capabilities"], { flags: {}, classes: [], scenarios: [], formulas: {}, seed: 42 });

    render(<App />);

    expect(
      await screen.findByText(/Household experience/, {}, { timeout: 5000 }),
    ).toBeTruthy();
  }, 15000);
});
