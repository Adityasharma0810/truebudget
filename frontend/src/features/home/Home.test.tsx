import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "../../App";
import { queryClient, transport, getTransport } from "../../lib/api";
import { MockTransport } from "../../lib/transport/MockTransport";
import Home from "./Home";

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

describe("Home", () => {
  it("renders the home console with the mock transport", async () => {
    expect(getTransport()).toBeInstanceOf(MockTransport);
    expect(Home).toBeTypeOf("function");
    vi.stubGlobal("scrollTo", vi.fn());
    window.history.replaceState({}, "", "/app");

    // Seed the shell data through the mock transport so the smoke test is not
    // dependent on timer flushing in jsdom. Home's own queries still run.
    queryClient.setQueryData(
      ["/state/snapshot"],
      await transport.request("GET", "/state/snapshot"),
    );
    queryClient.setQueryData(["/settings"], await transport.request("GET", "/settings"));
    queryClient.setQueryData(["/capabilities"], await transport.request("GET", "/capabilities"));

    render(<App />);

    expect(
      await screen.findByText(/Household experience/, {}, { timeout: 5000 }),
    ).toBeTruthy();
  }, 15000);
});
