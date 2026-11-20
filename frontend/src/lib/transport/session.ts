/**
 * Browser-side agent/session storage.
 *
 * Only session tokens are persisted.  Pairing keys are single-use and are
 * never written to localStorage; a remembered agent is remembered by its
 * scoped access + refresh tokens.
 */

export type TransportMode = "relay" | "direct";

export interface StoredAgent {
  agentId: string;
  name: string;
  mode: TransportMode;
  relayUrl?: string;
  httpUrl?: string;
  publicKey?: string;
  fingerprint?: string;
  accessToken?: string;
  refreshToken?: string;
  expiresAt?: string;
  role?: string;
  lastSeen?: string;
}

const AGENTS_KEY = "eb-agents";
const ACTIVE_KEY = "eb-active-agent";
const LEGACY_KEY = "eb-key";

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage may be blocked; pairing still works for this tab */
  }
}

export function listAgents(): StoredAgent[] {
  const agents = readJson<StoredAgent[]>(AGENTS_KEY, []);
  return Array.isArray(agents) ? agents.filter((a) => a && a.agentId) : [];
}

export function getAgent(agentId: string): StoredAgent | undefined {
  return listAgents().find((a) => a.agentId === agentId);
}

export function getActiveAgent(): StoredAgent | undefined {
  const id = (() => {
    try {
      return localStorage.getItem(ACTIVE_KEY) || "";
    } catch {
      return "";
    }
  })();
  if (id) return getAgent(id);
  return listAgents()[0];
}

export function saveAgent(agent: StoredAgent): void {
  const agents = listAgents().filter((a) => a.agentId !== agent.agentId);
  agents.unshift({ ...agent, lastSeen: new Date().toISOString() });
  writeJson(AGENTS_KEY, agents);
  try {
    localStorage.setItem(ACTIVE_KEY, agent.agentId);
  } catch {
    /* ignore */
  }
}

export function updateAgent(agentId: string, patch: Partial<StoredAgent>): void {
  const agents = listAgents();
  const index = agents.findIndex((a) => a.agentId === agentId);
  if (index < 0) return;
  agents[index] = { ...agents[index], ...patch, lastSeen: new Date().toISOString() };
  writeJson(AGENTS_KEY, agents);
}

export function setActiveAgent(agentId: string): void {
  try {
    localStorage.setItem(ACTIVE_KEY, agentId);
  } catch {
    /* ignore */
  }
}

export function removeAgent(agentId: string): void {
  const agents = listAgents().filter((a) => a.agentId !== agentId);
  writeJson(AGENTS_KEY, agents);
  try {
    if (localStorage.getItem(ACTIVE_KEY) === agentId) {
      if (agents[0]) localStorage.setItem(ACTIVE_KEY, agents[0].agentId);
      else localStorage.removeItem(ACTIVE_KEY);
    }
  } catch {
    /* ignore */
  }
}

export function clearAgents(): void {
  try {
    localStorage.removeItem(AGENTS_KEY);
    localStorage.removeItem(ACTIVE_KEY);
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* ignore */
  }
}
