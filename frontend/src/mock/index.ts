import { Engine } from "./engine";
import { handle } from "./handlers";
import { MockError } from "./faults";
export function createMockServer() {
  const engine = new Engine();
  engine.start();
  return {
    async request(method: string, path: string, body?: unknown) {
      if (path.startsWith("/dev/"))
        return structuredClone(await handle(engine, method, path, body));
      await new Promise((resolve) =>
        setTimeout(resolve, engine.faults.slow ? 2000 : 80 + Math.random() * 240),
      );
      if (engine.faults.offline)
        throw new MockError("OFFLINE", "The agent is offline. Showing the last known snapshot.");
      if (engine.faults.rateLimit)
        throw new MockError("RATE_LIMIT", "Too many requests. Please try again shortly.");
      return structuredClone(await handle(engine, method, path, body));
    },
    subscribe: (event: string, cb: (data: unknown) => void) => engine.subscribe(event, cb),
    async connect(key: string) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      if (!/^EB-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(key))
        throw new MockError("BAD_KEY", "Use a key in the format EB-XXXX-XXXX-XXXX.");
      if (key.includes("EXPR"))
        throw new MockError("EXPIRED", "This key has expired. Generate a new key on the agent.");
      if (key.includes("USED")) throw new MockError("USED", "This key has already been used.");
      if (key.includes("FAIL"))
        throw new MockError("UNAUTHORIZED", "Authentication failed. Check your pairing key.");
      engine.snapshot.connected = true;
      engine.persist();
      return engine.snapshot;
    },
    dispose: () => engine.stop(),
  };
}
