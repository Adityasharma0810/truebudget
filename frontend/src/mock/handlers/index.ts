import { z } from "zod";
import type { Engine } from "../engine";
import { MockError } from "../faults";
import capabilities from "../data/capabilities.json";
import scenarios from "../data/scenarios.json";
import diagnoses from "../data/health.diagnoses.json";
import links from "../data/links.json";
import formulas from "../data/formulas.json";
import answers from "../data/ask.answers.json";
import logs from "../data/logs.json";
import { generateMetrics, experimentResults } from "../generators";
import {
  IntentInputSchema,
  SettingsSchema,
  TrafficClassSchema,
  type Experiment,
} from "../../types";
export async function handle(
  e: Engine,
  method: string,
  path: string,
  body?: unknown,
): Promise<unknown> {
  const url = new URL(path, "https://mock.local");
  const p = url.pathname;
  const b = body as Record<string, unknown> | undefined;
  if (p === "/dev/control") {
    if (method === "GET") return e.faults;
    e.setFaults(z.object({
      offline:z.boolean().optional(), stale:z.boolean().optional(), slow:z.boolean().optional(),
      rateLimit:z.boolean().optional(), speed:z.number().min(1).max(60).optional(),
      seed:z.number().int().optional(),
    }).parse(b ?? {}));
    return e.faults;
  }
  if (p === "/dev/reset") {
    try {
      localStorage.removeItem("eb-engine-v1");
    } catch {}
    return { ok: true };
  }
  if (p === "/capabilities")
    return { ...capabilities, flags: e.settings.flags, seed: e.snapshot.agent.seed };
  if (p === "/state/snapshot") return e.snapshot;
  if (p === "/metrics")
    return generateMetrics(
      e.snapshot.agent.seed,
      e.snapshot.ts,
      url.searchParams.get("range") ?? "1h",
      e.experiments.find(experiment => experiment.id === url.searchParams.get("experiment"))?.scenario ?? e.snapshot.scenario,
    ).map((point) =>
      url.searchParams.get("device") && url.searchParams.get("device") !== "all"
        ? { ...point, throughput: point.throughput / 4 }
        : point,
    );
  if (p === "/devices") {
    if (method === "PATCH") {
      const patch = z
        .object({
          id: z.string(),
          name: z.string().min(1).max(60),
          person: z.string().max(40).optional(),
        })
        .parse(body);
      const d = e.snapshot.devices.find((d) => d.id === patch.id);
      if (!d) throw new MockError("NOT_FOUND", "Device not found.");
      Object.assign(d, patch);
      e.record("Device updated", patch.id);
    }
    return e.snapshot.devices;
  }
  if (p === "/flows") return e.snapshot.devices.flatMap((d) => d.flows);
  if (/^\/flows\/[^/]+\/correct$/.test(p)) {
    const id = p.split("/")[2];
    const { cls } = z.object({ cls: TrafficClassSchema }).parse(body);
    const d = e.snapshot.devices.find((d) => d.flows.some((f) => f.id === id));
    const f = d?.flows.find((f) => f.id === id);
    if (!f) throw new MockError("NOT_FOUND", "Flow not found.");
    e.ml.corrections.unshift({
      id: `cor-${e.ml.corrections.length + 1}`,
      flowId: id,
      device: d!.name,
      from: f.cls,
      to: cls,
      status: "pending",
    });
    f.baselineCls = f.cls;
    f.cls = cls;
    f.confidence = 1;
    e.addDecision(
      "Classifier correction",
      `User labelled flow ${id} as ${cls}. Correction queued for review.`,
    );
    return f;
  }
  if (p === "/intents") {
    if (method === "POST") {
      if (e.snapshot.mode === "safe_state")
        throw new MockError("SAFE_STATE", "Restore adaptive mode before creating a new boost.");
      const input = IntentInputSchema.parse(body);
      if (!e.snapshot.devices.some((d) => d.id === input.target.deviceId))
        throw new MockError("NOT_FOUND", "Choose a known device.");
      const intent = {
        ...input,
        id: `int-${e.intents.length + 1}`,
        expiresAt: new Date(Date.parse(e.snapshot.ts) + input.durationSeconds * 1000).toISOString(),
        status:
          input.priority === "high" && e.intents.filter((i) => i.status === "active").length > 2
            ? ("infeasible" as const)
            : ("active" as const),
      };
      e.intents.unshift(intent);
      e.addDecision(
        "Temporary boost created",
        `${input.goal}. Auto-reverts at ${intent.expiresAt}. Service floor locked at ${e.settings.floorMbps} Mbps.`,
      );
      e.emit("intent", intent);
      e.persist();
      return intent;
    }
    if (method === "DELETE") {
      const id = url.searchParams.get("id") ?? b?.id;
      const intent = e.intents.find((i) => i.id === id);
      if (!intent) throw new MockError("NOT_FOUND", "Intent not found.");
      intent.status = "cancelled";
      e.addDecision("Intent cancelled", "Restored the normal allocation at your request.");
      e.persist();
      return intent;
    }
    return e.intents;
  }
  if (p === "/decisions") return e.decisions;
  if (p === "/decisions/verify")
    return {
      valid: e.verifyDecisions(),
      count: e.decisions.length,
    };
  if (p === "/conflicts") return e.conflicts;
  if (/^\/conflicts\/[^/]+\/(accept|adjust|cancel)$/.test(p)) {
    const c = e.conflicts.find((c) => c.id === p.split("/")[2]);
    if (!c) throw new MockError("NOT_FOUND", "Conflict not found.");
    c.status = p.endsWith("cancel") ? "cancelled" : "resolved";
    e.addDecision(
      "Intent conflict resolved",
      `Applied ${String(b?.option ?? "balanced allocation")}. Minimum service remains guaranteed.`,
    );
    e.persist();
    return c;
  }
  if (p === "/health/diagnosis") return diagnoses;
  if (p === "/demand/schedules") {
    if (method === "POST") {
      const v = z
        .object({
          name: z.string().min(1),
          device: z.string(),
          time: z.string(),
          duration: z.string(),
        })
        .parse(body);
      e.schedules.push({ ...v, id: `s${e.schedules.length + 1}`, confidence: 0.8, enabled: true });
    }
    if (method === "DELETE")
      e.schedules = e.schedules.filter((s) => s.id !== url.searchParams.get("id"));
    e.persist();
    return e.schedules;
  }
  if (p === "/demand/forecast") return e.snapshot.link.forecast;
  if (p === "/links") return links;
  if (p === "/paths/evaluate")
    return {
      recommended: "wan1",
      reason: "Primary link has lower latency. Failover may reset active sessions.",
      emulated: true,
    };
  if (p.startsWith("/scenarios/")) {
    const name = p.split("/")[2];
    if (name !== "stop" && !scenarios.some((s) => s.id === name))
      throw new MockError("NOT_FOUND", "Scenario unavailable.");
    e.startScenario(name === "stop" ? null : name);
    e.addDecision(
      name === "stop" ? "Scenario stopped" : "Scenario started",
      name === "stop"
        ? "Restored normal capacity."
        : `Emulating ${scenarios.find((s) => s.id === name)?.name}. No real network traffic is changed.`,
    );
    e.emit("scenario", { name });
    e.emit("snapshot", e.snapshot);
    e.persist();
    return e.snapshot;
  }
  if (p === "/experiments" && method === "POST") {
    const input = z
      .object({
        scenario: z.string(),
        seed: z.number().int(),
        trials: z.number().int().min(1).max(100),
      })
      .parse(body);
    const result: Experiment = {
      ...input,
      id: `exp-${e.experiments.length + 1}`,
      progress: 0,
      status: "running",
      results: experimentResults(input.seed),
    };
    e.experiments.unshift(result);
    e.persist();
    return result;
  }
  if (p === "/experiments") return e.experiments;
  if (p.startsWith("/experiments/")) {
    const exp = e.experiments.find((x) => x.id === p.split("/")[2]);
    if (!exp) throw new MockError("NOT_FOUND", "Experiment not found.");
    return exp;
  }
  if (p === "/settings") {
    if (method === "PUT") {
      const settings = SettingsSchema.parse(body);
      if (settings.floorMbps * e.snapshot.devices.filter(d => d.flows.length).length > e.snapshot.link.capacityMbps)
        throw new MockError("INFEASIBLE", "This floor exceeds the available capacity. Choose a smaller floor.");
      e.settings = settings;
      e.snapshot.agent.intervalSeconds = settings.intervalSeconds;
      e.addDecision(
        "Settings updated",
        "Saved control thresholds. Minimum service floor remains enabled.",
      );
      e.persist();
    }
    return e.settings;
  }
  if (p === "/safe-state/reset") {
    const restore = b?.restore === true;
    e.snapshot.mode = restore ? "adaptive" : "safe_state";
    if (!restore)
      e.intents.forEach((i) => {
        if (i.status === "active") i.status = "cancelled";
      });
    e.snapshot.alerts = restore
      ? []
      : [
          {
            id: "safe",
            severity: "info",
            message:
              "Safe state is active: boosts paused, conservative CAKE allocation, floors preserved.",
          },
        ];
    e.addDecision(
      restore ? "Adaptive mode restored" : "Entered safe state",
      restore
        ? "Adaptive control has resumed."
        : "Cancelled temporary intents and restored a conservative shaping policy.",
    );
    e.emit("mode", e.snapshot.mode);
    e.emit("snapshot", e.snapshot);
    e.persist();
    return e.snapshot;
  }
  if (p === "/ml/metrics") return e.ml;
  if (p === "/ml/corrections") {
    if (method === "POST") {
      const { id, status } = z
        .object({ id: z.string(), status: z.enum(["approved", "rejected"]) })
        .parse(body);
      const c = e.ml.corrections.find((c) => c.id === id);
      if (c) c.status = status;
      e.persist();
    }
    return e.ml.corrections;
  }
  if (p === "/ml/retrain") {
    e.ml.accuracy = 0.972;
    e.ml.f1 = 0.963;
    e.ml.models.forEach((m) => {
      if (m.status === "active") m.status = "previous";
    });
    e.ml.models.unshift({
      version: `v0.9.${4 + e.ml.models.length}`,
      status: "active",
      accuracy: 0.972,
      date: "Just now",
    });
    e.record("Model retrained", "Emulated training run; no data leaves this browser.");
    return e.ml;
  }
  if (p === "/ml/rollback") {
    const models = e.ml.models;
    const current = models.find((m) => m.status === "active");
    const previous = models.find((m) => m.status === "previous");
    if (current && previous) {
      current.status = "previous";
      previous.status = "active";
      e.ml.accuracy = previous.accuracy;
    }
    e.record("Model rolled back", "Previous model restored.");
    return e.ml;
  }
  if (p === "/qoe/formulas") return formulas;
  if (p === "/ask") {
    z.object({ question: z.string().min(1).max(1000) }).parse(body);
    return { ...answers[0], citations: e.decisions.slice(0, 2).map((d) => d.id) };
  }
  if (p === "/agent/info")
    return {
      hostname: "ubuntu-home",
      os: "Ubuntu 24.04 LTS",
      version: e.snapshot.agent.version,
      uptime: "3 days, 14 hours",
      dbSize: "18.4 MB",
      relay: "Connected · emulated",
      loopMs: 14,
      seed: e.snapshot.agent.seed,
      emulated: true,
    };
  if (p === "/agent/logs") return logs;
  if (p === "/sessions") {
    if (method === "DELETE") {
      e.sessions = e.sessions.filter((s) => s.id !== url.searchParams.get("id"));
      e.record("Session revoked", "Browser session access removed.");
    }
    return e.sessions;
  }
  if (p === "/keys/rotate") {
    e.record("Pairing key rotated", "Previous demo key invalidated.");
    return { key: "EB-NEWW-KEYS-4242", emulated: true };
  }
  if (p === "/audit") return e.audit;
  throw new MockError("NOT_IMPLEMENTED", `This agent does not expose ${method} ${p}.`);
}
