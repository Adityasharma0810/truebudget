import { useState } from "react";
import { Download, Pause, Play, Table2, Activity, ArrowUpRight } from "lucide-react";
import { useApi } from "../../lib/api";
import { useConsole } from "../../app/Layout";
import { CapacityChart } from "../../components/Chart";
import {
  SectionHeading,
  CardHeader,
  Button,
  InfoTip,
  Badge,
  ErrorState,
  Skeletons,
} from "../../components/ui";
import { download, csv, dateTime } from "../../lib/format";
import type { MetricPoint } from "../../types";
export default function Metrics() {
  const { snapshot } = useConsole();
  const [range, setRange] = useState("1h");
  const [device, setDevice] = useState("all");
  const [paused, setPaused] = useState(false);
  const [frozen, setFrozen] = useState<MetricPoint[]>([]);
  const [table, setTable] = useState(false);
  const query = useApi<MetricPoint[]>(`/metrics?range=${range}&device=${device}`, {
    refetchInterval: paused ? false : 3000,
  });
  const points = paused ? frozen : (query.data ?? []);
  const latest = points[points.length - 1];
  const metrics = [
    {
      key: "latency",
      name: "Round-trip latency",
      unit: "ms",
      desc: "Call and game responsiveness",
    },
    { key: "jitter", name: "Jitter", unit: "ms", desc: "Variation in packet timing" },
    { key: "loss", name: "Packet loss", unit: "%", desc: "Packets that did not arrive" },
    {
      key: "throughput",
      name: "Throughput & capacity",
      unit: "Mbps",
      desc: "Used bandwidth and uncertainty",
    },
    { key: "queue", name: "Queue depth", unit: "pkts", desc: "Packets waiting to be sent" },
    {
      key: "fairness",
      name: "Jain fairness index",
      unit: "",
      desc: "Experience balance across active devices",
    },
  ] as const;
  return (
    <>
      <SectionHeading
        title="A clearer picture, in real time."
        description="Live measurements from the kernel and network probes. No invented intermediate samples."
      >
        <Button
          variant="secondary"
          onClick={() =>
            download(
              "experience-metrics.csv",
              csv([
                [
                  "timestamp",
                  "capacity_mbps",
                  "throughput_mbps",
                  "latency_ms",
                  "jitter_ms",
                  "loss_pct",
                  "queue",
                  "fairness",
                ],
                ...points.map((p) => [
                  p.ts,
                  p.capacity,
                  p.throughput,
                  p.latency,
                  p.jitter,
                  p.loss,
                  p.queue,
                  p.fairness,
                ]),
              ]),
              "csv",
            )
          }
        >
          <Download size={14} /> Export CSV
        </Button>
      </SectionHeading>
      <div className="filter-toolbar">
        <div className="segmented-control">
          {[
            ["15m", "15 min"],
            ["1h", "1 hour"],
            ["24h", "24 hours"],
            ["30d", "30 days"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={range === id ? "active" : ""}
              onClick={() => {
                setRange(id);
                setPaused(false);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <select
          aria-label="Filter device"
          value={device}
          onChange={(e) => {
            setDevice(e.target.value);
            setPaused(false);
          }}
        >
          <option value="all">All devices</option>
          {snapshot.devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <div className="toolbar-spacer" />
        <Button
          variant="secondary"
          onClick={() => {
            if (!paused) setFrozen(query.data ?? []);
            setPaused(!paused);
          }}
        >
          {paused ? <Play size={13} /> : <Pause size={13} />}{" "}
          {paused ? "Resume live" : "Pause stream"}
        </Button>
        <Button variant="secondary" onClick={() => setTable(!table)}>
          <Table2 size={13} />
          {table ? "Show charts" : "Data table"}
        </Button>
      </div>
      {query.error && (
        <ErrorState message={query.error.message} retry={() => void query.refetch()} />
      )}{" "}
      {!query.data && !query.error ? (
        <Skeletons />
      ) : table ? (
        <div className="card table-scroll">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Capacity</th>
                <th>Throughput</th>
                <th>Latency</th>
                <th>Jitter</th>
                <th>Loss</th>
                <th>Fairness</th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.ts}>
                  <td>{dateTime(p.ts)}</td>
                  <td>{p.capacity.toFixed(1)} Mbps</td>
                  <td>{p.throughput.toFixed(1)} Mbps</td>
                  <td>{p.latency.toFixed(1)} ms</td>
                  <td>{p.jitter.toFixed(1)} ms</td>
                  <td>{p.loss.toFixed(3)}%</td>
                  <td>{p.fairness.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="metrics-grid">
          {metrics.map((m) => (
            <section className="card metric-chart-card" key={m.key}>
              <CardHeader title={m.name} subtitle={m.desc}>
                <Badge tone={paused ? "neutral" : "blue"}>{paused ? "Paused" : "Live"}</Badge>
              </CardHeader>
              <div className="metric-chart-value">
                {latest?.[m.key].toFixed(m.key === "fairness" ? 3 : 1) ?? "—"}
                <span>{m.unit}</span>
              </div>
              <CapacityChart
                points={points}
                metric={m.key}
                compact
                forecast={m.key === "throughput" ? snapshot.link.forecast : undefined}
              />
            </section>
          ))}
        </div>
      )}
    </>
  );
}
