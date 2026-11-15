import { useState } from "react";
import {
  Play,
  Download,
  FlaskConical,
  ArrowRight,
  Check,
  ChartNoAxesCombined,
  ShieldCheck,
  Info,
  Printer,
} from "lucide-react";
import { useConsole } from "../../app/Layout";
import { useApi, request } from "../../lib/api";
import {
  SectionHeading,
  CardHeader,
  EmulatedBadge,
  Button,
  Badge,
  ProgressBar,
  useTask,
  EmptyState,
} from "../../components/ui";
import { CapacityChart } from "../../components/Chart";
import type { Experiment, MetricPoint } from "../../types";
import { download, csv } from "../../lib/format";
export default function Experiments() {
  const { capabilities } = useConsole();
  const [scenario, setScenario] = useState(capabilities?.scenarios[0]?.id ?? "");
  const [seed, setSeed] = useState(capabilities?.seed ?? 4242);
  const [trials, setTrials] = useState(20);
  const { data: experiments = [] } = useApi<Experiment[]>("/experiments", {
    refetchInterval: 1000,
  });
  const task = useTask<Experiment>(
    "A/B run started. All three controllers use the same seeded trace.",
  );
  const reportTask = useTask<Experiment>("");
  const [format, setFormat] = useState("html");
  const selected = experiments[0];
  const running = selected?.status === "running";
  const { data: points = [] } = useApi<MetricPoint[]>(
    `/metrics?range=15m&experiment=${selected?.id ?? ""}`,
    { refetchInterval: running ? 3000 : false },
  );
  async function report() {
    if (!selected) return;
    reportTask.mutate(
      { path: `/experiments/${selected.id}/report`, method: "GET" },
      {
        onSuccess: (exp) => {
          if (format === "json") download(`experience-budget-${exp.id}.json`, exp);
          else if (format === "csv")
            download(
              `experience-budget-${exp.id}.csv`,
              csv([
                ["Metric", "Unit", "Unmanaged", "Strong CAKE", "Experience Budget", "95% CI ±"],
                ...exp.results.map((r) => [r.metric, r.unit, r.unmanaged, r.cake, r.eb, r.ci]),
              ]),
              "csv",
            );
          else {
            const rows = exp.results
              .map(
                (r) =>
                  `<tr><td>${r.metric}</td><td>${r.unmanaged.toFixed(2)}</td><td>${r.cake.toFixed(2)}</td><td>${r.eb.toFixed(2)} ± ${r.ci} ${r.unit}</td></tr>`,
              )
              .join("");
            download(
              `experience-budget-${exp.id}.html`,
              `<!doctype html><html lang="en"><meta charset="utf-8"><title>Experience Budget · Emulated report</title><style>body{font:16px system-ui;max-width:900px;margin:60px auto;color:#26354d}table{border-collapse:collapse;width:100%}td,th{padding:16px;text-align:left;border-bottom:1px solid #dee6f3}h1{color:#4778f5}</style><h1>Experience Budget</h1><h2>EMULATED A/B report</h2><p>Seed ${exp.seed} · ${exp.trials} trials · All results and confidence intervals are synthetic, not measurements.</p><table><thead><tr><th>Metric</th><th>Unmanaged</th><th>Strong CAKE</th><th>Experience Budget</th></tr></thead><tbody>${rows}</tbody></table><p>QoE is a formula-based proxy. Real-agent validation is still required.</p></html>`,
              "html",
            );
          }
        },
      },
    );
  }
  return (
    <>
      <SectionHeading
        title="Don’t take our word for it."
        description="Same scenario. Same seed. Three controllers. See the trade-offs side by side."
      >
        <EmulatedBadge />
      </SectionHeading>
      <section className="card experiment-setup">
        <CardHeader
          title="Design a reproducible experiment"
          subtitle="Unmanaged vs strong CAKE baseline vs Experience Budget"
        />
        <form
          onSubmit={(e) => {
            e.preventDefault();
            task.mutate({ path: "/experiments", body: { scenario, seed, trials } });
          }}
        >
          <label className="field-label">
            Scenario
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {capabilities?.scenarios.map((s) => (
                <option value={s.id} key={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            Random seed
            <input
              type="number"
              aria-label="Random seed"
              value={seed}
              required
              onChange={(e) => setSeed(Number(e.target.value))}
            />
          </label>
          <label className="field-label">
            Trials
            <input
              type="number"
              min={1}
              max={100}
              value={trials}
              required
              onChange={(e) => setTrials(Number(e.target.value))}
            />
          </label>
          <Button type="submit" pending={task.isPending || running}>
            <Play size={13} />
            {running ? "Experiment running…" : "Run experiment"}
          </Button>
        </form>
      </section>
      {selected ? (
        <>
          <section className="card race-card">
            <CardHeader
              title={running ? "A fair race, in progress." : "The results are in."}
              subtitle={`Seed ${selected.seed} · ${selected.trials} paired trials · Emulated capacity trace`}
            >
              <Badge tone="blue">
                {running
                  ? `${Math.floor((selected.progress / 100) * selected.trials)} / ${selected.trials} trials`
                  : "Completed"}
              </Badge>
            </CardHeader>
            <div className="race-progress">
              <ProgressBar value={selected.progress / 100} />
            </div>
            <div className="race-chart-legend">
              <span>
                <i /> Experience Budget
              </span>
              <span>
                <i /> Unmanaged baseline
              </span>
              <span>Call RTT · ms</span>
            </div>
            <CapacityChart points={points} metric="latency" baseline />
            <div className="race-lanes">
              <div>
                <span>Unmanaged</span>
                <div>
                  <i
                    style={{ transform: `translateX(${Math.min(selected.progress * 0.68, 66)}%)` }}
                  >
                    <ActivityGlyph />
                  </i>
                </div>
                <strong>Latency spikes</strong>
              </div>
              <div>
                <span>Experience Budget</span>
                <div>
                  <i style={{ transform: `translateX(${selected.progress}%)` }}>
                    <ShieldCheck size={16} />
                  </i>
                </div>
                <strong>Steady calls</strong>
              </div>
            </div>
          </section>
          {!running && (
            <section className="card results-card">
              <CardHeader
                title="The whole picture, not just a headline"
                subtitle="Values shown with illustrative 95% confidence intervals."
              >
                <div className="report-actions">
                  <select
                    aria-label="Report format"
                    value={format}
                    onChange={(e) => setFormat(e.target.value)}
                  >
                    <option value="html">HTML report</option>
                    <option value="csv">CSV data</option>
                    <option value="json">JSON data</option>
                  </select>
                  <Button pending={reportTask.isPending} onClick={report}>
                    <Download size={13} /> Download report
                  </Button>
                </div>
              </CardHeader>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Metric</th>
                      <th>Unmanaged</th>
                      <th>Strong CAKE</th>
                      <th>Experience Budget</th>
                      <th>95% CI ±</th>
                    </tr>
                  </thead>
                  <tbody>
                    {selected.results.map((r) => (
                      <tr key={r.metric}>
                        <td>
                          <strong>{r.metric}</strong>
                          <span className="muted"> · {r.unit}</span>
                        </td>
                        <td className="mono">{r.unmanaged.toFixed(r.unit === "proxy" ? 2 : 1)}</td>
                        <td className="mono">{r.cake.toFixed(r.unit === "proxy" ? 2 : 1)}</td>
                        <td className="mono result-eb">
                          {r.eb.toFixed(r.unit === "proxy" ? 2 : 1)}
                        </td>
                        <td>
                          <span className="ci-whisker" />
                          {r.ci}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="report-disclaimer">
                <Info size={14} /> Emulated results are for interface demonstration, not evidence of
                real-world superiority. CPU and bulk progress make the trade-offs visible.
              </div>
            </section>
          )}
        </>
      ) : (
        <section className="card">
          <EmptyState
            title="A good comparison starts with a fair test."
            description="Choose a scenario and run the same trace through three controllers. Your seed makes every result reproducible."
          />
        </section>
      )}
      <div className="page-note">
        <FlaskConical size={13} /> Strong CAKE is a meaningful baseline, not a deliberately weakened
        comparison. Real network trials are not included in this demo.
      </div>
    </>
  );
}
function ActivityGlyph() {
  return <ChartNoAxesCombined size={16} />;
}
