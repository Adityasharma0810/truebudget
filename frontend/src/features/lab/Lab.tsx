import { useState } from "react";
import { Link } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  Download,
  Signal,
  Tv,
  Play,
  Square,
  ArrowRight,
  Zap,
  FlaskConical,
  ShieldCheck,
  Activity,
  CircleHelp,
} from "lucide-react";
import { useConsole } from "../../app/Layout";
import { useApi } from "../../lib/api";
import {
  SectionHeading,
  CardHeader,
  EmulatedBadge,
  Button,
  Badge,
  useTask,
  ProgressBar,
} from "../../components/ui";
import { CapacityChart } from "../../components/Chart";
import type { MetricPoint } from "../../types";
import { useEffectiveTier } from "../../motion/store";
export default function Lab() {
  const { snapshot, capabilities } = useConsole();
  const task = useTask("Scenario state updated. No real network traffic is affected.");
  const [shock, setShock] = useState(false);
  const tier = useEffectiveTier();
  const { data: points = [] } = useApi<MetricPoint[]>("/metrics?range=15m", {
    refetchInterval: 3000,
  });
  return (
    <>
      <SectionHeading
        title="What if your network had a rough day?"
        description="Put adaptive control to the test. Scenario targets are synthetic illustrations; the chart below stays live and monitor-only, and running a scenario changes no network traffic."
      >
        <EmulatedBadge />
        {snapshot.scenario && (
          <Button
            variant="secondary"
            pending={task.isPending}
            onClick={() => task.mutate({ path: "/scenarios/stop" })}
          >
            <Square size={12} /> Stop & restore
          </Button>
        )}
      </SectionHeading>
      <div className="scenario-grid">
        {capabilities?.scenarios.map((s, i) => {
          const Icon = s.icon === "download" ? Download : s.icon === "signal" ? Signal : Tv;
          const running = snapshot.scenario === s.id;
          return (
            <motion.article
              className={`card scenario-card ${running ? "scenario-running" : ""}`}
              key={s.id}
              whileHover={
                tier === "Full"
                  ? { y: -5, rotateX: 2, rotateY: -2 }
                  : tier === "Balanced"
                    ? { y: -3 }
                    : undefined
              }
            >
              <div className="row-between">
                <span className={`scenario-illustration scenario-illustration-${i}`}>
                  <Icon size={30} />
                  <span className="scenario-orbit" />
                </span>
                <Badge tone={running ? "blue" : "neutral"}>
                  {running ? "Running now" : s.duration}
                </Badge>
              </div>
              <div className="eyebrow">{s.tag}</div>
              <h2>{s.name}</h2>
              <p>{s.description}</p>
              <div className="scenario-comparison">
                <div>
                  <span>Unmanaged QoE</span>
                  <strong>
                    {s.baseline.toFixed(2)}
                    <small>proxy</small>
                  </strong>
                </div>
                <ArrowRight size={15} />
                <div>
                  <span>EB call QoE</span>
                  <strong>
                    {s.managed.toFixed(2)}
                    <small>proxy</small>
                  </strong>
                </div>
              </div>
              <Button
                variant={running ? "secondary" : "primary"}
                className="full-width"
                pending={task.isPending}
                onClick={() =>
                  task.mutate(
                    { path: `/scenarios/${running ? "stop" : s.id}` },
                    {
                      onSuccess: () => {
                        if (!running) {
                          setShock(true);
                          setTimeout(() => setShock(false), 1200);
                        }
                      },
                    },
                  )
                }
              >
                {running ? <Square size={13} /> : <Play size={13} />}{" "}
                {running ? "Stop & restore" : "Run scenario"}
              </Button>
            </motion.article>
          );
        })}
      </div>
      <section className="card scenario-monitor">
        <CardHeader
          title={snapshot.scenario ? "Scenario state recorded." : "Ready when you are."}
          subtitle={
            snapshot.scenario
              ? "The agent stored this scenario. The chart below remains live measurement data; no traffic or qdisc is changed."
              : "Start a scenario above to record it. The chart below shows live measurements."
          }
        >
          <Badge tone={snapshot.scenario ? "blue" : "neutral"}>
            <Activity size={11} />
            {snapshot.scenario ? "State only" : "Standing by"}
          </Badge>
        </CardHeader>
        <div className="race-chart-legend">
          <span>
            <i /> Experience Budget
          </span>
          <span>
            <i /> Unmanaged baseline
          </span>
          <span>RTT · milliseconds</span>
        </div>
        <CapacityChart points={points} metric="latency" baseline />
        <div className="scenario-monitor-footer">
          <ShieldCheck size={15} />
          <p>
            Scenario targets above are synthetic illustrations. Selecting one records a labelled
            state only; the latency chart is live measured data from this agent.
          </p>
          <Link to="/app/experiments" className="text-link">
            Run a seeded A/B experiment <ArrowRight size={13} />
          </Link>
        </div>
      </section>
      <div className="scenario-bottom-note">
        <FlaskConical size={19} />
        <p>
          <strong>Curiosity without consequences.</strong> Scenarios record state only: no qdisc is
          changed, no traffic is simulated, and metrics below stay live.
        </p>
        <Link to="/app/experiments" className="text-link">
          Compare with an A/B run <ArrowRight size={13} />
        </Link>
      </div>
      <AnimatePresence>
        {shock && tier !== "Reduced" && tier !== "Off" && (
          <motion.div
            className="shockwave"
            initial={{ opacity: 0.6, scale: 0.1 }}
            animate={{ opacity: 0, scale: 2.5 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 1.2 }}
          />
        )}
      </AnimatePresence>
    </>
  );
}
