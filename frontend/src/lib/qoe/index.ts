import type { QoEBand } from "../../types";
export const clamp = (x: number, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, x));
export const qoeBand = (qoe: number | null): QoEBand =>
  qoe === null ? "idle" : qoe >= 0.75 ? "green" : qoe >= 0.5 ? "amber" : "red";
export const f = (x: number, good: number, bad: number) => clamp((bad - x) / (bad - good));
export function callQoE(
  rtt: number,
  jitter: number,
  lossPct: number,
  codecDelay = 20,
  Ie = 0,
  Bpl = 25,
) {
  const d = rtt / 2 + 2 * jitter + codecDelay;
  const Id = 0.024 * d + 0.11 * Math.max(0, d - 177.3);
  const R = clamp(93.2 - Id - (Ie + ((95 - Ie) * lossPct) / (lossPct + Bpl)), 0, 100);
  const MOS = clamp(1 + 0.035 * R + 7e-6 * R * (R - 60) * (100 - R), 1, 4.5);
  return (MOS - 1) / 3.5;
}
export const gameQoE = (rtt: number, jitter: number, loss: number) =>
  0.55 * f(rtt, 30, 150) + 0.3 * f(jitter, 5, 40) + 0.15 * f(loss, 0, 2);
export const streamQoE = (throughput: number, target: number, cov: number, rebuffer: number) =>
  0.6 * clamp(throughput / target) + 0.25 * (1 - clamp(cov / 0.5)) + 0.15 * (1 - clamp(rebuffer));
export const bulkQoE = (rate: number, fairRate: number) =>
  fairRate > 0 ? clamp(rate / fairRate) : null;
export const fairness = (values: number[]) =>
  !values.length || values.every((x) => x === 0)
    ? 1
    : values.reduce((a, b) => a + b, 0) ** 2 /
      (values.length * values.reduce((a, b) => a + b * b, 0));
export const remainingSeconds = (expiresAt: string, now: string) =>
  Math.max(0, Math.ceil((Date.parse(expiresAt) - Date.parse(now)) / 1000));
