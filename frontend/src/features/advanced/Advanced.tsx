import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowRight,
  Check,
  X,
  ShieldCheck,
  Workflow,
  HeartPulse,
  CalendarClock,
  Plus,
  Trash2,
  Info,
  Signal,
  Wifi,
  Network,
  Search,
  Download,
  RotateCcw,
  Copy,
  Eye,
  EyeOff,
  LockKeyhole,
  Server,
  ExternalLink,
  CheckCircle2,
} from "lucide-react";
import { useApi } from "../../lib/api";
import { useConsole } from "../../app/Layout";
import {
  SectionHeading,
  CardHeader,
  Button,
  Badge,
  ExperimentalBadge,
  EmptyState,
  ErrorState,
  Skeletons,
  ProgressBar,
  Modal,
  useTask,
  useToasts,
} from "../../components/ui";
import { Toggle } from "../settings/Settings";
import { percent, dateTime, download } from "../../lib/format";
import type {
  Conflict,
  Diagnosis,
  Schedule,
  Link as WANLink,
  AgentInfo,
  Session,
  LogEntry,
  Settings,
  Snapshot,
} from "../../types";
export function CapabilityGate({ flag, children }: { flag: string; children: ReactNode }) {
  const { capabilities } = useConsole();
  return capabilities?.flags[flag] ? (
    <>{children}</>
  ) : (
    <div className="card">
      <EmptyState
        title="This capability isn’t available."
        description="The connected agent does not report support for this experimental feature."
      >
        <Link className="btn btn-secondary" to="/app">
          Back to overview <ArrowRight size={14} />
        </Link>
      </EmptyState>
    </div>
  );
}
export function Conflicts() {
  const query = useApi<Conflict[]>("/conflicts");
  const { snapshot } = useConsole();
  const [choice, setChoice] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const task = useTask("Allocation applied. Service floors remain protected.");
  const conflict = query.data?.find((c) => c.status === "pending");
  return (
    <CapabilityGate flag="conflicts">
      <SectionHeading
        title="A better compromise, together."
        description="When priorities compete, you get the final say. Nobody loses their service floor."
      >
        <ExperimentalBadge />
      </SectionHeading>
      {query.error ? (
        <ErrorState message={query.error.message} retry={() => void query.refetch()} />
      ) : !query.data ? (
        <Skeletons />
      ) : !conflict ? (
        <section className="card">
          <EmptyState
            title="Everyone’s priorities are in harmony."
            description="No unresolved intent conflicts. The agent will ask before making a material trade-off."
          />
        </section>
      ) : (
        <>
          <section className="card conflict-summary">
            <span className="conflict-summary-icon">
              <Workflow size={26} />
            </span>
            <div>
              <Badge tone="purple">Your input matters</Badge>
              <h2>{conflict.title}</h2>
              <p>{conflict.description}</p>
            </div>
          </section>
          <div className="conflict-options">
            {conflict.options.map((option, i) => (
              <motion.button
                layout
                className={`card conflict-option ${choice === i ? "option-selected" : ""}`}
                key={option.name}
                onClick={() => setChoice(i)}
              >
                <div className="row-between">
                  <span className="option-number">0{i + 1}</span>
                  <span className="option-radio">{choice === i && <Check size={11} />}</span>
                </div>
                <h3>{option.name}</h3>
                <p>{option.description}</p>
                <div className="conflict-allocation">
                  {option.allocations.map((v, j) => (
                    <div key={j}>
                      <div>
                        <span>{snapshot.devices[j]?.name ?? "Unknown device"}</span>
                        <strong>{v}%</strong>
                      </div>
                      <ProgressBar value={v / 60} />
                    </div>
                  ))}
                </div>
                {i === 0 && <Badge tone="blue">Preferred by the agent</Badge>}
              </motion.button>
            ))}
          </div>
          <div className="conflict-actions">
            <Button
              variant="secondary"
              pending={task.isPending}
              onClick={() => task.mutate({ path: `/conflicts/${conflict.id}/cancel` })}
            >
              <X size={13} /> Cancel negotiation
            </Button>
            <Button onClick={() => setConfirm(true)}>
              <Check size={13} />
              {choice === 0 ? "Accept preferred option" : "Apply alternative"}
              <ArrowRight size={13} />
            </Button>
          </div>
          <Modal open={confirm} onClose={() => setConfirm(false)} title="Confirm this trade-off">
            <p className="dialog-description">
              {conflict.options[choice].name}: {conflict.options[choice].description} The minimum
              service floor stays protected.
            </p>
            <div className="modal-footer">
              <Button variant="secondary" onClick={() => setConfirm(false)}>
                Go back
              </Button>
              <Button
                pending={task.isPending}
                onClick={() =>
                  task.mutate(
                    {
                      path: `/conflicts/${conflict.id}/${choice === 0 ? "accept" : "adjust"}`,
                      body: { option: choice },
                    },
                    { onSuccess: () => setConfirm(false) },
                  )
                }
              >
                Confirm allocation
              </Button>
            </div>
          </Modal>
        </>
      )}
    </CapabilityGate>
  );
}
export function Health() {
  const query = useApi<Diagnosis[]>("/health/diagnosis");
  return (
    <CapabilityGate flag="health">
      <SectionHeading
        title="A healthier connection starts with why."
        description="Ranked hypotheses, supporting signals, and measured next steps. Unknown stays unknown."
      >
        <ExperimentalBadge />
      </SectionHeading>
      <div className="health-summary card">
        <span className="health-summary-icon">
          <HeartPulse size={27} />
        </span>
        <div>
          <h2>Measured signals, explained.</h2>
          <p>
            These hypotheses are ranked from the agent's live latency, jitter, loss and QoE
            measurements. Diagnosis is observation-only; no network change is made.
          </p>
        </div>
        <Badge tone="blue">Observation only</Badge>
      </div>
      {query.error ? (
        <ErrorState message={query.error.message} retry={() => void query.refetch()} />
      ) : !query.data ? (
        <Skeletons />
      ) : (
        <div className="diagnoses-list">
          {query.data.map((d, i) => (
            <motion.article layout className="card diagnosis-card" key={d.id}>
              <div className="diagnosis-rank">0{i + 1}</div>
              <div className="diagnosis-main">
                <div className="row-between">
                  <h3>{d.title}</h3>
                  <span>{percent(d.confidence)} confidence</span>
                </div>
                <ProgressBar value={d.confidence} />
                <div className="signal-chips">
                  {d.signals.map((s) => (
                    <span className={s.includes("unknown") ? "unknown-signal" : ""} key={s}>
                      {s.includes("unknown") ? <Info size={11} /> : <CheckCircle2 size={11} />} {s}
                    </span>
                  ))}
                </div>
                <div className="recommendation">
                  <Search size={15} />
                  <div>
                    <strong>Recommended check</strong>
                    <p>{d.recommendation}</p>
                  </div>
                </div>
              </div>
            </motion.article>
          ))}
        </div>
      )}
      <div className="page-note">
        <ShieldCheck size={14} /> No automatic remedies are enabled. Only allow-listed, reversible
        changes should ever be applied by a real agent.
      </div>
    </CapabilityGate>
  );
}
export function Demand() {
  const { snapshot, settings } = useConsole();
  const query = useApi<Schedule[]>("/demand/schedules");
  const [add, setAdd] = useState(false);
  const [name, setName] = useState("");
  const [device, setDevice] = useState(snapshot.devices[0]?.name ?? "");
  const [time, setTime] = useState("09:00");
  const [duration, setDuration] = useState("60 min");
  const task = useTask("Demand schedule updated.");
  return (
    <CapabilityGate flag="demand">
      <SectionHeading
        title="A little ahead of your everyday."
        description="Prepare for familiar moments. You choose what the network learns."
      >
        <ExperimentalBadge />
        <Button onClick={() => setAdd(true)}>
          <Plus size={14} /> Add schedule
        </Button>
      </SectionHeading>
      <section className="card demand-learning">
        <span>
          <CalendarClock size={24} />
        </span>
        <div>
          <h3>Prepare, don’t presume.</h3>
          <p>
            Profiles are prepared 5 minutes ahead. They only activate when matching traffic is
            observed.
          </p>
        </div>
        <div>
          <label>Learning {settings?.demandLearning ? "on" : "off"}</label>
          <Toggle
            label="Enable demand learning"
            checked={settings?.demandLearning ?? false}
            onChange={(value) =>
              settings &&
              task.mutate({
                path: "/settings",
                method: "PUT",
                body: { ...settings, demandLearning: value },
              })
            }
          />
        </div>
      </section>
      {query.error ? (
        <ErrorState message={query.error.message} retry={() => void query.refetch()} />
      ) : !query.data ? (
        <Skeletons />
      ) : !query.data.length ? (
        <div className="card">
          <EmptyState
            title="A clean slate for your routines."
            description="Add a schedule when you’d like the agent to prepare ahead."
          />
        </div>
      ) : (
        <div className="demand-timeline">
          {query.data.map((s) => (
            <section className="card schedule-card" key={s.id}>
              <div className="schedule-time">
                <ClockIcon />
                <strong>{s.time}</strong>
                <span>Daily · local time</span>
              </div>
              <div className="schedule-description">
                <Badge tone="blue">5 min preparation lead</Badge>
                <h2>{s.name}</h2>
                <p>
                  {s.device} <span>·</span> {s.duration}
                </p>
                <div className="schedule-profile">
                  <span>Prepared profile</span>
                  <ProgressBar value={s.confidence} />
                  <strong>{percent(s.confidence)} confidence</strong>
                </div>
              </div>
              <div className="schedule-actions">
                <Badge>Opt-in schedule</Badge>
                <Button
                  variant="ghost"
                  aria-label={`Delete ${s.name}`}
                  pending={task.isPending}
                  onClick={() =>
                    task.mutate({ path: `/demand/schedules?id=${s.id}`, method: "DELETE" })
                  }
                >
                  <Trash2 size={14} />
                </Button>
              </div>
            </section>
          ))}
        </div>
      )}
      <Modal open={add} onClose={() => setAdd(false)} title="Plan a little headroom">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            task.mutate(
              { path: "/demand/schedules", body: { name, device, time, duration } },
              {
                onSuccess: () => {
                  setAdd(false);
                  setName("");
                },
              },
            );
          }}
        >
          <label className="field-label">
            Schedule name
            <input
              required
              minLength={1}
              maxLength={80}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Weekday stand-up"
            />
          </label>
          <label className="field-label">
            Device
            <select value={device} onChange={(e) => setDevice(e.target.value)}>
              {snapshot.devices.map((d) => (
                <option key={d.id}>{d.name}</option>
              ))}
            </select>
          </label>
          <div className="form-row">
            <label className="field-label">
              Time
              <input type="time" required value={time} onChange={(e) => setTime(e.target.value)} />
            </label>
            <label className="field-label">
              Duration
              <select value={duration} onChange={(e) => setDuration(e.target.value)}>
                <option>30 min</option>
                <option>60 min</option>
                <option>120 min</option>
              </select>
            </label>
          </div>
          <div className="modal-footer">
            <Button variant="secondary" type="button" onClick={() => setAdd(false)}>
              Cancel
            </Button>
            <Button pending={task.isPending} type="submit">
              Save schedule
            </Button>
          </div>
        </form>
      </Modal>
    </CapabilityGate>
  );
}
function ClockIcon() {
  return <CalendarClock size={20} />;
}
export function Links() {
  const query = useApi<WANLink[]>("/links");
  const task = useTask<{ recommended: string; reason: string; emulated: boolean }>(
    "Path evaluation complete. No real failover was performed.",
  );
  return (
    <CapabilityGate flag="links">
      <SectionHeading
        title="Two paths. One thoughtful connection."
        description="See every uplink and understand what a path change would mean."
      >
        <ExperimentalBadge />
        <Button
          variant="secondary"
          pending={task.isPending}
          onClick={() => task.mutate({ path: "/paths/evaluate" })}
        >
          <Network size={14} /> Evaluate paths
        </Button>
      </SectionHeading>
      {query.error ? (
        <ErrorState message={query.error.message} />
      ) : !query.data ? (
        <Skeletons />
      ) : query.data.length < 2 ? (
        <div className="card">
          <EmptyState
            title="One link is connected."
            description="Multi-link controls appear only when the agent reports a second uplink."
          />
        </div>
      ) : (
        <div className="links-grid">
          {query.data.map((link, i) => (
            <section className="card wan-card" key={link.id}>
              <div className="row-between">
                <span className={`wan-icon wan-icon-${i}`}>
                  <Signal size={29} />
                </span>
                <Badge tone={link.state === "active" ? "blue" : "neutral"}>{link.state}</Badge>
              </div>
              <h2>{link.name}</h2>
              <p>{link.type}</p>
              <div className={`wan-pipe ${link.state === "active" ? "pipe-active" : ""}`}>
                <i />
                <i />
                <i />
                <span>
                  {link.throughput.toFixed(1)} / {link.capacity} Mbps
                </span>
              </div>
              <div className="wan-stats">
                <div>
                  <span>RTT</span>
                  <strong>
                    {link.rtt} <small>ms</small>
                  </strong>
                </div>
                <div>
                  <span>Loss</span>
                  <strong>
                    {link.loss} <small>%</small>
                  </strong>
                </div>
                <div>
                  <span>Capacity</span>
                  <strong>
                    {link.capacity} <small>Mbps</small>
                  </strong>
                </div>
              </div>
            </section>
          ))}
        </div>
      )}
      {task.data && (
        <div className="info-banner">
          <Network size={20} />
          <p>
            <strong>Recommended: {task.data.recommended}.</strong> {task.data.reason}
          </p>
        </div>
      )}
      <section className="card link-warning">
        <Info size={21} />
        <div>
          <h3>A path change can interrupt a moment.</h3>
          <p>
            Failover may reset sessions, interrupt calls, or change public IP addresses. This
            prototype evaluates paths only; it never silently reroutes traffic.
          </p>
        </div>
      </section>
    </CapabilityGate>
  );
}
export function Agent() {
  const navigate = useNavigate();
  const info = useApi<AgentInfo>("/agent/info");
  const sessions = useApi<Session[]>("/sessions");
  const audit = useApi<LogEntry[]>("/audit");
  const logs = useApi<LogEntry[]>("/agent/logs");
  const [tab, setTab] = useState("audit");
  const [confirm, setConfirm] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [reveal, setReveal] = useState(false);
  const task = useTask<{ key: string }>("Security operation completed.");
  const entries = tab === "audit" ? audit.data : logs.data;
  return (
    <>
      <SectionHeading
        title="Your agent. Your trust boundary."
        description="A clear view of the machine, its sessions, and the changes that matter."
      >
        <Button
          variant="secondary"
          onClick={() =>
            download("experience-budget-diagnostics.json", {
              agent: info.data,
              audit: audit.data,
              logs: logs.data,
              warning: "Local diagnostic export. No pairing keys included.",
            })
          }
        >
          <Download size={14} /> Export diagnostics
        </Button>
      </SectionHeading>
      {info.error ? (
        <ErrorState message={info.error.message} />
      ) : !info.data ? (
        <Skeletons />
      ) : (
        <>
          <section className="card agent-info-card">
            <span className="agent-machine-icon">
              <Server size={32} />
            </span>
            <div>
              <div className="row-between">
                <h2>{info.data.hostname}</h2>
                <Badge tone={info.data.emulated ? "emulated" : "blue"}>
                  {info.data.emulated ? "Emulated agent" : "Live agent"}
                </Badge>
              </div>
              <p>
                {info.data.os} <span>·</span> v{info.data.version}
              </p>
              <div className="agent-info-stats">
                <span>
                  Uptime <strong>{info.data.uptime}</strong>
                </span>
                <span>
                  Control loop <strong>{info.data.loopMs} ms</strong>
                </span>
                <span>
                  Local DB <strong>{info.data.dbSize}</strong>
                </span>
                <span>
                  Random seed <strong>{info.data.seed || "—"}</strong>
                </span>
              </div>
            </div>
          </section>
          <div className="security-grid">
            <section className="card">
              <CardHeader
                title="Trusted sessions"
                subtitle="Revoke anything you don’t recognize."
              />
              <div className="session-list">
                {sessions.data?.map((s) => (
                  <div className="session-row" key={s.id}>
                    <span className="session-icon">
                      <GlobeIcon />
                    </span>
                    <div>
                      <h3>
                        {s.name} {s.current && <Badge tone="blue">This session</Badge>}
                      </h3>
                      <p>
                        {s.location} · {s.lastSeen}
                      </p>
                    </div>
                    {!s.current && (
                      <Button variant="ghost" onClick={() => setConfirm(s.id)}>
                        Revoke
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            </section>
            <section className="card pairing-security">
              <CardHeader
                title="Pairing key hygiene"
                subtitle="Treat pairing keys like a password."
              />
              <div className="security-key">
                <LockKeyhole size={17} />
                <code>{key ? (reveal ? key : "EB-••••-••••-••••") : "EB-••••-••••-••••"}</code>
                <button
                  aria-label={reveal ? "Hide rotated key" : "Reveal rotated key"}
                  disabled={!key}
                  onClick={() => setReveal(!reveal)}
                >
                  {reveal ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
              <p>
                Rotate your key if it may have been exposed. This is a local emulation; no real
                agent credentials are changed.
              </p>
              <div className="key-actions">
                <Button variant="secondary" onClick={() => setConfirm("rotate")}>
                  <RotateCcw size={13} /> Rotate pairing key
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => {
                    try {
                      localStorage.removeItem("eb-key");
                    } catch {}
                    navigate("/connect");
                  }}
                >
                  Forget agent
                </Button>
              </div>
            </section>
          </div>
          <section className="card audit-card">
            <div className="audit-header">
              <h2>A record you can read.</h2>
              <div className="segmented-control">
                <button className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")}>
                  Audit log
                </button>
                <button className={tab === "logs" ? "active" : ""} onClick={() => setTab("logs")}>
                  Agent logs
                </button>
              </div>
            </div>
            <div className="audit-entries">
              {entries?.map((entry) => (
                <div key={entry.id}>
                  <span>{dateTime(entry.ts)}</span>
                  <div>
                    <strong>{entry.action}</strong>
                    <p>{entry.detail}</p>
                  </div>
                  <ShieldCheck size={14} />
                </div>
              ))}
            </div>
          </section>
        </>
      )}
      <Modal
        open={!!confirm}
        onClose={() => setConfirm(null)}
        title={confirm === "rotate" ? "Rotate the pairing key?" : "Revoke this session?"}
      >
        <p className="dialog-description">
          {confirm === "rotate"
            ? "The previous demo key will be invalidated. A new key will be shown once, masked by default."
            : "This session will lose access to the agent. This browser remains connected."}
        </p>
        <div className="modal-footer">
          <Button variant="secondary" onClick={() => setConfirm(null)}>
            Cancel
          </Button>
          <Button
            pending={task.isPending}
            onClick={() =>
              task.mutate(
                {
                  path:
                    confirm === "rotate"
                      ? "/keys/rotate"
                      : `/sessions/${encodeURIComponent(confirm ?? "")}`,
                  method: confirm === "rotate" ? "POST" : "DELETE",
                },
                {
                  onSuccess: (data) => {
                    if (confirm === "rotate") {
                      setKey(data.key);
                      setReveal(false);
                    }
                    setConfirm(null);
                  },
                },
              )
            }
          >
            Confirm {confirm === "rotate" ? "rotation" : "revoke"}
          </Button>
        </div>
      </Modal>
    </>
  );
}
function GlobeIcon() {
  return <Wifi size={17} />;
}
