import { useQuery, useMutation, useQueryClient, QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { z } from "zod";
import { RelayTransport } from "../transport/RelayTransport";
import { DirectTransport } from "../transport/DirectTransport";
import { getActiveAgent, saveAgent, updateAgent, type TransportMode } from "../transport/session";
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
function defaultMode(): TransportMode {
  const configured = import.meta.env?.VITE_TRANSPORT as TransportMode | undefined;
  if (configured === "direct" || configured === "relay") return configured;
  // Relay mode needs a paired agent id, so a fresh browser that has never
  // paired cannot use it.  Until an agent is stored, talk straight to the
  // local agent API instead of silently showing an empty console.
  return getActiveAgent()?.mode ?? "direct";
}

export interface PairingLink {
  key: string;
  agentId: string;
  publicKey?: string;
}

/**
 * Parse the pairing link printed by `eb-agent pair` when it is opened in this
 * browser: `…?key=EB-…&agent=eb-…#pk=<hex>`.  The key is single-use and is
 * never persisted; the agent id and pinned public key are.
 */
export function parsePairingLink(): PairingLink | null {
  try {
    const params = new URLSearchParams(window.location.search);
    const rawKey = params.get("key") || "";
    const agentId = params.get("agent") || "";
    if (!rawKey || !agentId) return null;
    const pk = window.location.hash.startsWith("#pk=")
      ? window.location.hash.slice(4)
      : "";
    return { key: rawKey.trim().toUpperCase(), agentId: agentId.trim(), publicKey: pk.trim() || undefined };
  } catch {
    return null;
  }
}

/**
 * A browser that opens a pairing link must remember the agent BEFORE the
 * transport is constructed (the constructor reads the stored agent).  The
 * key itself is kept only in memory for the one connect() call.
 */
let pendingPairing: PairingLink | null = parsePairingLink();
if (pendingPairing) {
  const stored = getActiveAgent();
  if (!stored || stored.agentId !== pendingPairing.agentId) {
    saveAgent({
      agentId: pendingPairing.agentId,
      name: pendingPairing.agentId,
      mode: import.meta.env?.VITE_TRANSPORT === "direct" ? "direct" : "relay",
      httpUrl: import.meta.env?.VITE_AGENT_HTTP_URL || undefined,
      relayUrl: import.meta.env?.VITE_RELAY_URL || undefined,
      publicKey: pendingPairing.publicKey,
    });
  } else if (pendingPairing.publicKey && !stored.publicKey) {
    updateAgent(stored.agentId, { publicKey: pendingPairing.publicKey });
  }
}

let currentTransport: Transport =
  defaultMode() === "relay"
    ? new RelayTransport()
    : new DirectTransport();
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
