import { useEffect, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import {
  GitFork,
  CircleDot,
  List,
  ArrowRight,
  Info,
  SlidersHorizontal,
  Radio,
  Pencil,
  Check,
  ShieldCheck,
} from "lucide-react";
import { useConsole } from "../../app/Layout";
import { Topology } from "../../components/Topology";
import {
  SectionHeading,
  Badge,
  Button,
  CardHeader,
  DeviceIcon,
  Modal,
  QoEBadge,
  TrafficIcon,
  ProgressBar,
  useTask,
  InfoTip,
  ExperimentalBadge,
} from "../../components/ui";
import type { Device, Flow } from "../../types";
import { percent } from "../../lib/format";
export default function Tree() {
  const { snapshot, capabilities } = useConsole();
  const [view, setView] = useState("tree");
  const [selected, setSelected] = useState<Device | null>(null);
  const [flow, setFlow] = useState<Flow | null>(null);
  const [correction, setCorrection] = useState("call");
  const task = useTask("Label corrected. Added to the review queue.");
  const [search] = useSearchParams();
  useEffect(() => {
    const id = search.get("device");
    if (id) setSelected(snapshot.devices.find((d) => d.id === id) ?? null);
  }, [search]);
  const current = selected
    ? (snapshot.devices.find((d) => d.id === selected.id) ?? selected)
    : null;
  return (
    <>
      <SectionHeading
        title="Every device has a story."
        description="Follow the flow from one shared connection to every experience."
      >
        <Badge tone="blue">
          <Radio size={11} /> Privacy-safe classifier
        </Badge>
      </SectionHeading>
      <section className="card">
        <div className="tree-toolbar">
          <div className="segmented-control">
            {[
              { id: "tree", label: "Tree", icon: GitFork },
              { id: "radial", label: "Radial", icon: CircleDot },
              { id: "table", label: "Table", icon: List },
            ].map((v) => (
              <button
                key={v.id}
                className={view === v.id ? "active" : ""}
                onClick={() => setView(v.id)}
              >
                <v.icon size={13} />
                {v.label}
              </button>
            ))}
          </div>
          <span className="muted small-label">
            {snapshot.devices.length} devices ·{" "}
            {snapshot.devices.reduce((n, d) => n + d.flows.length, 0)} active flows
          </span>
        </div>
        {view === "tree" ? (
          <Topology
            snapshot={snapshot}
            large
            onDevice={(d) => {
              setSelected(d);
              setFlow(null);
            }}
          />
        ) : view === "radial" ? (
          <div className="radial-network">
            <div className="radial-center">
              <GitFork size={25} />
              <strong>EB agent</strong>
            </div>
            <svg viewBox="0 0 600 400" aria-hidden="true">
              {snapshot.devices.map((d, i) => {
                const a = (i / 5) * Math.PI * 2 - Math.PI / 2;
                return (
                  <path
                    className="flowing-path"
                    key={d.id}
                    d={`M300 200 L${300 + Math.cos(a) * 180} ${200 + Math.sin(a) * 145}`}
                    fill="none"
                    stroke="#c8d9fb"
                    strokeWidth="2"
                    strokeDasharray="5 5"
                  />
                );
              })}
            </svg>
            {snapshot.devices.map((d, i) => {
              const a = (i / 5) * Math.PI * 2 - Math.PI / 2;
              return (
                <button
                  className="radial-node"
                  key={d.id}
                  style={{ left: `${50 + Math.cos(a) * 32}%`, top: `${50 + Math.sin(a) * 36}%` }}
                  onClick={() => {
                    setSelected(d);
                    setFlow(null);
                  }}
                >
                  <DeviceIcon device={d} />
                  <strong>{d.name}</strong>
                  <QoEBadge band={d.band} small />
                </button>
              );
            })}
          </div>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Experiences</th>
                  <th>Throughput</th>
                  <th>QoE proxy</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {snapshot.devices.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <button className="table-device" onClick={() => setSelected(d)}>
                        <span className="table-device-icon">
                          <DeviceIcon device={d} size={17} />
                        </span>
                        <div>
                          <strong>{d.name}</strong>
                          <small>{d.ip?.replace(/\.\d+$/, ".•••")}</small>
                        </div>
                      </button>
                    </td>
                    <td>
                      <div className="table-classes">
                        {d.flows.length
                          ? d.flows.map((f) => (
                              <span
                                className={`traffic-class class-${f.cls}`}
                                key={f.id}
                                title={f.cls}
                              >
                                <TrafficIcon cls={f.cls} />
                              </span>
                            ))
                          : "Idle"}
                      </div>
                    </td>
                    <td className="mono">
                      {d.flows.reduce((n, f) => n + f.throughputMbps, 0).toFixed(1)} Mbps
                    </td>
                    <td>
                      <QoEBadge band={d.band} score={d.qoe} />
                    </td>
                    <td>
                      <Button
                        variant="ghost"
                        onClick={() => {
                          setSelected(d);
                          setFlow(null);
                        }}
                      >
                        Inspect <ArrowRight size={13} />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <div className="tree-legend">
        <span>Traffic classes</span>
        {capabilities?.classes.map((c) => (
          <span key={c}>
            <i className={`class-swatch swatch-${c}`} />
            {c}
          </span>
        ))}
        <span className="tree-legend-note">
          <Info size={12} /> Colour is class, not quality.
        </span>
      </div>
      <section className="card tree-explainer">
        <ShieldCheck size={21} />
        <div>
          <h3>We see patterns. Not your packets.</h3>
          <p>
            Classification uses timing, packet sizes and flow statistics. Low-confidence traffic
            stays unknown until there is enough evidence.
          </p>
        </div>
        <Link to="/app/about" className="text-link">
          How it works <ArrowRight size={13} />
        </Link>
      </section>
      <Modal
        open={!!current}
        onClose={() => {
          setSelected(null);
          setFlow(null);
        }}
        title={current?.name ?? "Device"}
        drawer
      >
        {current && (
          <>
            <div className="drawer-device">
              <span>
                <DeviceIcon device={current} size={27} />
              </span>
              <div>
                <h3>{current.person ?? "Unassigned"}</h3>
                <p>
                  {current.type} · {current.ip?.replace(/\.\d+$/, ".•••")}
                </p>
              </div>
              <QoEBadge band={current.band} score={current.qoe} />
            </div>
            <div className="drawer-section-heading">
              ACTIVE EXPERIENCES <span>{current.flows.length}</span>
            </div>
            {!current.flows.length && (
              <p className="padded-text">This device is idle. No QoE score is assigned.</p>
            )}
            {current.flows.map((f) => (
              <button
                className={`flow-list-item ${flow?.id === f.id ? "flow-selected" : ""}`}
                key={f.id}
                onClick={() => {
                  setFlow(f);
                  setCorrection(f.cls === "unknown" ? "game" : f.cls);
                }}
              >
                <span className={`traffic-class class-${f.cls}`}>
                  <TrafficIcon cls={f.cls} size={16} />
                </span>
                <div>
                  <strong>{f.cls}</strong>
                  <small>
                    {f.throughputMbps} Mbps · {percent(f.confidence)} confidence
                  </small>
                </div>
                <QoEBadge band={f.band} score={f.qoe} small />
                <ArrowRight size={12} />
              </button>
            ))}
            {flow && (
              <div className="flow-detail">
                <h3>
                  Classifier confidence <span>{percent(flow.confidence)}</span>
                </h3>
                <ProgressBar value={flow.confidence} />
                <p className="field-hint">
                  {flow.confidence < 0.75
                    ? "Not enough evidence for a reliable label. This flow stays unknown."
                    : "Traffic metadata supports this label. You can correct it below."}
                </p>
                <div className="metric-key-values">
                  {Object.entries(flow.features)
                    .slice(0, 4)
                    .map(([key, value]) => (
                      <div key={key}>
                        <span>{key}</span>
                        <strong>{value.toFixed(2)}</strong>
                      </div>
                    ))}
                </div>
                <div className="formula-callout">
                  <Info size={13} />
                  <p>
                    {capabilities?.formulas[flow.cls] ??
                      "Unknown class: no formula or experience is guessed."}
                  </p>
                </div>
                <label className="field-label">
                  Wrong label? Help the local classifier.
                  <select
                    aria-label="Correct traffic class"
                    value={correction}
                    onChange={(e) => setCorrection(e.target.value)}
                  >
                    {capabilities?.classes.map((c) => (
                      <option value={c} key={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  pending={task.isPending}
                  className="full-width"
                  onClick={() =>
                    task.mutate(
                      {
                        path: `/flows/${encodeURIComponent(flow.id)}/correct`,
                        body: { confirmed_class: correction, suggested_class: flow.cls },
                      },
                      {
                        onSuccess: () =>
                          setFlow({ ...flow, cls: correction as Flow["cls"], confidence: 1 }),
                      },
                    )
                  }
                >
                  <Pencil size={13} /> Correct label
                </Button>
              </div>
            )}
          </>
        )}
      </Modal>
    </>
  );
}
