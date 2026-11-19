import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowUpRight,
  ArrowRight,
  ChevronDown,
  Wifi,
  ShieldCheck,
  Zap,
  CircleHelp,
  Users,
  Activity,
  SlidersHorizontal,
  Check,
  Radio,
  RotateCcw,
  CalendarDays,
  Plus,
  Info,
  LockKeyhole,
  Sparkles,
  Network,
  Gauge,
  GitBranch,
  CheckCircle2,
} from "lucide-react";
import { motion } from "motion/react";
import { useApi } from "../../lib/api";
import { useConsole, FreshnessPill } from "../../app/Layout";
import {
  CardHeader,
  EmulatedBadge,
  Button,
  Badge,
  InfoTip,
  QoEBadge,
  DeviceIcon,
  TrafficIcon,
  LockNote,
} from "../../components/ui";
import { CapacityChart, Sparkline } from "../../components/Chart";
import { Topology } from "../../components/Topology";
import { SpotlightCard, Reveal } from "../../motion/primitives";
import { percent, dateTime } from "../../lib/format";
import type { MetricPoint, Decision, Intent } from "../../types";
import { remainingSeconds } from "../../lib/qoe";
export default function Home() {
  const { snapshot: s, settings, capabilities, openBoost, openSafe } = useConsole();
  const [range, setRange] = useState("1h");
  const { data: points = [] } = useApi<MetricPoint[]>(`/metrics?range=${range}`, {
    refetchInterval: 3000,
  });
  const { data: decisions = [] } = useApi<Decision[]>("/decisions");
  const { data: intents = [] } = useApi<Intent[]>("/intents");
  const [boostDevice, setBoostDevice] = useState(s.devices[0]?.id ?? "");
  const active = s.devices.filter((d) => d.flows.length).length;
  const householdQoe = s.household.qoe;
  const score = householdQoe === null ? null : Math.round(householdQoe * 100);
  const qoeBand = householdQoe === null ? "idle" : score! >= 75 ? "green" : score! >= 50 ? "amber" : "red";
  const activeIntent = intents.find((i) => i.status === "active");
  return (
    <>
      <SectionWelcome />
      <div className="stats-grid">
        <SpotlightCard className="stat-card qoe-stat">
          <div className="stat-top">
            <span>
              Household experience{" "}
              <InfoTip
                text={
                  capabilities?.formulas.household ??
                  "Mean active-device QoE proxy. Not a subjective score."
                }
              />
            </span>
            <span className="stat-icon mint-icon">
              <Sparkles size={15} />
            </span>
          </div>
          <div className="stat-value-row">
            <div>
              <div className="stat-value">
                {score === null ? <span>—</span> : <LiveNumber value={score} />}
                <span>/100</span>
              </div>
              <div className="stat-note">
                <QoEBadge band={qoeBand} small />
                <span>{score === null ? "No active flows" : "QoE proxy"}</span>
              </div>
            </div>
            <div className="mini-gauge">
              <svg viewBox="0 0 80 65">
                <path
                  d="M12 55 A31 31 0 1 1 68 55"
                  fill="none"
                  stroke="#e9f4ee"
                  strokeWidth="7"
                  strokeLinecap="round"
                />
                <motion.path
                  d="M12 55 A31 31 0 1 1 68 55"
                  fill="none"
                  stroke="#38b88b"
                  strokeWidth="7"
                  strokeLinecap="round"
                  initial={{ pathLength: 0 }}
                  animate={{ pathLength: householdQoe ?? 0 }}
                  transition={{ duration: 0.9 }}
                />
                <path d="M40 37 L54 22" stroke="#76bd9d" strokeWidth="2" strokeLinecap="round" />
                <circle cx="40" cy="37" r="3" fill="#38b88b" />
              </svg>
              <span>{score === null ? "No traffic yet" : score >= 75 ? "Looking good" : score >= 50 ? "Some congestion" : "Needs attention"}</span>
            </div>
          </div>
          <div className="stat-bottom">
            <CheckCircle2 size={11} />{" "}
            {score === null
              ? "Waiting for an active application flow"
              : score >= 75
                ? "Everyone has room to do their thing"
                : "Measured from live latency, jitter and loss"}
          </div>
        </SpotlightCard>
        <SpotlightCard className="stat-card">
          <div className="stat-top">
            <span>
              Available capacity{" "}
              <InfoTip text="An estimate, not your subscribed line rate. The ± value shows modeled uncertainty." />
            </span>
            <span className="stat-icon blue-icon">
              <Wifi size={16} />
            </span>
          </div>
          <div className="stat-value-row">
            <div>
              <div className="stat-value">
                <LiveNumber value={s.link.capacityMbps} digits={1} />
                <span>Mbps</span>
              </div>
              <div className="stat-note">± {s.link.uncertaintyMbps} Mbps uncertainty</div>
            </div>
            <Sparkline points={points.slice(-22).map((p) => p.capacity)} />
          </div>
          <div className="stat-bottom">
            <span className="mini-line" /> Confidence {percent(s.link.forecastConfidence)}{" "}
            <span className="stat-bottom-right">
              {s.link.forecastMethod === "holt_winters"
                ? "Holt-Winters"
                : s.link.forecastMethod === "linear_trend"
                  ? "Linear trend"
                  : s.link.forecastMethod === "persistence"
                    ? "Persistence"
                    : "Unavailable"}
            </span>
          </div>
        </SpotlightCard>
        <SpotlightCard className="stat-card">
          <div className="stat-top">
            <span>Connected devices</span>
            <span className="stat-icon lilac-icon">
              <LaptopIcon />
            </span>
          </div>
          <div className="stat-value-row">
            <div>
              <div className="stat-value">
                {s.devices.length}
                <span>devices</span>
              </div>
              <div className="stat-note">
                <span className="active-dot" />
                {active} active <span className="stat-separator">·</span>
                {s.devices.length - active} idle
              </div>
            </div>
            <div className="device-stack">
              {s.devices.slice(0, 3).map((d) => (
                <span key={d.id}>
                  <DeviceIcon device={d} size={15} />
                </span>
              ))}
            </div>
          </div>
          <Link to="/app/devices" className="stat-bottom stat-link">
            A little connection for everyone <ArrowUpRight size={12} />
          </Link>
        </SpotlightCard>
        <SpotlightCard className="stat-card">
          <div className="stat-top">
            <span>
              Fairness index{" "}
              <InfoTip
                text={
                  capabilities?.formulas.fairness ?? "Jain fairness index. 1.0 is equal allocation."
                }
              />
            </span>
            <span className="stat-icon peach-icon">
              <ShieldCheck size={15} />
            </span>
          </div>
          <div className="stat-value-row">
            <div>
              <div className="stat-value">
                <LiveNumber value={s.household.fairness} digits={2} />
                <span>/1.00</span>
              </div>
              <div className="stat-note">A fair share, not an equal split</div>
            </div>
            <div className="fairness-bars">
              {s.devices
                .filter((d) => d.qoe !== null)
                .map((d) => (
                  <i key={d.id} style={{ height: `${d.qoe! * 32}px` }} />
                ))}
            </div>
          </div>
          <div className="stat-bottom">
            <LockKeyhole size={10} />
            {settings?.floorMbps ?? 2} Mbps minimum per device{" "}
            <span className="stat-bottom-right">Protected</span>
          </div>
        </SpotlightCard>
      </div>
      <div className="home-middle-grid">
        <Reveal delay={2}>
          <section className="card capacity-card">
            <CardHeader
              title="Room for every experience"
              subtitle="Your bandwidth, working a little smarter."
            >
              <div className={`mode-pill mode-${s.mode}`}>
                <span />
                {s.mode === "adaptive"
                  ? "Adaptive mode"
                  : s.mode === "safe_state"
                    ? "Safe state"
                    : "Reactive fallback"}
              </div>
            </CardHeader>
            <div className="chart-topline">
              <div className="chart-legends">
                <span>
                  <i className="legend-throughput" /> Throughput
                </span>
                <span>
                  <i className="legend-capacity" /> Estimated capacity
                </span>
                <span>
                  <i className="legend-band" /> Uncertainty
                </span>
              </div>
              <select
                aria-label="Chart time range"
                value={range}
                onChange={(e) => setRange(e.target.value)}
                className="compact-select"
              >
                <option value="15m">15 minutes</option>
                <option value="1h">Last hour</option>
                <option value="24h">24 hours</option>
                <option value="30d">30 days</option>
              </select>
            </div>
            <div className="chart-unit">Mbps</div>
            <CapacityChart points={points} forecast={s.link.forecast} />
            <div className="chart-footer">
              <span>
                <Sparkles size={12} /> Forecast confidence{" "}
                <strong>{percent(s.link.forecastConfidence)}</strong>
                {s.link.forecastGate && !s.link.forecastGate.useForecast && (
                  <span className="stat-bottom-right"> reactive fallback</span>
                )}
                <InfoTip
                  text={`Forecast is only used above the configured confidence threshold. Gate: ${
                    s.link.forecastGate?.reason ?? "unavailable"
                  }.`}
                />
              </span>
              <Link to="/app/metrics">
                Explore metrics <ArrowRight size={12} />
              </Link>
            </div>
          </section>
        </Reveal>
        <Reveal delay={3}>
          <section className="card experience-card">
            <CardHeader
              title="A good day for your network"
              subtitle="Small adjustments. Noticeably better moments."
            >
              <span className="soft-icon">
                <ShieldCheck size={18} />
              </span>
            </CardHeader>
            <div className="experience-checklist">
              <div>
                <span className="experience-list-icon">
                  <Radio size={15} />
                </span>
                <div>
                  <strong>Calls stay clear</strong>
                  <span>
                    {s.metrics.latencyMs.toFixed(0)} ms latency · {s.metrics.jitterMs.toFixed(1)} ms
                    jitter
                  </span>
                </div>
                <span className="tiny-check">
                  <Check size={11} />
                </span>
              </div>
              <div>
                <span className="experience-list-icon purple">
                  <GitBranch size={15} />
                </span>
                <div>
                  <strong>No device left behind</strong>
                  <span>Minimum service floor, always on</span>
                </div>
                <span className="tiny-check">
                  <Check size={11} />
                </span>
              </div>
              <div>
                <span className="experience-list-icon peach">
                  <ShieldCheck size={15} />
                </span>
                <div>
                  <strong>Changes that prove themselves</strong>
                  <span>The verifier checks every adjustment</span>
                </div>
                <span className="tiny-check">
                  <Check size={11} />
                </span>
              </div>
            </div>
            <div className="verifier-note">
              <div className="verifier-symbol">
                <Check size={14} />
              </div>
              <div>
                <strong>Protected, not just prioritized.</strong>
                <p>
                  Changes that don’t help get rolled back.
                  <br />
                  You can see the why, every time.
                </p>
              </div>
              <Link to="/app/decisions" aria-label="See decision log">
                <ArrowUpRight size={15} />
              </Link>
            </div>
          </section>
        </Reveal>
      </div>
      <div className="home-bottom-grid">
        <Reveal delay={4}>
          <section className="card topology-card">
            <CardHeader
              title="One connection. Different moments."
              subtitle="A live look at who’s doing what."
            >
              <Link to="/app/tree" className="text-link">
                Open usage tree <ArrowUpRight size={12} />
              </Link>
            </CardHeader>
            <Topology snapshot={s} />
          </section>
        </Reveal>
        <Reveal delay={5}>
          <section className="card quick-boost-card">
            <div className="boost-card-glow" />
            <div className="row-between">
              <span className="boost-symbol">
                <Zap size={18} />
              </span>
              <Badge tone="purple">TEMPORARY INTENT</Badge>
            </div>
            <h2>{activeIntent ? "A little more room. Right now." : "Some moments matter more."}</h2>
            <p>
              {activeIntent
                ? activeIntent.goal
                : "Big call? Final round? Give it a little extra room, just for a while."}
            </p>
            <label className="boost-device-select">
              <span>
                <DeviceIcon
                  device={s.devices.find((d) => d.id === boostDevice) ?? s.devices[0] ?? null}
                  size={15}
                />
              </span>
              <select
                aria-label="Device to prioritize"
                value={boostDevice}
                onChange={(e) => setBoostDevice(e.target.value)}
              >
                {s.devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
              <ChevronDown size={12} />
            </label>
            <Button onClick={openBoost} className="full-width">
              <Zap size={13} />
              {activeIntent ? "Manage temporary boost" : "Boost an experience"}
              <ArrowRight size={14} />
            </Button>
            <p className="boost-footnote">
              <LockKeyhole size={11} /> Auto-reverts. Fairness floor stays on.
            </p>
          </section>
        </Reveal>
      </div>
      <Reveal delay={6}>
        <section className="card recent-decisions">
          <CardHeader
            title="Quietly making things better"
            subtitle="The latest decisions, with the why included."
          >
            <Link to="/app/decisions" className="text-link">
              All decisions <ArrowRight size={12} />
            </Link>
          </CardHeader>
          <div className="recent-decision-list">
            {decisions.slice(0, 3).map((d) => (
              <Link to={`/app/decisions?entry=${d.id}`} key={d.id} className="recent-decision-row">
                <span className={`decision-icon verdict-${d.verdict}`}>
                  {d.verdict === "rolled_back" ? (
                    <RotateCcw size={14} />
                  ) : d.verdict === "escalated" ? (
                    <Info size={14} />
                  ) : (
                    <Check size={14} />
                  )}
                </span>
                <div>
                  <strong>{d.trigger}</strong>
                  <p>{d.reason}</p>
                </div>
                <span className="decision-time">{dateTime(d.ts)}</span>
                <ChevronRightIcon />
              </Link>
            ))}
          </div>
        </section>
      </Reveal>
    </>
  );
}
function SectionWelcome() {
  const { openBoost, snapshot: s } = useConsole();
  return (
    <header className="page-heading home-heading">
      <div>
        <div className="home-eyebrow">
          <span className="home-eyebrow-line" /> YOUR NETWORK, IN HARMONY{" "}
          {s.agent.emulated && <EmulatedBadge />}
        </div>
        <h1>
          Good things flow together<span className="heading-period">.</span>
        </h1>
        <p>A little balance. A better experience. Here’s how your home is doing.</p>
      </div>
      <div className="welcome-actions">
        <FreshnessPill />
        <Button onClick={openBoost}>
          <Zap size={14} /> Boost an experience
        </Button>
      </div>
    </header>
  );
}
function LiveNumber({ value, digits = 0 }: { value: number; digits?: number }) {
  return <span className="live-number">{value.toFixed(digits)}</span>;
}
function LaptopIcon() {
  return <Network size={15} />;
}
function ChevronRightIcon() {
  return <ArrowRight className="muted" size={13} />;
}
