# Experience Budget

Experience Budget is an intent-aware QoS/QoE controller for home broadband. It
consists of a **Python Linux agent** (real-time traffic classification, capacity
estimation, QoE scoring, max-min policy, and a monitor-only control loop on this
build) and a **React web console** that connects to the agent's local HTTP +
WebSocket API in real time.

## Architecture

| Component | Language | What it does |
|-----------|----------|--------------|
| Agent daemon (`eb-agentd`) | Python + FastAPI | Reads `/proc/net/dev` and `/proc/net/nf_conntrack`, classifies flows, estimates WAN capacity (Kalman + CUSUM), scores per-device QoE, runs max-min policy, serves `/api/v1` |
| CLI (`eb-agent`) | Node.js | `init`, `pair`, `start`, `stop`, `status`, `doctor`, `config`, `mode`, `sessions`, `rotate-keys`, `update`, `safe-reset`, `export-diagnostics`, `uninstall`, `lab`, `logs` |
| Web console | React + Vite | Real-time dashboard: devices, flows, QoE, intents, decisions, settings, health diagnosis, conflicts, experiments, federated status, ask/rag |
| Transport | HTTP + WebSocket | `RelayTransport` talks to the agent's local API and stream; `DirectTransport` targets an HTTPS LAN host and enforces a secure page context |

## Quick start

### 1. Install Python dependencies

```sh
cd /home/vboxuser/Desktop/truebudget
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e "agent[dev]"
```

### 2. Install Node.js dependencies

```sh
cd frontend
pnpm install
cd ..
```

### 3. Start the agent daemon

```sh
cd /home/vboxuser/Desktop/truebudget
EB_DATA_DIR=/tmp/eb-data node cli/bin/eb-agent.js start
```

This launches the FastAPI API server on `http://localhost:7443` with the real-time
control loops running. Live telemetry comes from `/proc/net/dev`,
`/proc/net/nf_conntrack`, `ss`, and `ping`. Scenario targets and A/B experiments
run in the synthetic lab and are labelled as such; they never masquerade as
production measurements.

Open another terminal and verify:

```sh
curl http://localhost:7443/api/v1/capabilities
curl http://localhost:7443/api/v1/state/snapshot
curl http://localhost:7443/api/v1/devices
```

### 4. Connect the web console

Local read-only access does not require a pairing key in this build. To generate
a local pairing secret anyway:

```sh
node cli/bin/eb-agent.js pair --site-url https://localhost:8443
```

This prints a key like `EB-XXXX-XXXX-XXXX` and a link with a `#pk=` fragment.
Open the web console (see step 5) and connect with **Relay** (local API) or
**Direct LAN** (HTTPS host). Pairing enforcement is not enabled yet, so the key
is accepted but not required.

### 5. Start the web console

```sh
cd frontend
pnpm dev
```

The console connects to `http://localhost:7443/api/v1` in real time. By default
`VITE_TRANSPORT=relay`; set `VITE_TRANSPORT=direct` for direct HTTPS LAN.

## CLI commands

```sh
# Pairing & identity
node cli/bin/eb-agent.js init --site-url https://localhost:8443
node cli/bin/eb-agent.js pair --site-url https://localhost:8443

# Agent lifecycle
node cli/bin/eb-agent.js start            # start API daemon on :7443
node cli/bin/eb-agent.js stop             # stop the daemon
node cli/bin/eb-agent.js status           # show daemon status
node cli/bin/eb-agent.js doctor           # verify CAP_NET_ADMIN, modules, binaries
node cli/bin/eb-agent.js safe-reset       # enter safe state (cancel intents, conservative shaping)

# Configuration
node cli/bin/eb-agent.js config get mode
node cli/bin/eb-agent.js config get shaping_factor
node cli/bin/eb-agent.js mode set lab     # gateway | bridge | lab | monitor

# Security & maintenance
node cli/bin/eb-agent.js sessions         # list active sessions
node cli/bin/eb-agent.js rotate-keys      # rotate agent agreement keys
node cli/bin/eb-agent.js update           # check for updates
node cli/bin/eb-agent.js export-diagnostics
node cli/bin/eb-agent.js uninstall --purge

# Diagnostics
node cli/bin/eb-agent.js lab              # run reproducible netns lab
node cli/bin/eb-agent.js logs             # show agent logs
```

## API endpoints (real-time data)

