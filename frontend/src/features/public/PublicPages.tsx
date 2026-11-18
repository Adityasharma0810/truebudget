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
                Explore the live demo <ArrowRight size={16} />
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
                ? "This demo is fully emulated and does not change your network traffic."
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
              ? `Reproducible demo · Seed ${cap.seed} · No real traffic is shaped`
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
      ? "npx experience-budget-agent init"
      : "npm i -g experience-budget-agent && eb-agent init";
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
            <span className="terminal-key">Pairing key: EB-DEMO-HOME-4242</span>
            <span>Illustrative output · the CLI package is not published</span>
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
  const [remember, setRemember] = useState(false);
  const [choice, setChoice] = useState("relay");
  const [host, setHost] = useState("192.168.1.2:8080");
  const [stage, setStage] = useState(0);
  const [error, setError] = useState("");
  const inputs = useRef<(HTMLInputElement | null)[]>([]);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    const url = new URL(location.href);
    const raw = url.searchParams.get("key");
    if (raw) {
      setParts(raw.replace(/^EB-/i, "").toUpperCase().split("-").slice(0, 3));
      url.searchParams.delete("key");
      history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
    try {
      setSaved(!!localStorage.getItem("eb-key"));
    } catch {}
  }, []);
  function paste(text: string) {
    let key = text.trim();
    try {
      const u = new URL(text);
      key = u.searchParams.get("key") ?? key;
    } catch {}
    const p = key
      .toUpperCase()
      .replace(/^EB-/, "")
      .replace(/[^A-Z0-9]/g, "");
    setParts([p.slice(0, 4), p.slice(4, 8), p.slice(8, 12)]);
  }
  async function connect() {
    setError("");
    const key = "EB-" + parts.join("-");
    if (!/^EB-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$/.test(key)) {
      setError("Enter all 12 characters of your pairing key.");
      return;
    }
    try {
      setStage(1);
      await new Promise((r) => setTimeout(r, 400));
      setStage(2);
      // Select the real transport based on the user's connection method.
      if (choice === "direct") {
        setTransport(new DirectTransport(undefined, host));
      } else {
        setTransport(new RelayTransport());
      }
      await transport.connect(key);
      setStage(3);
      await new Promise((r) => setTimeout(r, 400));
      if (remember)
        try {
          localStorage.setItem("eb-key", key);
        } catch {}
      navigate("/app");
      useToasts.getState().push("Connected to your local agent.");
    } catch (e) {
      setStage(0);
      setError(e instanceof Error ? e.message : "Connection failed.");
    }
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
            <EmulatedBadge />
          </div>
          <div
            className={`key-input ${error ? "input-error" : ""}`}
            onPaste={(e) => {
              e.preventDefault();
              paste(e.clipboardData.getData("text"));
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
            Demo key:{" "}
            <button onClick={() => paste("EB-DEMO-HOME-4242")}>Use EB-DEMO-HOME-4242</button>
          </p>
          <label className="field-label">
            Connection method
            <select value={choice} onChange={(e) => setChoice(e.target.value)}>
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
                HTTPS browsers may block plain-HTTP LAN hosts. This demo uses the in-browser mock
                regardless of host.
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
            Stores the pairing key locally. Use only on a trusted device.
          </p>
          {saved && (
            <button
              className="text-link"
              onClick={() => {
                try {
                  localStorage.removeItem("eb-key");
                } catch {}
                setSaved(false);
              }}
            >
              Forget remembered agent
            </button>
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
  const emulated = snapshot?.agent.emulated ?? true;
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
            {emulated && <EmulatedBadge />}
          </CardHeader>
          {emulated ? (
            <p>
              This is a fully emulated, browser-local demonstration. No Linux agent is running, no
              network packets are inspected, and no real traffic is shaped. Scenarios, classifier
              metrics, history, and experiments are synthetic. Demo seed: <strong>4242</strong>.
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
            and Docker demonstrations do not shape a Windows PC’s traffic. Relay and Direct LAN
            transports are unimplemented stubs.
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
            or modified. Confidence intervals in mock experiments are illustrative and do not
            establish a real-world performance benefit.
          </p>
          <p>
            Dependencies: React (MIT), Vite (MIT), Tailwind (MIT), Motion (MIT), Lucide (ISC),
            TanStack Query (MIT), Zustand (MIT), Zod (MIT), D3 (ISC), Noble Hashes (MIT), Seedrandom
            (MIT), Number Flow (MIT).
          </p>
        </section>
      </main>
    </div>
  );
}
