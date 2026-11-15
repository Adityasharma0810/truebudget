import { useEffect, useState } from "react";
import {
  Save,
  ShieldCheck,
  LockKeyhole,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Info,
  ArrowRight,
} from "lucide-react";
import { useConsole } from "../../app/Layout";
import { useApi } from "../../lib/api";
import {
  SectionHeading,
  Button,
  CardHeader,
  Badge,
  ExperimentalBadge,
  useTask,
  Skeletons,
  ErrorState,
} from "../../components/ui";
import { useEffectiveTier, useMotionPreference, tiers } from "../../motion/store";
import type { Settings as SettingsType } from "../../types";
export default function Settings() {
  const { openSafe } = useConsole();
  const query = useApi<SettingsType>("/settings");
  const [draft, setDraft] = useState<SettingsType | null>(null);
  const task = useTask("Settings saved. Control loop configuration updated.");
  const tier = useEffectiveTier();
  const setTier = useMotionPreference((s) => s.setTier);
  useEffect(() => {
    if (query.data && !draft) setDraft(query.data);
  }, [query.data]);
  if (query.error)
    return <ErrorState message={query.error.message} retry={() => void query.refetch()} />;
  if (!draft) return <Skeletons />;
  const set = <K extends keyof SettingsType>(key: K, value: SettingsType[K]) =>
    setDraft({ ...draft, [key]: value });
  return (
    <>
      <SectionHeading
        title="Thoughtful defaults. Your control."
        description="Fine-tune the loop without compromising the network’s safety net."
      >
        <Button variant="secondary" onClick={() => setDraft(query.data ?? null)}>
          Discard changes
        </Button>
        <Button
          pending={task.isPending}
          onClick={() => task.mutate({ path: "/settings", method: "PUT", body: draft })}
        >
          <Save size={14} /> Save settings
        </Button>
      </SectionHeading>
      <div className="settings-grid">
        <section className="card settings-card">
          <CardHeader
            title="Fairness & stability"
            subtitle="Small, measured changes keep the connection calm."
          />
          <SettingSlider
            title="Guaranteed service floor"
            description="Minimum bandwidth for every active device. Never zero."
            value={draft.floorMbps}
            min={1}
            max={20}
            step={1}
            unit="Mbps"
            onChange={(v) => set("floorMbps", v)}
            locked
          />
          <SettingSlider
            title="Maximum rate change"
            description="Limit how much an allocation can move in one loop."
            value={draft.rateChangePct}
            min={1}
            max={50}
            unit="% / loop"
            onChange={(v) => set("rateChangePct", v)}
          />
          <SettingSlider
            title="Hysteresis / dwell time"
            description="Let the network settle before adjusting again."
            value={draft.dwellSeconds}
            min={10}
            max={300}
            step={10}
            unit="seconds"
            onChange={(v) => set("dwellSeconds", v)}
          />
        </section>
        <section className="card settings-card">
          <CardHeader
            title="Intelligence & observation"
            subtitle="When confidence is low, react to what you know."
          />
          <SettingSlider
            title="Forecast confidence gate"
            description="Below this confidence, use reactive fallback."
            value={Math.round(draft.forecastThreshold * 100)}
            min={50}
            max={100}
            unit="%"
            onChange={(v) => set("forecastThreshold", v / 100)}
          />
          <SettingSlider
            title="Classifier confidence threshold"
            description="Low-confidence flows remain unknown."
            value={Math.round(draft.classifierThreshold * 100)}
            min={50}
            max={100}
            unit="%"
            onChange={(v) => set("classifierThreshold", v / 100)}
          />
          <div className="setting-row">
            <div>
              <h3>Observation interval</h3>
              <p>Real-agent loop timing. Demo clock is accelerated.</p>
            </div>
            <select
              aria-label="Observation interval"
              value={draft.intervalSeconds}
              onChange={(e) => set("intervalSeconds", Number(e.target.value))}
            >
              <option value={30}>30 seconds</option>
              <option value={60}>60 seconds</option>
              <option value={15}>15 seconds · lab</option>
            </select>
          </div>
        </section>
        <section className="card settings-card">
          <CardHeader title="Experimental capabilities">
            <ExperimentalBadge />
          </CardHeader>
          {Object.entries(draft.flags).map(([flag, enabled]) => (
            <div className="setting-row" key={flag}>
              <div>
                <h3>
                  {(
                    {
                      conflicts: "Intent conflict negotiation",
                      health: "Self-healing diagnostics",
                      demand: "Demand forecast",
                      links: "Multi-link support",
                      federated: "Federated learning",
                      ask: "Ask the router",
                    } as Record<string, string>
                  )[flag] ?? flag}
                </h3>
                <p>Show experimental {flag} controls in this console.</p>
              </div>
              <Toggle
                label={`Enable ${flag}`}
                checked={enabled}
                onChange={(v) => set("flags", { ...draft.flags, [flag]: v })}
              />
            </div>
          ))}
        </section>
        <section className="card settings-card">
          <CardHeader
            title="Privacy, learning & motion"
            subtitle="Local-first means you choose what stays."
          />
          <div className="setting-row">
            <div>
              <h3>Data retention</h3>
              <p>Retain metadata and decisions locally.</p>
            </div>
            <select
              aria-label="Retention days"
              value={draft.retentionDays}
              onChange={(e) => set("retentionDays", Number(e.target.value))}
            >
              <option value={7}>7 days</option>
              <option value={30}>30 days</option>
              <option value={90}>90 days</option>
            </select>
          </div>
          <div className="setting-row">
            <div>
              <h3>Demand learning</h3>
              <p>Prepare profiles ahead of familiar routines.</p>
            </div>
            <Toggle
              label="Demand learning"
              checked={draft.demandLearning}
              onChange={(v) => set("demandLearning", v)}
            />
          </div>
          {draft.flags.federated && (
            <div className="setting-row">
              <div>
                <h3>
                  Federated learning <ExperimentalBadge />
                </h3>
                <p>Opt-in only. Emulated; no model data is uploaded.</p>
              </div>
              <Toggle
                label="Federated learning opt in"
                checked={draft.federated}
                onChange={(v) => set("federated", v)}
              />
            </div>
          )}
          <div className="setting-row">
            <div>
              <h3>Motion preference</h3>
              <p>System reduced-motion preference takes priority.</p>
            </div>
            <select
              aria-label="Settings motion preference"
              value={tier}
              onChange={(e) => setTier(e.target.value as typeof tier)}
            >
              {tiers.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </div>
        </section>
      </div>
      <section className="card safety-card">
        <div className="safety-symbol">
          <ShieldCheck size={25} />
        </div>
        <div>
          <h2>A calmer starting point, anytime.</h2>
          <p>
            Cancel temporary priorities and return to a conservative safe allocation. Your fairness
            floor remains protected.
          </p>
        </div>
        <Button variant="secondary" onClick={openSafe}>
          <RotateCcw size={14} /> Reset to safe state
        </Button>
      </section>
      <div className="page-note">
        <Info size={13} /> Demo settings persist in this browser only. Nothing changes a real
        network.
      </div>
    </>
  );
}
function SettingSlider({
  title,
  description,
  value,
  min,
  max,
  step = 1,
  unit,
  onChange,
  locked = false,
}: {
  title: string;
  description: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit: string;
  onChange: (v: number) => void;
  locked?: boolean;
}) {
  return (
    <div className="setting-slider">
      <div className="row-between">
        <label>
          {locked && <LockKeyhole size={12} />} {title}
        </label>
        <span className="slider-value">
          {value} <small>{unit}</small>
        </span>
      </div>
      <p>{description}</p>
      <input
        type="range"
        aria-label={title}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <div className="range-endpoints">
        <span>
          {min} {unit}
        </span>
        <span>
          {max} {unit}
        </span>
      </div>
    </div>
  );
}
export function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      className={`toggle ${checked ? "toggle-on" : ""}`}
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
    >
      <span />
    </button>
  );
}
