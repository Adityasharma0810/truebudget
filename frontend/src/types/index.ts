import { z } from "zod";
export const TrafficClassSchema = z.enum(["call", "game", "stream", "bulk", "unknown"]);
export const QoEBandSchema = z.enum(["green", "amber", "red", "idle"]);
export const ModeSchema = z.enum(["adaptive", "reactive_fallback", "safe_state"]);
export const FlowSchema = z.object({
  id: z.string(),
  cls: TrafficClassSchema,
  confidence: z.number().min(0).max(1),
  qoe: z.number().nullable(),
  band: QoEBandSchema,
  throughputMbps: z.number(),
  features: z.record(z.string(), z.number()),
  baselineCls: TrafficClassSchema.optional(),
  method: z.enum(["ml", "rule"]).optional(),
  modelClass: TrafficClassSchema.optional(),
  modelConfidence: z.number().min(0).max(1).nullable().optional(),
});
export const DeviceSchema = z.object({
  id: z.string(),
  name: z.string(),
  person: z.string().optional(),
  type: z.string().optional(),
  ip: z.string().optional(),
  online: z.boolean().optional(),
  lastSeen: z.string().optional(),
  source: z.string().optional(),
  qoe: z.number().nullable(),
  band: QoEBandSchema,
  flows: z.array(FlowSchema),
  qoeWarn: z
    .object({
      state: z.string(),
      alert: z.boolean(),
      pRed: z.number().nullable(),
      source: z.string(),
      horizonS: z.number().nullable(),
    })
    .optional(),
});
export const ForecastSchema = z.object({
  ts: z.string(),
  mbps: z.number(),
  lo: z.number(),
  hi: z.number(),
});
export const AlertSchema = z.object({
  id: z.string(),
  message: z.string(),
  severity: z.enum(["info", "warning", "error"]),
});
export const SnapshotSchema = z.object({
  agent: z.object({
    id: z.string(),
    name: z.string(),
    version: z.string(),
    intervalSeconds: z.number(),
    capabilities: z.array(z.string()),
    emulated: z.boolean(),
    seed: z.number(),
  }),
  ts: z.string(),
  mode: ModeSchema,
  link: z.object({
    capacityMbps: z.number(),
    uncertaintyMbps: z.number(),
    forecast: z.array(ForecastSchema),
    forecastConfidence: z.number(),
    forecastMethod: z.string().optional(),
    forecastGate: z
      .object({
        useForecast: z.boolean(),
        reason: z.string(),
        threshold: z.number(),
      })
      .optional(),
  }),
  household: z.object({
    qoe: z.number().nullable(),
    fairness: z.number(),
    worstDeviceId: z.string().nullable(),
  }),
  devices: z.array(DeviceSchema),
  metrics: z.object({
    latencyMs: z.number(),
    jitterMs: z.number(),
    lossPct: z.number(),
    throughputMbps: z.number(),
    queueDepthPkts: z.number(),
    queueDepthBytes: z.number().optional(),
  }),
  alerts: z.array(AlertSchema),
  scenario: z.string().nullable(),
  tick: z.number(),
  connected: z.boolean(),
});
export const IntentInputSchema = z.object({
  target: z.object({ deviceId: z.string(), cls: TrafficClassSchema }),
  goal: z.string().min(3),
  priority: z.enum(["low", "normal", "high"]),
  durationSeconds: z.number().min(60).max(86400),
  deadline: z.string().nullable().optional(),
  allowedTradeoffs: z.array(z.string()),
  minServiceFloor: z.literal(true),
});
export const IntentSchema = IntentInputSchema.extend({
  id: z.string(),
  expiresAt: z.string(),
  status: z.enum(["pending", "active", "expired", "cancelled", "infeasible"]),
});
export const DecisionSchema = z.object({
  id: z.string(),
  ts: z.string(),
  trigger: z.string(),
  reason: z.string(),
  before: z.record(z.string(), z.number()),
  after: z.record(z.string(), z.number()),
  verdict: z.enum(["kept", "rolled_back", "escalated"]),
  evidence: z.unknown(),
  hash: z.string(),
  prevHash: z.string(),
});
export const SettingsSchema = z.object({
  floorMbps: z.number().min(1).max(20),
  rateChangePct: z.number().min(1).max(50),
  dwellSeconds: z.number().min(10).max(300),
  forecastThreshold: z.number().min(0.5).max(1),
  classifierThreshold: z.number().min(0.5).max(1),
  intervalSeconds: z.number().min(5).max(120),
  retentionDays: z.number().min(1).max(365),
  federated: z.boolean(),
  demandLearning: z.boolean(),
  flags: z.record(z.string(), z.boolean()),
});
export const ErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  details: z.unknown().optional(),
});
export type TrafficClass = z.infer<typeof TrafficClassSchema>;
export type QoEBand = z.infer<typeof QoEBandSchema>;
export type Mode = z.infer<typeof ModeSchema>;
export type Flow = z.infer<typeof FlowSchema>;
export type Device = z.infer<typeof DeviceSchema>;
export type Snapshot = z.infer<typeof SnapshotSchema>;
export type Intent = z.infer<typeof IntentSchema>;
export type IntentInput = z.infer<typeof IntentInputSchema>;
export type Decision = z.infer<typeof DecisionSchema>;
export type Settings = z.infer<typeof SettingsSchema>;
export interface MetricPoint {
  ts: string;
  capacity: number;
  lo: number;
  hi: number;
  throughput: number;
  latency: number;
  baseline: number;
  jitter: number;
  loss: number;
  queue: number;
  fairness: number;
}
export interface Scenario {
  id: string;
  name: string;
  description: string;
  icon: string;
  baseline: number;
  managed: number;
  duration: string;
  tag: string;
}
export interface Policy {
  floorMbps: number;
  allocations: Record<string, number>;
}
export interface Link {
  id: string;
  name: string;
  type: string;
  rtt: number;
  loss: number;
  capacity: number;
  throughput: number;
  state: string;
}
export interface Diagnosis {
  id: string;
  title: string;
  confidence: number;
  signals: string[];
  recommendation: string;
  status: string;
}
export interface Conflict {
  id: string;
  title: string;
  description: string;
  status: string;
  options: { name: string; description: string; allocations: number[] }[];
}
export interface Schedule {
  id: string;
  name: string;
  device: string;
  time: string;
  duration: string;
  confidence: number;
  enabled: boolean;
}
export interface Experiment {
  id: string;
  scenario: string;
  seed: number;
  trials: number;
  progress: number;
  status: string;
  error?: string | null;
  emulated?: boolean;
  results: {
    metric: string;
    unit: string;
    unmanaged: number;
    cake: number;
    eb: number;
    ci: number;
  }[];
}
export interface Capabilities {
  flags: Record<string, boolean>;
  classes: TrafficClass[];
  scenarios: Scenario[];
  formulas: Record<string, string>;
  seed: number;
}
export interface Correction {
  id: string;
  flowId: string;
  device: string;
  from: string;
  to: string;
  status: string;
}
export interface MLData {
  accuracy: number | null;
  f1: number | null;
  baseline: number | null;
  baselineF1?: number | null;
  encrypted: number | null;
  matrix: number[][];
  labels: string[];
  trained?: boolean;
  modelKind?: string;
  trainingSource?: string;
  trainingNote?: string;
  trainedAt?: string | null;
  nTrain?: number | null;
  nTest?: number | null;
  inference?: {
    lastTickMl: number;
    lastTickRule: number;
    totalMl: number;
    totalRule: number;
    byClass: Record<string, number>;
    lastAt: string | null;
    threshold?: number;
    shadow?: {
      last: {
        evaluated: number;
        accepted: number;
        belowThreshold: number;
        domainGuard: number;
        untrained: number;
      };
      totals: {
        evaluated: number;
        accepted: number;
        belowThreshold: number;
        domainGuard: number;
        untrained: number;
      };
      byClass: Record<string, number>;
    };
  };
  models: { version: string; status: string; accuracy: number | null; date: string }[];
  corrections: Correction[];
}
export interface DemandForecastClass {
  trafficClass: string;
  horizonSeconds: number;
  predictedMbps: number;
  confidence: number;
  samples: number;
  method: string;
}
export interface DemandForecast {
  classes: DemandForecastClass[];
  horizonSeconds: number;
  capacityMbps: number | null;
  method: string;
  learning: boolean;
  updatedAt: string | null;
  synthetic: boolean;
}
export interface AgentInfo {
  hostname: string;
  os: string;
  version: string;
  uptime: string;
  dbSize: string;
  relay: string;
  loopMs: number;
  seed: number;
  emulated: boolean;
}
export interface Session {
  id: string;
  name: string;
  location: string;
  lastSeen: string;
  current: boolean;
}
export interface LogEntry {
  id: string;
  ts: string;
  action: string;
  detail: string;
}
export interface AskAnswer {
  text: string;
  citations: string[];
  disclaimer: string;
}
export interface MockControl {
  offline: boolean;
  stale: boolean;
  slow: boolean;
  rateLimit: boolean;
  speed: number;
  seed: number;
}
