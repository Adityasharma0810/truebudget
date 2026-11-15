import { createMockServer } from "../../mock";
import { SnapshotSchema } from "../../types";
import type { Transport, TransportStatus } from "./Transport";
export class MockTransport implements Transport {
  status: TransportStatus = "connected";
  private server = createMockServer();
  async request(method: string, path: string, body?: unknown) {
    try {
      const result = await this.server.request(method, path, body);
      if (path === "/state/snapshot") return SnapshotSchema.parse(result);
      return result;
    } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "OFFLINE")
        this.status = "offline";
      throw error;
    }
  }
  subscribe(event: string, cb: (data: unknown) => void) {
    return this.server.subscribe(event, (data) => {
      if (event === "snapshot") {
        this.status = "connected";
        cb(SnapshotSchema.parse(data));
      } else cb(data);
    });
  }
  async connect(key: string) {
    this.status = "connecting";
    try {
      const snapshot = SnapshotSchema.parse(await this.server.connect(key));
      this.status = "connected";
      return snapshot;
    } catch (e) {
      this.status = "offline";
      throw e;
    }
  }
}
