import { useQuery, useMutation, useQueryClient, QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { z } from "zod";
import { RelayTransport } from "../transport/RelayTransport";
import { DirectTransport } from "../transport/DirectTransport";
import type { Transport } from "../transport/Transport";
import {
  SnapshotSchema,
  IntentSchema,
  DecisionSchema,
  DeviceSchema,
  SettingsSchema,
  type Snapshot,
} from "../../types";
export const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 1500, refetchOnWindowFocus: false } },
});
const mode = import.meta.env?.VITE_TRANSPORT ?? "relay";
let currentTransport: Transport =
  mode === "relay"
    ? new RelayTransport()
    : mode === "direct"
      ? new DirectTransport()
      : (() => { throw new Error(`Unknown transport: ${mode}`); })();
export function setTransport(t: Transport) {
  currentTransport = t;
}
export function getTransport(): Transport {
  return currentTransport;
}
export const transport = new Proxy({} as Transport, {
  get(_target, prop) {
    const value = (currentTransport as any)[prop];
    // Bind methods to the live transport. Without this, `this` would be the
    // proxy target, so instance state (socket, subscribers) is read back as
    // undefined and the WebSocket cannot be set up.
    return typeof value === "function" ? value.bind(currentTransport) : value;
  },
});
const schemas: Record<string, z.ZodType> = {
  "/state/snapshot": SnapshotSchema,
  "/intents": z.array(IntentSchema),
  "/decisions": z.array(DecisionSchema),
  "/devices": z.array(DeviceSchema),
  "/settings": SettingsSchema,
};
export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const data = await transport.request(method, path, body);
  return (method === "GET" && schemas[path] ? schemas[path].parse(data) : data) as T;
}
export function useApi<T>(
  path: string,
  options?: { refetchInterval?: number | false; enabled?: boolean },
) {
  return useQuery<T>({ queryKey: [path], queryFn: () => request<T>("GET", path), ...options });
}
export function useAction<T = unknown>(path: string, method = "POST") {
  const client = useQueryClient();
  return useMutation<T, Error, unknown>({
    mutationFn: (body) => request<T>(method, path, body),
    onSuccess: () => {
      void client.invalidateQueries();
    },
  });
}
export function useSnapshot() {
  const client = useQueryClient();
  const query = useApi<Snapshot>("/state/snapshot", { refetchInterval: 3000 });
  useEffect(() => {
    const unsubscribeSnapshot = transport.subscribe("snapshot", (value: unknown) =>
      client.setQueryData(["/state/snapshot"], value),
    );
    const unsubscribeTick = transport.subscribe("tick", (value: unknown) => {
      const counters = (value as { counters?: Record<string, unknown> } | null)?.counters;
      if (!counters) return;
      const throughput = Number(counters.throughput_mbps);
      const capacity = Number(counters.capacity_mbps);
      client.setQueryData<Snapshot>(["/state/snapshot"], (old) => {
        if (!old) return old;
        return {
          ...old,
          link: {
            ...old.link,
            capacityMbps: Number.isFinite(capacity) ? capacity : old.link.capacityMbps,
          },
          metrics: {
            ...old.metrics,
            throughputMbps: Number.isFinite(throughput) ? throughput : old.metrics.throughputMbps,
          },
        };
      });
    });
    return () => {
      unsubscribeSnapshot();
      unsubscribeTick();
    };
  }, [client]);
  return query;
}
