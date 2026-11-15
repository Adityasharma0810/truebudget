import type { Transport, TransportStatus } from "./Transport";
export class RelayTransport implements Transport {
  status: TransportStatus = "unavailable";
  // TODO: authenticated relay request/stream protocol once a real agent exists.
  async request(_method: string, _path: string, _body?: unknown): Promise<unknown> {
    throw new Error(
      "Relay is not connected. Choose the emulated demo or connect a real agent in a future release.",
    );
  }
  subscribe(_event: string, _cb: (data: unknown) => void) {
    return () => {};
  }
  async connect(_key: string): Promise<unknown> {
    throw new Error("Real relay transport is not implemented. No connection has been made.");
  }
}
