import seedrandom from "seedrandom";
import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import agent from "../data/agent.json";
import devices from "../data/devices.json";
import settings from "../data/settings.json";
import seeds from "../data/decisions.seed.json";
import conflicts from "../data/conflicts.json";
import schedules from "../data/demand.schedules.json";
import ml from "../data/ml.metrics.json";
import sessions from "../data/sessions.json";
import audit from "../data/audit.json";
import { initialFaults } from "../faults";
import { callQoE, gameQoE, streamQoE, bulkQoE, qoeBand, fairness } from "../../lib/qoe";
import type {
  Snapshot,
  Intent,
  Decision,
  Settings,
  Conflict,
  Schedule,
  MLData,
  Session,
  Experiment,
  MockControl,
  LogEntry,
} from "../../types";
import { SnapshotSchema, SettingsSchema, DecisionSchema, IntentSchema } from "../../types";
import { z } from "zod";
export const decisionHash = (decision: Omit<Decision, "hash">) =>
  bytesToHex(sha256(new TextEncoder().encode(JSON.stringify(decision))));
export class Engine {
  snapshot: Snapshot;
  intents: Intent[] = [];
  decisions: Decision[] = [];
  settings = structuredClone(settings) as Settings;
  conflicts = structuredClone(conflicts) as Conflict[];
  schedules = structuredClone(schedules) as Schedule[];
  ml = structuredClone(ml) as MLData;
  sessions = structuredClone(sessions) as Session[];
  audit = structuredClone(audit) as LogEntry[];
  experiments: Experiment[] = [];
  faults = { ...initialFaults };
  listeners = new Map<string, Set<(value: unknown) => void>>();
  private rng = seedrandom("4242");
  private timer: ReturnType<typeof setInterval> | undefined;
  private scenarioTick = 0;
  constructor() {
    this.snapshot = {
      agent,
      ts: new Date().toISOString(),
      mode: "adaptive",
      link: { capacityMbps: 100, uncertaintyMbps: 5.2, forecast: [], forecastConfidence: 0.91 },
      household: { qoe: 0.94, fairness: 0.98, worstDeviceId: "D4" },
      devices: structuredClone(devices) as Snapshot["devices"],
      metrics: {
        latencyMs: 18,
        jitterMs: 2.4,
        lossPct: 0.02,
        throughputMbps: 68.4,
        queueDepthPkts: 6,
      },
      alerts: [],
      scenario: null,
      tick: 0,
      connected: true,
    };
    for (let i = 47; i >= 0; i--) {
      const s = seeds[i % seeds.length];
      this.addDecision(
        s.trigger,
        s.reason,
        s.verdict as Decision["verdict"],
        new Date(Date.parse(this.snapshot.ts) - i * 240000).toISOString(),
      );
    }
    try {
      const saved = localStorage.getItem("eb-engine-v1");
      if (saved) {
        const data = JSON.parse(saved);
        if (data.version === 1 && SnapshotSchema.safeParse(data.state?.snapshot).success) {
          const state = data.state;
          this.snapshot = SnapshotSchema.parse(state.snapshot);
          this.settings = SettingsSchema.parse(state.settings);
          this.decisions = z.array(DecisionSchema).parse(state.decisions);
          this.intents = z.array(IntentSchema).parse(state.intents);
          for (const key of ["conflicts", "schedules", "sessions", "audit", "experiments"] as const)
            if (Array.isArray(state[key])) Object.assign(this, { [key]: state[key] });
          if (state.ml?.models && state.ml?.corrections) this.ml = state.ml;
        }
      }
    } catch {
      /* Private mode: in-memory fallback. */
    }
    this.rng = seedrandom(String(this.snapshot.agent.seed) + this.snapshot.tick);
    this.refreshForecast();
  }
  start() {
    if (!this.timer) this.timer = setInterval(() => this.tick(), 1000);
  }
  stop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = undefined;
  }
  startScenario(name: string | null) {
    this.scenarioTick = 0;
    this.snapshot.scenario = name;
    this.snapshot.link.capacityMbps = name === "wan-drop" ? 20 : 100;
    this.refreshForecast();
  }
  verifyDecisions() {
    return this.decisions.every(({ hash, ...d }, i) =>
      hash === decisionHash(d) &&
      d.prevHash === (this.decisions[i + 1]?.hash ?? "GENESIS")
    );
  }
  persist() {
    try {
      localStorage.setItem(
        "eb-engine-v1",
        JSON.stringify({
          version: 1,
          state: {
            snapshot: this.snapshot,
            intents: this.intents,
            decisions: this.decisions,
            settings: this.settings,
            conflicts: this.conflicts,
            schedules: this.schedules,
            ml: this.ml,
            sessions: this.sessions,
            audit: this.audit,
            experiments: this.experiments,
          },
        }),
      );
    } catch {
      /* Storage is optional. */
    }
  }
  emit(event: string, value: unknown) {
    this.listeners.get(event)?.forEach((cb) => cb(structuredClone(value)));
  }
  subscribe(event: string, cb: (data: unknown) => void) {
    const set = this.listeners.get(event) ?? new Set();
    set.add(cb);
    this.listeners.set(event, set);
    return () => {
      set.delete(cb);
    };
  }
  addDecision(
    trigger: string,
    reason: string,
    verdict: Decision["verdict"] = "kept",
    ts = this.snapshot.ts,
  ) {
    const previous = this.decisions[0];
    const item: Omit<Decision, "hash"> = {
      id: `dec-${this.decisions.length + 1}`,
      ts,
      trigger,
      reason,
      before: { qoe: 0.82, latencyMs: 34, bulkMbps: 24 },
      after: { qoe: 0.94, latencyMs: 18, bulkMbps: 18 },
      verdict,
      evidence: {
        source: "emulated verifier",
        windowSeconds: 30,
        floorMbps: this.settings.floorMbps,
        privacy: "metadata only",
      },
      prevHash: previous?.hash ?? "GENESIS",
    };
    const decision = { ...item, hash: decisionHash(item) };
    this.decisions.unshift(decision);
    this.emit("decision", decision);
    return decision;
  }
  refreshForecast() {
    this.snapshot.link.forecast = Array.from({ length: 12 }, (_, i) => {
      const mbps = this.snapshot.link.capacityMbps + Math.sin(i * 0.4) * 2 - i * 0.2;
      return {
        ts: new Date(Date.parse(this.snapshot.ts) + (i + 1) * 30000).toISOString(),
        mbps,
        lo: mbps - 5 - i * 0.3,
        hi: mbps + 5 + i * 0.3,
      };
    });
  }
  tick() {
    if (this.faults.offline || this.faults.stale) return;
    const s = this.snapshot;
    s.ts = new Date(Date.parse(s.ts) + 1000 * this.faults.speed).toISOString();
    s.tick++;
    for (const intent of this.intents)
      if (intent.status === "active" && Date.parse(intent.expiresAt) <= Date.parse(s.ts)) {
        intent.status = "expired";
        this.addDecision(
          "Intent auto-reverted",
          `${intent.goal} expired. Restored the normal allocation and preserved service floors.`,
        );
        this.emit("intent", intent);
      }
    for (const experiment of this.experiments)
      if (experiment.status === "running") {
        experiment.progress = Math.min(100, experiment.progress + 5);
        if (experiment.progress === 100) experiment.status = "completed";
      }
    if (s.scenario) {
      this.scenarioTick++;
      if (this.scenarioTick === 3)
        this.addDecision(
          "Capacity change detected",
          `Rebalanced allocations for ${s.scenario}. Protected call and game queues within 3 seconds.`,
        );
      if (this.scenarioTick === 5 && s.scenario === "wan-drop")
        this.addDecision(
          "Verifier: allocation rejected",
          "The first adjustment increased latency. Rolled back and applied a more conservative allocation.",
          "rolled_back",
        );
      if (this.scenarioTick >= 24) {
        s.scenario = null;
        this.addDecision("Scenario completed", "Restored pre-scenario capacity and allocation.");
      }
    }
    const capacity = s.scenario === "wan-drop" ? 20 : 100;
    s.link.capacityMbps = +(capacity + (this.rng() - 0.5) * 3).toFixed(1);
    s.metrics.latencyMs = +(18 + this.rng() * 4 + (s.scenario ? 3 : 0)).toFixed(1);
    s.metrics.jitterMs = +(1.8 + this.rng() * 1.5).toFixed(1);
    s.metrics.throughputMbps = +(s.link.capacityMbps * (0.64 + this.rng() * 0.08)).toFixed(1);
    s.metrics.queueDepthPkts = 4 + Math.round(this.rng() * 5);
    const activeDevices = s.devices.filter(device => device.flows.length > 0);
    const weight = (id: string) => {
      const demand = devices.find(d => d.id === id)?.flows.reduce((sum, flow) => sum + flow.throughputMbps, 0) || 1;
      return demand * (this.intents.some(i => i.status === "active" && i.target.deviceId === id) ? 1.8 : 1);
    };
    const totalWeights = activeDevices.reduce((sum, d) => sum + weight(d.id), 0);
    const available = Math.max(0, s.link.capacityMbps * .82 - activeDevices.length * this.settings.floorMbps);
    s.devices.forEach((device) => {
      const baseDevice = devices.find(d => d.id === device.id);
      const baseTotal = baseDevice?.flows.reduce((sum,f) => sum + f.throughputMbps,0) || 1;
      const allocation = this.settings.floorMbps + available * weight(device.id) / totalWeights;
      device.flows.forEach((flow) => {
        const v = flow.features;
        const original = baseDevice?.flows.find(f => f.id === flow.id);
        if (flow.confidence < this.settings.classifierThreshold && flow.cls !== "unknown") {
          flow.baselineCls = flow.cls;
          flow.cls = "unknown";
        } else if (flow.confidence >= this.settings.classifierThreshold && flow.cls === "unknown" && flow.baselineCls) {
          flow.cls = flow.baselineCls;
        }
        v.rtt = s.metrics.latencyMs;
        v.jitter = s.metrics.jitterMs;
        flow.throughputMbps = +(allocation * (original?.throughputMbps ?? 1) / baseTotal).toFixed(1);
        flow.qoe =
          flow.cls === "call"
            ? callQoE(v.rtt, v.jitter, v.loss)
            : flow.cls === "game"
              ? gameQoE(v.rtt, v.jitter, v.loss)
              : flow.cls === "stream"
                ? streamQoE(flow.throughputMbps, v.target ?? 20, v.cov ?? 0.08, v.rebuffer ?? 0)
                : flow.cls === "bulk"
                  ? bulkQoE(flow.throughputMbps, original?.throughputMbps ?? 20)
                  : null;
        flow.band = qoeBand(flow.qoe);
      });
      const q = device.flows.flatMap((f) => (f.qoe === null ? [] : [f.qoe]));
      device.qoe = q.length ? Math.min(...q) : null;
      device.band = qoeBand(device.qoe);
    });
    const active = s.devices.filter((d) => d.qoe !== null);
    s.household.qoe = active.reduce((sum, d) => sum + d.qoe!, 0) / active.length;
    s.household.fairness = fairness(active.map((d) => d.qoe!));
    s.household.worstDeviceId = active.slice().sort((a, b) => a.qoe! - b.qoe!)[0]?.id ?? null;
    if (s.mode !== "safe_state")
      s.mode =
        s.link.forecastConfidence >= this.settings.forecastThreshold
          ? "adaptive"
          : "reactive_fallback";
    this.refreshForecast();
    this.persist();
    this.emit("snapshot", s);
    this.emit("tick", { ts: s.ts });
  }
  setFaults(patch: Partial<MockControl>) {
    Object.assign(this.faults, patch);
    if (patch.seed) {
      this.snapshot.agent.seed = patch.seed;
      this.rng = seedrandom(String(patch.seed));
    }
    this.emit("alert", this.faults);
  }
  record(action: string, detail: string) {
    this.audit.unshift({ id: `a-${this.audit.length + 1}`, ts: this.snapshot.ts, action, detail });
    this.persist();
  }
}