All endpoints serve live data from the agent core + SQLite store:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/capabilities` | GET | Supported classes, scenarios, formulas |
| `/api/v1/state/snapshot` | GET | Full real-time snapshot (capacity, QoE, devices, flows) |
| `/api/v1/stream` | WS | Live tick/state stream |
| `/api/v1/devices` | GET | Known devices |
| `/api/v1/devices/{id}` | PATCH | Update device name/mac/IP |
| `/api/v1/flows` | GET | Active flows (from conntrack) |
| `/api/v1/flows/{id}/correct` | POST | Label a flow's traffic class |
| `/api/v1/intents` | GET/POST | List/create intents (QoE boosts with floors) |
| `/api/v1/intents/{id}` | DELETE | Cancel an intent |
| `/api/v1/decisions` | GET | Hash-chained decision log |
| `/api/v1/settings` | GET/PUT | Agent mode, shaping factor, intervals |
| `/api/v1/qoe/formulas` | GET | QoE formula descriptions |
| `/api/v1/agent/info` | GET | Version, uptime, mode |
| `/api/v1/agent/logs` | GET | Agent logs |
| `/api/v1/agent/update-check` | POST | Check for updates |
| `/api/v1/health/diagnosis` | GET | Ranked health hypotheses + recommendations |
| `/api/v1/conflicts` | GET | Capacity conflicts with trade-off options |
| `/api/v1/conflicts/{id}/accept\|adjust\|cancel` | POST | Resolve a conflict |
| `/api/v1/demand/schedules` | GET/POST/DELETE | Demand forecast schedules |
| `/api/v1/scenarios/{id}` | POST | Start a scenario |
| `/api/v1/scenarios/stop` | POST | Stop all scenarios |
| `/api/v1/experiments` | GET/POST | A/B experiment runner (unmanaged vs CAKE vs EB) |
| `/api/v1/experiments/{id}/report` | GET | Experiment results |
| `/api/v1/federated/status` | GET | Federated learning status |
| `/api/v1/federated/opt-in\|opt-out` | POST | Federated opt-in/out |
| `/api/v1/ask` | POST | Local knowledge Q&A |
| `/api/v1/dev/control` | GET/PATCH | Dev fault simulation (offline, stale, slow, rateLimit, speed, seed) |
| `/api/v1/dev/reset` | DELETE | Reset dev control flags |

## Lab (Phase 1 — reproducible, no root required)

```sh
make agent-install
make lab-demo          # 4 devices x 2 controllers -> reports/lab/baseline.csv
make reset-lab
make test-agent        # fast unit tests
make test-netns        # real veth/netem/CAKE integration via unshare
```

The lab runs inside an unprivileged user+network namespace (`unshare -Urn`) —
no `sudo` or `CAP_NET_ADMIN` on the host is required. Real `tc`/`netem`/CAKE
shaping is applied inside the namespace.

## Verification

From the repository root:

```sh
# Frontend
cd frontend && pnpm install && pnpm lint && pnpm typecheck && pnpm test && pnpm build

# Contracts
cd /home/vboxuser/Desktop/truebudget && node packages/contracts/scripts/drift-check.mjs

# Agent (fast unit tests, no netns)
cd agent && ../.venv/bin/python -m pytest -m "not netns"

# Agent (netns integration, via unshare)
cd agent && unshare -Urn env PYTHONPATH=agent ../.venv/bin/python -m pytest -m netns
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `EB_DATA_DIR` | `/var/lib/eb-agent` | Agent SQLite data directory (use `/tmp/eb-data` for non-root) |
| `EB_PYTHON` | `.venv/bin/python` | Python interpreter for the CLI |
| `VITE_TRANSPORT` | `relay` | Frontend transport: `relay` or `direct` |
| `VITE_RELAY_URL` | `ws://localhost:7443/stream` | Relay WebSocket URL |
| `VITE_AGENT_HTTP_URL` | `http://localhost:7443` | Agent HTTP API URL |
| `VITE_MOCK_SPEED` | `10` | Dev control simulation speed |

## Data integrity

Production telemetry is **measured live** — metrics come from `/proc/net/dev`,
`/proc/net/nf_conntrack`, `ss`, and `ping`. No mock transport ships in the
console. Synthetic data is confined to the lab: scenario targets, the
deterministic classifier training set, and seeded A/B experiment results are
labelled in the UI. The dev control panel (`/api/v1/dev/control`) simulates fault
conditions against the live backend for testing; it does not fabricate metrics.

## Project structure

```
agent/              # Python agent (FastAPI, core loops, store, security, enforcer, lab)
  eb_agent/
  tests/
cli/                # Node.js CLI (bin/eb-agent.js)
frontend/           # React + Vite web console
  src/
    lib/api/        # API client + transport (RelayTransport, DirectTransport)
    lib/transport/
    features/       # Home, Devices, Intents, Advanced, Lab, Experiments, PublicPages
    mock/           # deleted (was browser-local simulation)
packages/contracts/ # API contract schemas + drift check
Dockerfile          # Agent container
docker-compose.yml  # Agent + frontend orchestration
```

See [`task.md`](./task.md) for the full phase plan (0–10) and implementation gates.
