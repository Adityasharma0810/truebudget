import seedrandom from "seedrandom";
import type { MetricPoint, Experiment } from "../../types";
export function generateMetrics(
  seed: number,
  now: string,
  range = "1h",
  scenario: string | null = null,
): MetricPoint[] {
  const rng = seedrandom(String(seed));
  const total =
    range === "30d"
      ? 30 * 86400000
      : range === "24h"
        ? 86400000
        : range === "15m"
          ? 900000
          : 3600000;
  const count = range === "30d" ? 180 : 72;
  return Array.from({ length: count }, (_, i) => {
    const ts = new Date(Date.parse(now) - total + (i / (count - 1)) * total).toISOString();
    const evening = Math.max(0, Math.sin(((new Date(ts).getHours() - 14) / 10) * Math.PI));
    const capacity =
      scenario === "wan-drop" && i > count * 0.6 ? 20 + rng() * 2 : 99 - evening * 5 + rng() * 7;
    const throughput = Math.min(
      capacity * 0.9,
      45 + Math.sin(i * 0.18) * 9 + rng() * 9 + (i > 20 && i < 45 ? 14 : 0),
    );
    return {
      ts,
      capacity,
      lo: capacity - 5,
      hi: capacity + 5,
      throughput,
      latency: 17 + rng() * 6,
      jitter: 1.5 + rng() * 2,
      loss: rng() * 0.07,
      queue: 4 + Math.round(rng() * 8),
      fairness: 0.94 + rng() * 0.04,
      baseline: scenario && i > count * 0.4 ? 90 + rng() * 130 : 22 + rng() * 15,
    };
  });
}
export function experimentResults(seed: number): Experiment["results"] {
  const r = seedrandom(String(seed));
  return [
    {
      metric: "Call p95 RTT",
      unit: "ms",
      unmanaged: 186 + r() * 10,
      cake: 42 + r() * 5,
      eb: 21 + r() * 3,
      ci: 2.1,
    },
    {
      metric: "Jitter p95",
      unit: "ms",
      unmanaged: 38 + r() * 4,
      cake: 8 + r(),
      eb: 2.8 + r(),
      ci: 0.6,
    },
    { metric: "Bulk progress", unit: "%", unmanaged: 98, cake: 88, eb: 91, ci: 1.6 },
    { metric: "Worst-device QoE", unit: "proxy", unmanaged: 0.43, cake: 0.78, eb: 0.92, ci: 0.02 },
    { metric: "Agent CPU", unit: "%", unmanaged: 0, cake: 2.1, eb: 4.8, ci: 0.4 },
  ];
}
