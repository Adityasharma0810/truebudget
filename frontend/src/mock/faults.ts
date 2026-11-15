import type { MockControl } from "../types";
export const initialFaults: MockControl = {
  offline: false,
  stale: false,
  slow: false,
  rateLimit: false,
  speed: Number(import.meta.env?.VITE_MOCK_SPEED) || 10,
  seed: 4242,
};
export class MockError extends Error {
  constructor(
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}
