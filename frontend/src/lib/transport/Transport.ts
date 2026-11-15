export type TransportStatus = "connected" | "connecting" | "offline" | "unavailable";
export interface Transport {
  status: TransportStatus;
  request(method: string, path: string, body?: unknown): Promise<unknown>;
  subscribe(event: string, cb: (data: unknown) => void): () => void;
  connect(key: string): Promise<unknown>;
}
