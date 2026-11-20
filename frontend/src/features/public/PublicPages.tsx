import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowRight,
  Check,
  Copy,
  Terminal,
  ShieldCheck,
  Router,
  Server,
  Globe,
  Eye,
  EyeOff,
  CheckCircle2,
  ArrowUpRight,
  Wifi,
  Download,
  Lock,
  Zap,
} from "lucide-react";
import {
  Button,
  Badge,
  EmulatedBadge,
  useToasts,
  CardHeader,
  ErrorState,
} from "../../components/ui";
import { Topology } from "../../components/Topology";
import { useApi, transport, setTransport } from "../../lib/api";
import { DirectTransport } from "../../lib/transport/DirectTransport";
import { RelayTransport } from "../../lib/transport/RelayTransport";
import {
  getActiveAgent,
  listAgents,
  removeAgent,
  setActiveAgent,
  type StoredAgent,
} from "../../lib/transport/session";
import type { Capabilities, Snapshot } from "../../types";
import { Reveal } from "../../motion/primitives";
function PublicHeader() {
  return (
    <header className="public-header">
      <Link className="brand" to="/">
        <span className="brand-mark">
          <i />
          <i />
          <i />
        </span>
        <span>
          experience<span className="brand-light">budget</span>
        </span>
      </Link>
      <nav>
        <Link to="/welcome">Overview</Link>
        <Link to="/install">Install agent</Link>
        <Link to="/about">Our approach</Link>
        <Link className="btn btn-secondary" to="/app">
          Open console <ArrowUpRight size={15} />
        </Link>
      </nav>
    </header>
  );
}
export function Landing() {
  const { data: snapshot } = useApi<Snapshot>("/state/snapshot");
  const { data: cap } = useApi<Capabilities>("/capabilities");
  return (
    <div className="public-page">
      <PublicHeader />
      <main>
        <section className="landing-hero">
          <Reveal>
            <Badge tone="blue">
              <span className="status-dot" /> A little intelligence. A better connection.
            </Badge>
            <h1>
              Your bandwidth is shared.
              <br />
              <span>Your experience shouldn’t suffer.</span>
            </h1>
            <p>
              Work calls, movie nights, and the next big download.
              <br />
              Make room for what matters, without leaving anyone behind.
            </p>
            <div className="hero-actions">
              <Link className="btn btn-primary" to="/connect">
                Open the live console <ArrowRight size={16} />
              </Link>
              <Link className="btn btn-secondary" to="/install">
                <Terminal size={16} /> Install the agent
              </Link>
            </div>
            <div className="hero-note">
              <ShieldCheck size={14} /> Local-first. Privacy-safe. Fair by design.{" "}
              {snapshot?.agent.emulated && <EmulatedBadge />}
            </div>
          </Reveal>
        </section>
        <Reveal className="landing-network card">
          {snapshot && <Topology snapshot={snapshot} large />}
        </Reveal>
        <section className="landing-section">
          <div className="eyebrow">A CALMER KIND OF NETWORK</div>
          <h2>Not just faster. Better for everyone.</h2>
          <div className="feature-grid">
            {[
              {
                icon: Zap,
                title: "Understands the moment",
                text: "A call needs low latency. A download needs room to breathe. Intent-aware control knows the difference.",
              },
              {
                icon: ShieldCheck,
                title: "Fairness is not optional",
                text: "A guaranteed service floor keeps every device connected. Your priorities never leave someone with nothing.",
              },
              {
                icon: Eye,
                title: "Every decision, explained",
                text: "See what changed, why it changed, and whether it helped. A verifier rolls back changes that make things worse.",
              },
            ].map((f, i) => (
              <Reveal key={f.title} delay={i}>
                <article className="card feature-card">
                  <f.icon size={24} />
                  <h3>{f.title}</h3>
                  <p>{f.text}</p>
                </article>
              </Reveal>
            ))}
          </div>
        </section>
        <section className="landing-section how-it-works">
          <h2>Three steps to a more thoughtful connection.</h2>
          <div className="feature-grid">
            {[
              {
                n: "01",
                title: "Install",
                text: "Run the agent on an Ubuntu machine between your router and devices.",
              },
              {
                n: "02",
                title: "Pair",
                text: "Connect this console with the private key printed by your agent.",
              },
              {
                n: "03",
                title: "Make room",
                text: "See your network, set temporary priorities, and let adaptive control help.",
              },
            ].map((x) => (
              <article key={x.n}>
                <span className="step-number">{x.n}</span>
                <h3>{x.title}</h3>
                <p>{x.text}</p>
              </article>
            ))}
          </div>
        </section>
        <section className="honesty-strip">
          <ShieldCheck />
          <div>
            <h3>Designed with privacy. Built with honesty.</h3>
            <p>
              Metadata, not packet contents. QoE scores are proxies, not promises.{" "}
              {snapshot?.agent.emulated
                ? "This agent reports emulated mode and does not change your network traffic."
                : "The local agent is live and monitor-only: it measures your traffic but does not change it."}
            </p>
          </div>
          <Link to="/about">
            Read the limitations <ArrowRight size={16} />
          </Link>
        </section>
        {cap && (
          <p className="landing-seed">
            {snapshot?.agent.emulated
              ? `Emulated agent · Seed ${cap.seed} · No real traffic is shaped`
              : "Live local agent · Monitor-only · No real traffic is shaped"}
          </p>
        )}
      </main>
      <footer className="public-footer">
        <span>Experience Budget · A better share of the connection.</span>
        <Link to="/about">Privacy & limitations</Link>
      </footer>
    </div>
  );
}
export function Install() {
  const [tab, setTab] = useState("npx");
  const [copied, setCopied] = useState(false);
  const command =
    tab === "npx"
      ? "node cli/bin/eb-agent.js init"
      : "npm i -g ./cli && eb-agent init";
  async function copy() {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      useToasts.getState().push("Clipboard unavailable. Select and copy the command.", true);
    }
  }
  return (
    <div className="public-page">
      <PublicHeader />
      <main className="setup-page">
        <div className="eyebrow">YOUR NETWORK, YOUR AGENT</div>
        <h1>
          A small install.
          <br />A big difference.
        </h1>
        <p className="setup-intro">
          Keep the intelligence at home. Your Linux agent observes metadata,
          <br />
          protects experiences, and explains every decision.
        </p>
        <div className="card install-terminal">
          <div className="terminal-tabs">
            <button className={tab === "npx" ? "active" : ""} onClick={() => setTab("npx")}>
              Run with npx
            </button>
            <button className={tab === "global" ? "active" : ""} onClick={() => setTab("global")}>
              Global installation
            </button>
            <Badge>Placeholder CLI</Badge>
          </div>
          <div className="terminal-body">
            <span className="terminal-dollar">$</span>
            <code key={tab} className="typed-command">
              {command}
            </code>
            <button onClick={copy} aria-label="Copy install command">
              {copied ? <Check size={17} /> : <Copy size={17} />}
            </button>
          </div>
          <div className="terminal-output">
            <span>› Checking Ubuntu environment…</span>
            <span>› Initializing metadata-only classifier</span>
            <span>› Preparing fair allocation policy</span>
            <span className="terminal-key">Pairing key: generated by your agent</span>
            <span>Illustrative output · the CLI is run from this repository</span>
          </div>
        </div>
        <div className="setup-columns">
          <article className="card">
            <CardHeader title="Before you begin" />
            <ul className="check-list">
              <li>
                <CheckCircle2 />
                Ubuntu 22.04 or 24.04 LTS
              </li>
              <li>
                <CheckCircle2 />
                Node.js 20 or later
              </li>
              <li>
                <CheckCircle2 />
                sudo permission for Linux tc
              </li>
              <li>
                <CheckCircle2 />
                Agent sits between router and devices
              </li>
            </ul>
          </article>
          <article className="card">
            <CardHeader title="What stays on your machine" />
            <p className="padded-text">
              Flow metadata, local model inference, decisions and experiment results. No packet
              payloads, browsing history, or message contents.
            </p>
            <p className="padded-text small-text">
              Uninstall: <code>npm uninstall -g experience-budget-agent</code>
            </p>
          </article>
        </div>
        <div className="info-banner">
          <Server size={20} />
          <p>
            <strong>Windows or Docker?</strong> The agent runs in lab (emulated) mode only. It does
            not shape a Windows PC’s real traffic.
          </p>
        </div>
        <Link className="btn btn-primary" to="/connect">
          Continue to pairing <ArrowRight size={16} />
        </Link>
      </main>
    </div>
  );
}
export function Connect() {
  const navigate = useNavigate();
  const [parts, setParts] = useState(["", "", ""]);
  const [reveal, setReveal] = useState(false);
  const [remember, setRemember] = useState(true);
  const [choice, setChoice] = useState<"relay" | "direct">("relay");
  const [host, setHost] = useState("localhost:7443");
  const [stage, setStage] = useState(0);
  const [error, setError] = useState("");
  const [savedAgents, setSavedAgents] = useState<StoredAgent[]>([]);
  const [linkAgent, setLinkAgent] = useState("");
  const [linkPk, setLinkPk] = useState("");
  const [linkFp, setLinkFp] = useState("");
  const inputs = useRef<(HTMLInputElement | null)[]>([]);

  function splitKey(value: string): string[] {
    const compact = value
      .trim()
      .toUpperCase()
      .replace(/^EB-/, "")
      .replace(/[^A-Z0-9]/g, "")
      .slice(0, 12);
    return [compact.slice(0, 4), compact.slice(4, 8), compact.slice(8, 12)];
  }

  function absorbLink(value: string) {
    const raw = value.trim();
    if (!raw) return;
    let key = raw;
    try {
      const parsed = new URL(raw);
      key = parsed.searchParams.get("key") ?? raw;
      setLinkAgent(parsed.searchParams.get("agent") ?? "");
      const fragment = new URLSearchParams(parsed.hash.replace(/^#/, ""));
      setLinkPk(fragment.get("pk") ?? "");
      setLinkFp(fragment.get("fp") ?? "");
    } catch {
      /* a bare key is also accepted */
    }
    setParts(splitKey(key));
  }

  useEffect(() => {
    setSavedAgents(listAgents());
    try {
      setHost(`${location.hostname || "localhost"}:7443`);
    } catch {
      setHost("localhost:7443");
    }
    const url = new URL(location.href);
    const rawKey = url.searchParams.get("key");
    const agent = url.searchParams.get("agent");
    const fragment = new URLSearchParams(url.hash.replace(/^#/, ""));
    if (agent) setLinkAgent(agent);
    if (fragment.get("pk")) setLinkPk(fragment.get("pk") ?? "");
    if (fragment.get("fp")) setLinkFp(fragment.get("fp") ?? "");
    if (rawKey) {
      setParts(splitKey(rawKey));
      // The single-use key must not stay in the address bar or history.
      url.searchParams.delete("key");
      history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
  }, []);

  async function connect() {
    setError("");
    const key = "EB-" + parts.join("-");
    if (!/^EB-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(key)) {
      setError("Enter all 12 characters of your pairing key.");
      return;
    }
    try {
      setStage(1);
      let selected: RelayTransport;
      if (choice === "direct") {
        if (!host.trim()) throw new Error("Enter the agent's LAN host and port.");
        selected = new DirectTransport(linkFp || undefined, host.trim());
      } else {
        if (!linkAgent) {
          throw new Error(
            "This pairing link is missing the agent id. Run `eb-agent pair` and open the full link (it contains ?agent=…).",
          );
        }
        selected = new RelayTransport({
          mode: "relay",
          agentId: linkAgent,
          publicKey: linkPk || undefined,
          fingerprint: linkFp || undefined,
        });
      }
      setTransport(selected);
      setStage(2);
      await selected.connect(key);
      setStage(3);
      if (!remember && selected.agentId) {
        removeAgent(selected.agentId);
      }
      setSavedAgents(listAgents());
      useToasts.getState().push(
        selected.mode === "relay"
          ? "Connected through the relay with an end-to-end encrypted channel."
          : "Connected directly to your local agent.",
      );
      navigate("/app");
    } catch (e) {
      setStage(0);
      setError(e instanceof Error ? e.message : "Connection failed.");
      setSavedAgents(listAgents());
    }
  }

  async function switchAgent(agent: StoredAgent) {
    setError("");
    setStage(1);
    try {
      setActiveAgent(agent.agentId);
      const selected =
        agent.mode === "direct"
          ? new DirectTransport(agent.fingerprint, agent.httpUrl)
          : new RelayTransport({
              mode: "relay",
              relayUrl: agent.relayUrl,
              agentId: agent.agentId,
              publicKey: agent.publicKey,
              fingerprint: agent.fingerprint,
              accessToken: agent.accessToken,
              refreshToken: agent.refreshToken,
            });
      setTransport(selected);
      setStage(2);
      await selected.connect("");
      setStage(3);
      setSavedAgents(listAgents());
      navigate("/app");
    } catch (e) {
      setStage(0);
      setError(e instanceof Error ? e.message : "Could not reopen this agent.");
      setSavedAgents(listAgents());
    }
  }

  function forgetAgent(agentId: string) {
    const wasActive = getActiveAgent()?.agentId === agentId;
    removeAgent(agentId);
    if (wasActive) {
      void (transport as RelayTransport).disconnect?.();
    }
    setSavedAgents(listAgents());
  }

  return (
    <div className="public-page">
      <PublicHeader />
      <main className="connect-page">
        <div className="pair-illustration">
          <span>
            <Globe />
          </span>
          <i />
          <span>
            <Wifi />
          </span>
          <i />
          <span>
            <Server />
          </span>
        </div>
        <div className="eyebrow">ONE PRIVATE CONNECTION</div>
        <h1>Meet your network.</h1>
        <p>
          Paste the pairing key from your Linux agent.
          <br />
          We’ll take it from here.
        </p>
        <div className="card pairing-card">
          <div className="row-between">
            <h3>Your pairing key</h3>
            <Badge tone="blue">{linkAgent ? "Linked agent" : "Single use"}</Badge>
          </div>
          <div
            className={`key-input ${error ? "input-error" : ""}`}
            onPaste={(e) => {
              e.preventDefault();
              absorbLink(e.clipboardData.getData("text"));
            }}
          >
            <span>EB</span>
            {parts.map((part, i) => (
              <div key={i}>
                <b>–</b>
                <input
                  aria-label={`Key segment ${i + 1}`}
                  autoComplete="off"
                  type={reveal ? "text" : "password"}
                  placeholder="XXXX"
                  maxLength={4}
                  value={part}
                  ref={(node) => {
                    inputs.current[i] = node;
                  }}
                  onChange={(e) => {
                    const value = e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
                    setParts((p) => p.map((v, j) => (i === j ? value : v)));
                    if (value.length === 4) inputs.current[i + 1]?.focus();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Backspace" && !part) inputs.current[i - 1]?.focus();
                    if (e.key === "Enter") void connect();
                  }}
                />
              </div>
            ))}
            <button
              aria-label={reveal ? "Hide key" : "Reveal key"}
              onClick={() => setReveal(!reveal)}
            >
              {reveal ? <EyeOff size={17} /> : <Eye size={17} />}
            </button>
          </div>
          <p className="field-hint">
            Paste the whole link from <code>eb-agent pair</code> to include the agent id, public
            key, and TLS fingerprint. A bare key works for Direct LAN.
          </p>
          {linkAgent && (
            <p className="field-hint">
              Agent <code>{linkAgent}</code>
              {linkPk ? " · E2E public key pinned" : " · no public key in link (first-use trust)"}
              {linkFp ? " · TLS fingerprint pinned" : ""}
            </p>
          )}
          <label className="field-label">
            Connection method
            <select
              value={choice}
              onChange={(e) => setChoice(e.target.value as "relay" | "direct")}
            >
              <option value="relay">Relay · recommended</option>
              <option value="direct">Direct LAN</option>
            </select>
          </label>
          {choice === "direct" && (
            <>
              <label className="field-label">
                LAN host and port
                <input value={host} onChange={(e) => setHost(e.target.value)} />
              </label>
              <p className="field-hint">
                Browsers block plain-HTTP LAN hosts from an HTTPS page. Open this console over
                localhost, or serve the agent with a trusted certificate, to use Direct LAN.
              </p>
            </>
          )}
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember this agent on this browser
          </label>
          <p className="field-hint">
            Stores scoped session tokens, never the single-use pairing key. Use only on a trusted
            device.
          </p>
          {savedAgents.length > 0 && (
            <div className="saved-agents">
              <div className="row-between">
                <h3>Saved agents</h3>
                <Badge tone="blue">{savedAgents.length}</Badge>
              </div>
              {savedAgents.map((agent) => (
                <div className="saved-agent" key={agent.agentId}>
                  <div>
                    <strong>{agent.name || agent.agentId}</strong>
                    <div className="field-hint">
                      {agent.mode === "relay" ? "Relay · E2E" : "Direct LAN"} · {agent.agentId}
                    </div>
                  </div>
                  <div className="saved-agent-actions">
                    <Button
                      variant="secondary"
                      onClick={() => void switchAgent(agent)}
                      pending={stage > 0}
                    >
                      Open
                    </Button>
                    <Button variant="ghost" onClick={() => forgetAgent(agent.agentId)}>
                      Forget
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          <Button pending={stage > 0} onClick={connect} className="full-width">
            {stage === 1
              ? "Reaching agent…"
              : stage === 2
                ? "Authenticating…"
                : stage === 3
                  ? "Reading snapshot…"
                  : "Connect to my agent"}
            <ArrowRight size={16} />
          </Button>
          <div className="connection-steps">
            {["Reach agent", "Authenticate", "Read snapshot"].map((s, i) => (
              <span className={stage > i ? "step-done" : ""} key={s}>
                {stage > i ? <Check size={12} /> : <span className="step-dot" />}
                {s}
              </span>
            ))}
          </div>
        </div>
        <p className="connect-footer">
          <Lock size={13} /> Your key is never logged or left in the URL.
        </p>
      </main>
    </div>
  );
}

export function About({ embedded = false }: { embedded?: boolean }) {
  const { data: formulas, error } = useApi<Record<string, string>>("/qoe/formulas");
  const { data: snapshot } = useApi<Snapshot>("/state/snapshot");
  const emulated = snapshot?.agent.emulated ?? null;
  return (
    <div className={embedded ? "" : "public-page"}>
      {!embedded && <PublicHeader />}
      <main className={embedded ? "about-page" : "setup-page"}>
        <div className="eyebrow">TRANSPARENCY, BY DESIGN</div>
        <h1>A proxy. Not a promise.</h1>
        <p className="setup-intro">
          Experience Budget makes informed trade-offs, and shows its working.
        </p>
        <section className="card about-block">
          <CardHeader title="What you’re looking at">
            {emulated === true && <EmulatedBadge />}
          </CardHeader>
          {snapshot === undefined ? (
            <p>Connecting to the local agent…</p>
          ) : emulated ? (
            <p>
              This console is connected to an agent running in lab mode. It still reads real local
              measurements from this machine — kernel socket statistics, interface counters, and
              RTT/jitter/loss probes — but lab scenarios and A/B experiments are synthetic and
              labelled. No real traffic is shaped.
            </p>
          ) : (
            <p>
              This console is connected to the local agent running on this machine. It reads kernel
              socket statistics, interface counters, and probes RTT, jitter, and loss. It is
              monitor-only: no qdisc is changed. Classifier metrics and capacity come from live
              measurements; scenario-lab and A/B experiment results remain synthetic and are
              labelled as such.
            </p>
          )}
          <p>
            Real control requires an Ubuntu machine inline between the router and devices. Windows
            and Docker deployments do not shape a Windows PC’s traffic. Relay streams over the
            agent’s WebSocket; Direct LAN needs HTTPS or a localhost page.
          </p>
        </section>
        <section className="card about-block">
          <CardHeader
            title="The QoE proxy glossary"
            subtitle="Green ≥ 0.75 · Amber ≥ 0.50 · Red < 0.50 · No flows = idle"
          />
          {error && <ErrorState message={error.message} />}
          <div className="formula-list">
            {Object.entries(formulas ?? {}).map(([key, value]) => (
              <article key={key}>
                <h3>{key}</h3>
                <p>{value}</p>
              </article>
            ))}
          </div>
        </section>
        <section className="card about-block">
          <CardHeader title="Privacy & known limitations" />
          <p>
            Only flow metadata is modeled: timing, size, throughput, latency, jitter and packet
            loss. Unknown traffic stays unknown. Encrypted traffic can lower classification
            confidence. Missing metrics must not be interpreted as zero.
          </p>
          <p>
            The local hash chain is not a remote security guarantee. Browser storage can be cleared
            or modified. Confidence intervals from the synthetic lab harness are illustrative and
            do not establish a real-world performance benefit.
          </p>
          <p>
            Dependencies: React, Vite, Tailwind, Motion, Lucide, TanStack Query, Zustand, Zod,
            D3 Scale, Three.js, GSAP, Lenis, Noble Hashes, Seedrandom, Number Flow.
          </p>
        </section>
      </main>
    </div>
  );
}
