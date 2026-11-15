import { useState } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import {
  Zap,
  LockKeyhole,
  ArrowRight,
  Clock3,
  Check,
  ShieldCheck,
  RotateCcw,
  Plus,
  AlertCircle,
} from "lucide-react";
import { useConsole } from "../../app/Layout";
import { useApi } from "../../lib/api";
import {
  SectionHeading,
  CardHeader,
  Button,
  Badge,
  EmptyState,
  LockNote,
  useTask,
  DeviceIcon,
  ProgressBar,
  ErrorState,
} from "../../components/ui";
import { remainingSeconds } from "../../lib/qoe";
import type { Intent as IntentType, IntentInput } from "../../types";
export default function Intent() {
  const { snapshot, settings, capabilities } = useConsole();
  const { data: intents = [], error } = useApi<IntentType[]>("/intents", { refetchInterval: 3000 });
  const [device, setDevice] = useState(snapshot.devices[0].id);
  const [cls, setCls] = useState("call");
  const [goal, setGoal] = useState("Prioritize my work call");
  const [priority, setPriority] = useState("high");
  const [duration, setDuration] = useState("3600");
  const [deadline, setDeadline] = useState("");
  const [tradeoffs, setTradeoffs] = useState(["Slow bulk downloads"]);
  const task = useTask("Intent saved. The agent will automatically revert it.");
  const cancel = useTask("Boost cancelled. Normal allocation restored.");
  const active = intents.filter((i) => i.status === "active" || i.status === "infeasible");
  const previous = intents.filter((i) => i.status !== "active" && i.status !== "infeasible");
  const target = snapshot.devices.find((d) => d.id === device)!;
  function submit(e: React.FormEvent) {
    e.preventDefault();
    task.mutate({
      path: "/intents",
      body: {
        target: { deviceId: device, cls },
        goal,
        priority,
        durationSeconds: Number(duration),
        deadline: deadline || null,
        allowedTradeoffs: tradeoffs,
        minServiceFloor: true,
      },
    });
  }
  return (
    <>
      <SectionHeading
        title="A little priority. A better moment."
        description="Tell your network what matters now. We’ll make room, then return to normal."
      >
        <LockNote floor={settings?.floorMbps ?? 2} />
      </SectionHeading>
      <div className="intent-layout">
        <section className="card intent-form-card">
          <CardHeader
            title="What would you like to prioritize?"
            subtitle="Temporary by default. Fairness, always."
          />
          <form className="standard-form" onSubmit={submit}>
            <div className="form-row">
              <label className="field-label">
                Device
                <select value={device} onChange={(e) => setDevice(e.target.value)}>
                  {snapshot.devices.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                Traffic class
                <select value={cls} onChange={(e) => setCls(e.target.value)}>
                  {capabilities?.classes
                    .filter((c) => c !== "unknown")
                    .map((c) => (
                      <option key={c} value={c}>
                        {c.charAt(0).toUpperCase() + c.slice(1)}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            <label className="field-label">
              Your goal
              <input
                required
                minLength={3}
                maxLength={140}
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                placeholder="e.g. Keep my work call clear"
              />
            </label>
            <div className="form-row">
              <label className="field-label">
                Priority
                <select value={priority} onChange={(e) => setPriority(e.target.value)}>
                  <option value="low">Low · use spare headroom</option>
                  <option value="normal">Normal · balanced boost</option>
                  <option value="high">High · protect this experience</option>
                </select>
              </label>
              <label className="field-label">
                Duration
                <select value={duration} onChange={(e) => setDuration(e.target.value)}>
                  <option value="900">15 minutes</option>
                  <option value="1800">30 minutes</option>
                  <option value="3600">1 hour</option>
                  <option value="7200">2 hours</option>
                </select>
              </label>
            </div>
            <label className="field-label">
              Optional deadline
              <input
                type="datetime-local"
                value={deadline}
                onChange={(e) => setDeadline(e.target.value)}
              />
            </label>
            <fieldset className="tradeoffs-fieldset">
              <legend>Allowed trade-offs</legend>
              {["Slow bulk downloads", "Reduce stream resolution"].map((option) => (
                <label key={option} className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={tradeoffs.includes(option)}
                    onChange={(e) =>
                      setTradeoffs((v) =>
                        e.target.checked ? [...v, option] : v.filter((x) => x !== option),
                      )
                    }
                  />
                  {option}
                </label>
              ))}
            </fieldset>
            <div className="floor-lock">
              <LockKeyhole size={16} />
              <div>
                <strong>Minimum service floor is locked on</strong>
                <span>At least {settings?.floorMbps ?? 2} Mbps for every active device.</span>
              </div>
              <Badge tone="blue">Protected</Badge>
            </div>
            <Button type="submit" pending={task.isPending} className="full-width">
              <Zap size={14} /> Create temporary boost <ArrowRight size={14} />
            </Button>
          </form>
        </section>
        <div className="intent-side">
          <section className="card impact-preview">
            <CardHeader
              title="A preview, not a promise"
              subtitle="Illustrative allocation with your current intent."
            />
            <div className="preview-target">
              <span>
                <DeviceIcon device={target} size={25} />
              </span>
              <div>
                <strong>{target.name}</strong>
                <p>
                  {cls} · {priority} priority
                </p>
              </div>
              <Zap size={18} />
            </div>
            <div className="allocation-bars">
              {snapshot.devices
                .filter((d) => d.flows.length || d.id === device)
                .map((d) => (
                  <div key={d.id}>
                    <div>
                      <span>{d.name}</span>
                      <strong>
                        {d.id === device
                          ? priority === "high"
                            ? "42%"
                            : priority === "normal"
                              ? "32%"
                              : "25%"
                          : "~19%"}
                      </strong>
                    </div>
                    <ProgressBar
                      value={
                        d.id === device
                          ? priority === "high"
                            ? 0.84
                            : priority === "normal"
                              ? 0.64
                              : 0.5
                          : 0.38
                      }
                      className={d.id === device ? "target-allocation" : ""}
                    />
                  </div>
                ))}
            </div>
            <p className="preview-disclaimer">
              <ShieldCheck size={13} /> Every device retains its guaranteed floor. The verifier can
              roll back an allocation that makes things worse.
            </p>
          </section>
          <div className="intent-tip">
            <Clock3 size={20} />
            <h3>Set it. Then forget it.</h3>
            <p>
              Countdowns use the agent’s clock and survive refresh. At expiry, the original policy
              is restored and a decision is recorded.
            </p>
          </div>
        </div>
      </div>
      <section className="card active-intents">
        <CardHeader
          title="Your temporary priorities"
          subtitle={`${active.length} active · Auto-revert is always on`}
        />
        {error && <ErrorState message={error.message} />}{" "}
        {!active.length ? (
          <EmptyState
            title="A balanced connection, for now."
            description="Create a temporary boost above when a moment needs a little extra room."
          />
        ) : (
          <div className="intent-list">
            {active.map((i) => (
              <article
                key={i.id}
                className={`intent-item ${i.status === "infeasible" ? "intent-infeasible" : ""}`}
              >
                <CountdownRing intent={i} now={snapshot.ts} />
                <div>
                  <h3>{i.goal}</h3>
                  <p>
                    {snapshot.devices.find((d) => d.id === i.target.deviceId)?.name} ·{" "}
                    {i.target.cls} · {i.priority} priority
                  </p>
                  {i.status === "infeasible" && (
                    <p className="inline-error">
                      Cannot satisfy all active priorities at this capacity. Cancel a boost or
                      review conflict alternatives.
                    </p>
                  )}
                </div>
                <Badge tone={i.status === "infeasible" ? "purple" : "blue"}>
                  {i.status === "infeasible" ? "Needs attention" : "Active"}
                </Badge>
                <Button
                  variant="secondary"
                  pending={cancel.isPending}
                  onClick={() => cancel.mutate({ path: `/intents?id=${i.id}`, method: "DELETE" })}
                >
                  Cancel boost
                </Button>
              </article>
            ))}
          </div>
        )}
        {previous.length > 0 && (
          <div className="past-intents">
            {previous.slice(0, 4).map((i) => (
              <div key={i.id}>
                <RotateCcw size={13} />
                <span>{i.goal}</span>
                <Badge>{i.status === "expired" ? "Reverted" : i.status}</Badge>
                <Link className="text-link" to="/app/decisions">
                  See decision <ArrowRight size={11} />
                </Link>
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
export function CountdownRing({ intent, now }: { intent: IntentType; now: string }) {
  const remaining = remainingSeconds(intent.expiresAt, now);
  const progress = remaining / intent.durationSeconds;
  const minutes = Math.floor(remaining / 60);
  return (
    <div className="countdown-ring" title={`${remaining} seconds remaining · agent clock`}>
      <svg viewBox="0 0 58 58" aria-hidden="true">
        <circle cx="29" cy="29" r="25" fill="none" stroke="#eaf0fb" strokeWidth="3" />
        <motion.circle
          cx="29"
          cy="29"
          r="25"
          fill="none"
          stroke="#8ca9ec"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray="157.08"
          animate={{ strokeDashoffset: 157.08 * (1 - progress) }}
          transform="rotate(-90 29 29)"
        />
      </svg>
      <span>
        {minutes}
        <small>min left</small>
      </span>
    </div>
  );
}
