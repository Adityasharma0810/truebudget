import { useEffect, useState, useContext, createContext, type ReactNode } from "react";
import { NavLink, Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "motion/react";
import {
  LayoutDashboard,
  GitFork,
  ChartNoAxesCombined,
  Laptop,
  Zap,
  History,
  FlaskConical,
  ChartColumnIncreasing,
  BrainCircuit,
  Settings2,
  ShieldCheck,
  CircleHelp,
  ChevronsUpDown,
  ChevronRight,
  ArrowUpRight,
  ChevronDown,
  Bell,
  Search,
  Radio,
  Wifi,
  SlidersHorizontal,
  HeartPulse,
  CalendarClock,
  Network,
  Workflow,
  Command,
  LogOut,
  X,
  LockKeyhole,
  Plus,
  ArrowRight,
  Check,
  Sparkles,
  Activity,
  Globe,
} from "lucide-react";
import { useApi, useSnapshot, queryClient } from "../lib/api";
import type { Snapshot, Settings, Capabilities, Intent } from "../types";
import {
  Badge,
  EmulatedBadge,
  Button,
  IconButton,
  ConfirmDialog,
  Modal,
  useTask,
  Skeletons,
  ErrorState,
  LockNote,
  useToasts,
} from "../components/ui";
import { tiers, useMotionPreference, useEffectiveTier } from "../motion/store";
import { Reveal } from "../motion/primitives";
import { remainingSeconds } from "../lib/qoe";
import { dateTime } from "../lib/format";
const navGroups = [
  {
    label: "WORKSPACE",
    items: [
      { path: "/app", name: "Overview", icon: LayoutDashboard },
      { path: "/app/tree", name: "Usage tree", icon: GitFork },
      { path: "/app/metrics", name: "Live metrics", icon: ChartNoAxesCombined },
      { path: "/app/devices", name: "Devices", icon: Laptop },
    ],
  },
  {
    label: "EXPERIENCE CONTROL",
    items: [
      { path: "/app/intent", name: "Intent control", icon: Zap },
      { path: "/app/decisions", name: "Decision log", icon: History },
      { path: "/app/conflicts", name: "Conflicts", icon: Workflow, flag: "conflicts" },
      { path: "/app/health", name: "Network health", icon: HeartPulse, flag: "health" },
    ],
  },
  {
    label: "EXPLORE & LEARN",
    items: [
      { path: "/app/lab", name: "Scenario lab", icon: FlaskConical, tag: "LAB" },
      { path: "/app/experiments", name: "Experiments", icon: ChartColumnIncreasing },
      { path: "/app/ml", name: "ML lab", icon: BrainCircuit },
      { path: "/app/demand", name: "Demand forecast", icon: CalendarClock, flag: "demand" },
      { path: "/app/links", name: "Links", icon: Network, flag: "links" },
    ],
  },
  {
    label: "MANAGE",
    items: [
      { path: "/app/settings", name: "Settings & safety", icon: Settings2 },
      { path: "/app/agent", name: "Agent & security", icon: ShieldCheck },
    ],
  },
];
interface ConsoleContextValue {
  snapshot: Snapshot;
  settings: Settings | undefined;
  capabilities: Capabilities | undefined;
  openBoost: () => void;
  openSafe: () => void;
  offline: boolean;
  stale: boolean;
}
const ConsoleContext = createContext<ConsoleContextValue | null>(null);
export const useConsole = () => {
  const value = useContext(ConsoleContext);
  if (!value) throw new Error("Console context is missing");
  return value;
};
export function Layout() {
  const snapshotQuery = useSnapshot();
  const { data: settings } = useApi<Settings>("/settings");
  const { data: cap } = useApi<Capabilities>("/capabilities");
  const location = useLocation();
  const navigate = useNavigate();
  const [boost, setBoost] = useState(false);
  const [safe, setSafe] = useState(false);
  const [palette, setPalette] = useState(false);
  const [agentMenu, setAgentMenu] = useState(false);
  const [notifications, setNotifications] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);
  const [lastChanged, setLastChanged] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const task = useTask("Network safety state updated");
  const tier = useEffectiveTier();
  const { setTier, notice } = useMotionPreference();
  useEffect(() => {
    setLastChanged(Date.now());
  }, [snapshotQuery.data?.ts]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  useEffect(() => {
    if (notice) useToasts.getState().push(notice);
  }, [notice]);
  useEffect(() => {
    setMobileMenu(false);
    window.scrollTo({ top: 0, behavior: "instant" });
  }, [location.pathname]);
  const snapshot = snapshotQuery.data;
  const offline = snapshotQuery.isError;
  const stale =
    !!snapshot && now - lastChanged > Math.max(6000, snapshot.agent.intervalSeconds * 2000);
  const current =
    navGroups.flatMap((g) => g.items).find((x) => x.path === location.pathname)?.name ??
    "About & limitations";
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenu ? "sidebar-open" : ""}`}>
        <Link className="brand sidebar-brand" to="/app">
          <span className="brand-mark">
            <i />
            <i />
            <i />
          </span>
          <span>
            experience<span className="brand-light">budget</span>
            <small>A BETTER SHARE.</small>
          </span>
        </Link>
        <div className="agent-switch-wrap">
          <button
            className="agent-switch"
            onClick={() => setAgentMenu(!agentMenu)}
            aria-expanded={agentMenu}
          >
            <span className="home-icon">
              <Wifi size={17} />
            </span>
            <span>
              <strong>{snapshot?.agent.name ?? "Home network"}</strong>
              <small>Personal workspace</small>
            </span>
            <ChevronsUpDown size={12} />
          </button>
          {agentMenu && (
            <div className="agent-menu popover">
              <strong>Your agents</strong>
              <button
                onClick={() => {
                  setAgentMenu(false);
                  navigate("/app");
                }}
              >
                <Check size={13} /> Home network <EmulatedBadge />
              </button>
              <button onClick={() => navigate("/connect")}>
                <Plus size={13} /> Pair another agent
              </button>
              <button
                onClick={() => {
                  try {
                    localStorage.removeItem("eb-key");
                  } catch {}
                  navigate("/connect");
                }}
              >
                <LogOut size={13} /> Forget & disconnect
              </button>
            </div>
          )}
        </div>
        <nav className="sidebar-nav" aria-label="Main navigation">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-group-label">{group.label}</div>
              {group.items
                .filter((item) => !("flag" in item) || cap?.flags[item.flag!])
                .map((item) => (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === "/app"}
                    className={({ isActive }) => `nav-item ${isActive ? "nav-active" : ""}`}
                  >
                    {({ isActive }) => (
                      <>
                        {isActive && <motion.span layoutId="activeNav" className="nav-active-bg" />}
                        <item.icon size={16} />
                        <span>{item.name}</span>
                        {"tag" in item && <span className="nav-tag">{item.tag}</span>}
                        {"flag" in item && (
                          <span className="experimental-dot" title="Experimental capability" />
                        )}
                      </>
                    )}
                  </NavLink>
                ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="agent-status-card">
            <span className="agent-status-icon">
              <ServerIcon />
            </span>
            <div>
              <strong>Agent is {offline ? "offline" : "connected"}</strong>
              <small>
                Ubuntu 24.04 <span>·</span> v{snapshot?.agent.version ?? "—"}
              </small>
            </div>
            <span className={`connection-dot ${offline ? "offline-dot" : ""}`} />
          </div>
          <Link className="sidebar-help" to="/app/about">
            <CircleHelp size={14} /> About & limitations <ArrowUpRight size={12} />
          </Link>
          <div className="sidebar-footer">
            Made for a better connection.
            <span>
              LOCAL-FIRST <i /> PRIVATE BY DESIGN
            </span>
          </div>
        </div>
      </aside>
      {mobileMenu && (
        <button
          className="mobile-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileMenu(false)}
        />
      )}
      <div className="app-main">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>Workspace</span>
            <ChevronRight size={12} />
            <strong>{current}</strong>
          </div>
          <div className="topbar-right">
            <button
              className="search-trigger"
              onClick={() => setPalette(true)}
              aria-label="Search console"
            >
              <Search size={15} />
              <span>Search anything…</span>
              <kbd>⌘ K</kbd>
            </button>
            <span className="topbar-divider" />
            <div className="motion-select">
              <Sparkles size={13} />
              <select
                aria-label="Motion preference"
                value={tier}
                onChange={(e) => setTier(e.target.value as typeof tier)}
              >
                {tiers.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </div>
            <div className="notification-wrap">
              <IconButton label="Notifications" onClick={() => setNotifications(!notifications)}>
                <Bell size={17} />
                <i className="notification-dot" />
              </IconButton>
              {notifications && (
                <div className="notification-popover popover">
                  <h3>All caught up</h3>
                  <p>
                    Your agent is protecting the connection. New safety alerts will appear here.
                  </p>
                  <Link to="/app/decisions" onClick={() => setNotifications(false)}>
                    View decision log <ArrowRight size={13} />
                  </Link>
                </div>
              )}
            </div>
            <button
              className="avatar"
              onClick={() => navigate("/app/agent")}
              aria-label="Open agent and security"
            >
              M
            </button>
          </div>
        </header>
        <main className={`page-content ${offline ? "data-offline" : ""}`} id="main-content">
          {snapshot ? (
            <ConsoleContext.Provider
              value={{
                snapshot,
                settings,
                capabilities: cap,
                openBoost: () => setBoost(true),
                openSafe: () => setSafe(true),
                offline,
                stale,
              }}
            >
              {(offline || stale) && (
                <div className="freshness-banner" role="status">
                  <Wifi size={15} />
                  {offline
                    ? "Agent offline. Showing last known data. Controls will retry the connection."
                    : "Data is stale. Waiting for a fresh agent snapshot."}
                  <Button variant="ghost" onClick={() => void snapshotQuery.refetch()}>
                    Retry
                  </Button>
                </div>
              )}
              {snapshot.mode === "safe_state" && (
                <div className="safe-banner">
                  <ShieldCheck size={18} />
                  <div>
                    <strong>Safe state is active</strong>
                    <p>
                      {snapshot.alerts[0]?.message ??
                        "Temporary priorities are cancelled. The agent is monitoring with a conservative plan; no qdisc is changed."}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    pending={task.isPending}
                    onClick={() =>
                      task.mutate({ path: "/safe-state/reset", body: { restore: true } })
                    }
                  >
                    Restore adaptive mode
                  </Button>
                </div>
              )}
              <Reveal key={location.pathname}>
                <Outlet />
              </Reveal>
              <div className="console-footer">
                <span>
                  <ShieldCheck size={12} /> Privacy-safe metadata. No packet payloads.
                </span>
                <span>
                  QoE scores are proxies <i />{" "}
                  <Link to="/app/about">
                    How we calculate them <ArrowUpRight size={10} />
                  </Link>
                </span>
              </div>
              <BoostDialog open={boost} onClose={() => setBoost(false)} />
            </ConsoleContext.Provider>
          ) : snapshotQuery.isError ? (
            <ErrorState
              message={snapshotQuery.error.message}
              retry={() => void snapshotQuery.refetch()}
            />
          ) : (
            <Skeletons />
          )}
        </main>
      </div>
      <div className="mobile-nav">
        <Link to="/app">
          <LayoutDashboard size={19} />
          <span>Overview</span>
        </Link>
        <Link to="/app/tree">
          <GitFork size={19} />
          <span>Usage tree</span>
        </Link>
        <button onClick={() => setBoost(true)}>
          <Zap size={19} />
          <span>Boost</span>
        </button>
        <Link to="/app/lab">
          <FlaskConical size={19} />
          <span>Lab</span>
        </Link>
        <button onClick={() => setMobileMenu(true)}>
          <SlidersHorizontal size={19} />
          <span>More</span>
        </button>
      </div>
      <ConfirmDialog
        open={safe}
        onClose={() => setSafe(false)}
        pending={task.isPending}
        onConfirm={() =>
          task.mutate({ path: "/safe-state/reset" }, { onSuccess: () => setSafe(false) })
        }
      />
      <CommandPalette open={palette} onClose={() => setPalette(false)} capabilities={cap} />
    </div>
  );
}
function ServerIcon() {
  return <Radio size={15} />;
}
export function FreshnessPill() {
  const { snapshot, offline, stale } = useConsole();
  return (
    <span
      className={`freshness-pill ${offline ? "fresh-offline" : stale ? "fresh-stale" : ""}`}
      title={`Last snapshot ${dateTime(snapshot.ts)} · emulated clock`}
    >
      <span className="freshness-ring" key={snapshot.ts}>
        <span />
      </span>
      {offline ? "Offline" : stale ? "Stale" : "Live"}
      <span className="freshness-divider" />
      Updated just now
    </span>
  );
}
export function BoostDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { snapshot, settings, capabilities } = useConsole();
  const [device, setDevice] = useState(snapshot.devices[0]?.id ?? "");
  const [cls, setCls] = useState("call");
  const [duration, setDuration] = useState("3600");
  const [goal, setGoal] = useState("Prioritize my work call");
  const task = useTask("Boost is active. Your fairness floor stays protected.");
  return (
    <Modal open={open} onClose={onClose} title="Make room for what matters">
      <div className="boost-dialog-intro">
        <span>
          <Zap size={23} />
        </span>
        <p>
          A little extra priority, just when you need it.
          <br />
          Your network returns to normal automatically.
        </p>
      </div>
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
      <div className="form-row">
        <label className="field-label">
          Experience
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
        <label className="field-label">
          For how long?
          <select value={duration} onChange={(e) => setDuration(e.target.value)}>
            <option value="900">15 minutes</option>
            <option value="1800">30 minutes</option>
            <option value="3600">1 hour</option>
            <option value="7200">2 hours</option>
          </select>
        </label>
      </div>
      <label className="field-label">
        Your intent
        <input
          value={goal}
          minLength={3}
          maxLength={120}
          onChange={(e) => setGoal(e.target.value)}
        />
      </label>
      <div className="boost-safety">
        <LockKeyhole size={17} />
        <div>
          <strong>Nobody gets left behind.</strong>
          <p>
            Every active device keeps at least {settings?.floorMbps ?? 2} Mbps. Bulk downloads may
            slow temporarily.
          </p>
        </div>
      </div>
      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          Not now
        </Button>
        <Button
          pending={task.isPending}
          disabled={goal.trim().length < 3}
          onClick={() =>
            task.mutate(
              {
                path: "/intents",
                body: {
                  target: { deviceId: device, cls },
                  goal,
                  priority: "high",
                  durationSeconds: Number(duration),
                  allowedTradeoffs: ["bulk throughput"],
                  minServiceFloor: true,
                },
              },
              { onSuccess: onClose },
            )
          }
        >
          <Zap size={14} /> Start boost
        </Button>
      </div>
    </Modal>
  );
}
function CommandPalette({
  open,
  onClose,
  capabilities,
}: {
  open: boolean;
  onClose: () => void;
  capabilities?: Capabilities;
}) {
  const [search, setSearch] = useState("");
  const [index, setIndex] = useState(0);
  const navigate = useNavigate();
  const items = navGroups
    .flatMap((g) => g.items)
    .filter(
      (i) =>
        (!("flag" in i) || capabilities?.flags[i.flag!]) &&
        i.name.toLowerCase().includes(search.toLowerCase()),
    );
  useEffect(() => setIndex(0), [search]);
  function go(path: string) {
    if ("startViewTransition" in document)
      (
        document as Document & { startViewTransition: (fn: () => void) => void }
      ).startViewTransition(() => navigate(path));
    else navigate(path);
    onClose();
  }
  return (
    <Modal open={open} onClose={onClose} title="Find your flow">
      <div className="palette-search">
        <Search size={18} />
        <input
          aria-label="Search pages"
          placeholder="Where would you like to go?"
          autoComplete="off"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIndex((i) => Math.min(items.length - 1, i + 1));
            }
            if (e.key === "ArrowUp") {
              e.preventDefault();
              setIndex((i) => Math.max(0, i - 1));
            }
            if (e.key === "Enter" && items[index]) go(items[index].path);
          }}
        />
      </div>
      <div className="palette-results">
        {items.map((item, i) => (
          <button
            key={item.path}
            className={index === i ? "selected" : ""}
            onClick={() => go(item.path)}
          >
            <item.icon size={17} />
            {item.name}
            <ArrowUpRight size={14} />
          </button>
        ))}
        {!items.length && <p>No matching pages. Try “metrics” or “intent”.</p>}
      </div>
      <p className="palette-hint">
        ↑ ↓ to navigate <span>↵ to open · Esc to close</span>
      </p>
    </Modal>
  );
}
