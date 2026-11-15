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
        description="Put adaptive control to the test. Every scenario is safe, reversible, and entirely emulated."
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
                  <span>Unmanaged RTT</span>
                  <strong>
                    {s.baseline}
                    <small>ms</small>
                  </strong>
                </div>
                <ArrowRight size={15} />
                <div>
                  <span>EB call RTT</span>
                  <strong>
                    {s.managed}
                    <small>ms</small>
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
          title={snapshot.scenario ? "The agent is making room." : "Ready when you are."}
          subtitle={
            snapshot.scenario
              ? "Watch call latency stay steady as the unmanaged baseline rises."
              : "Start a scenario above to see congestion and recovery."
          }
        >
          <Badge tone={snapshot.scenario ? "blue" : "neutral"}>
            <Activity size={11} />
            {snapshot.scenario ? "Live emulation" : "Standing by"}
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
            Policy responds within 2–5 seconds in the demo. The WAN-drop scenario includes a
            verifier rollback. All outcomes are synthetic illustrations.
          </p>
          <Link to="/app/decisions" className="text-link">
            See the decisions <ArrowRight size={13} />
          </Link>
        </div>
      </section>
      <div className="scenario-bottom-note">
        <FlaskConical size={19} />
        <p>
          <strong>Curiosity without consequences.</strong> Scenarios change only the in-browser
          agent state. Stop at any time to restore normal capacity.
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
