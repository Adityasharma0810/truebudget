import { useState } from "react";
import { Router, Server, ArrowRight, Globe, Maximize2, ShieldCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { DeviceIcon, QoEBadge, TrafficIcon } from "./ui";
import type { Snapshot, Device } from "../types";
export function Topology({
  snapshot,
  large = false,
  onDevice,
}: {
  snapshot: Snapshot;
  large?: boolean;
  onDevice?: (device: Device) => void;
}) {
  const navigate = useNavigate();
  const [active, setActive] = useState<string | null>(null);
  return (
    <div className={`topology ${large ? "topology-large" : ""}`}>
      <div className="network-origin">
        <div className="origin-node">
          <Globe size={23} />
        </div>
        <div>
          <strong>Internet</strong>
          <span>{snapshot.link.capacityMbps.toFixed(1)} Mbps available</span>
        </div>
        <div className="packet-connector">
          <i />
          <i />
          <ArrowRight size={13} />
        </div>
        <div className="agent-node">
          <div className="agent-icon">
            <Server size={24} />
            <span />
          </div>
          <div>
            <strong>EB agent</strong>
            <span>
              Ubuntu Linux <ShieldCheck size={10} />
            </span>
          </div>
        </div>
        <span className="agent-mode">
          <span /> {snapshot.mode === "safe_state" ? "Safe state" : "Adaptive"}
        </span>
      </div>
      <div className="topology-trunk">
        <svg viewBox="0 0 800 50" preserveAspectRatio="none" aria-hidden="true">
          <path d="M400,0 V15 Q400,24 390,24 H90 Q80,24 80,34 V50 M400,24 V50 M400,24 H710 Q720,24 720,34 V50 M240,24 V50 M560,24 V50" />
          <path
            className="flowing-path"
            d="M400,0 V24 H80 V50 M400,24 H720 V50 M240,24 V50 M400,24 V50 M560,24 V50"
          />
        </svg>
      </div>
      <div className="topology-devices">
        {snapshot.devices.map((d) => (
          <button
            key={d.id}
            onMouseEnter={() => setActive(d.id)}
            onMouseLeave={() => setActive(null)}
            className={`topology-device ${d.band === "idle" ? "is-idle" : ""} ${active === d.id ? "node-hover" : ""}`}
            onClick={() => (onDevice ? onDevice(d) : navigate("/app/tree?device=" + d.id))}
          >
            <span className="device-node-icon">
              <DeviceIcon device={d} size={23} />
              <i className={`node-status node-${d.band}`} />
            </span>
            <strong>{d.name}</strong>
            <span className="node-subtitle">
              {d.flows.length
                ? `${d.flows.reduce((s, f) => s + f.throughputMbps, 0).toFixed(1)} Mbps`
                : "No active traffic"}
            </span>
            <div className="traffic-dots">
              {d.flows.map((f) => (
                <span
                  key={f.id}
                  className={`traffic-class class-${f.cls}`}
                  title={`${f.cls} · ${Math.round(f.confidence * 100)}% confidence`}
                >
                  <TrafficIcon cls={f.cls} />
                  {large && f.cls}
                </span>
              ))}
            </div>
            {large && <QoEBadge band={d.band} score={d.qoe} small />}
          </button>
        ))}
      </div>
      <div className="topology-footer">
        <span>
          <Router size={13} /> Router <ArrowRight size={11} /> EB agent <ArrowRight size={11} />{" "}
          Your devices
        </span>
        <span>
          <ShieldCheck size={13} /> Metadata only. Always.
        </span>
      </div>
    </div>
  );
}
