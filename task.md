# Experience Budget — Master Task Plan (task.md)

| | |
|---|---|
| **Project** | Experience Budget (EB) — intent-aware adaptive QoS/QoE controller for home broadband |
| **Version of this plan** | 1.0 (2026-11-15) |
| **Status** | LIVE LOCAL VALIDATION — agent and console are wired to real telemetry; synthetic data is confined to the lab; host enforcement is not enabled. |
| **Working dir** | `/home/vboxuser/Desktop/truebudget` (Linux) |
| **Rule** | Never claim a feature is done unless it has a passing test or a reproducible demo command. |

**How to use this file:** Phase 0 → Phase 10 in order. Each phase has: Goal, Work items (checkboxes), Tests, Gate (G#), Demo command, Commit message. Tick boxes as work completes. Sections 6–19 are the permanent reference specs the phases implement against.

---

## 1. Mission & product recap

Experience Budget optimises each device's **experience** (call / game / stream / bulk), not just bandwidth. It:
forecasts link dips → applies **bounded, reversible** Linux `tc` changes → **verifies** each change → **rolls back** if it did not help → **explains** every decision.

**Deployment model**

```
Internet -> Wi-Fi router/modem -> [ Ubuntu Linux machine running EB agent ] -> D1, D2, D3, D4, D5 ...
                                         ^  local SQLite + ML + API (+ outbound relay link)
Browser (website) <-- pairing key/link --> relay (or direct LAN) <--> agent
```

- User visits the website, runs an **npm command** on their Linux machine; the agent initialises local storage and prints a **pairing key + link + QR**. Entering it on the website gives full remote visibility and control.
- Each device runs 0–4 traffic types simultaneously (Call, Game, Stream, Bulk). The Linux box is the middleware that sees and shapes all of it.
- **Two control loops** in the agent:
  - **Slow loop** every 30–60 s (configurable): classification, forecasting, QoE scoring, policy, snapshot publishing.
  - **Fast loop** every 1–2 s: counters, probes, step-change detection, emergency reactive fallback. A 100→20 Mbps drop must be handled within **2–5 s**.

**Honesty rules (enforced in code, UI, docs — see §18):** label emulated parts; QoE is a proxy with published formulas; show `unknown` instead of guessing; never claim "only one in the market"; adaptive rate control alone is not novel (cake-autorate exists); Windows/Docker cannot shape a Windows host's real traffic.

---

## 2. Ground-truth audit (2026-11-15)

> **Historical snapshot.** Sections 2.1–2.2 describe the repository before the
> local agent wiring landed. See §2.3 for current status.

### 2.1 What exists

| Area | State | Files |
|---|---|---|
| Web app | React 19 + Vite 8 + Tailwind 4 + TS 5.7 + zod 4 + zustand + react-query; pkg `figma-make-app`; **pnpm** | `frontend/package.json`, `frontend/vite.config.ts` |
| Routes | **All 15 console pages + 4 public pages exist** | `frontend/src/App.tsx`, `frontend/src/features/**` |
| Data contract | Zod: `Snapshot, Device, Flow, Intent(Input), Decision, Settings` + TS interfaces `MetricPoint, Scenario, Policy, Link, Diagnosis, Conflict, Schedule, Experiment, Capabilities, Correction, MLData, AgentInfo, Session, LogEntry, AskAnswer, MockControl` — **camelCase** | `frontend/src/types/index.ts` |
| Transport | `Transport` iface + working `MockTransport` (fault injection: offline/stale/slow/rateLimit/speed/seed) · `RelayTransport` = **stub that throws** · `DirectTransport = extends RelayTransport` (stub) | `frontend/src/lib/transport/*.ts` |
| Mock engine | 1 s tick, client-side QoE formulas, sha256 decision hash-chain (`GENESIS`), intent expiry, scenario ticks, experiment progress, localStorage persist, seeded RNG | `frontend/src/mock/engine/index.ts` |
| Mock API | Nearly every §8 endpoint: capabilities, snapshot, metrics, devices PATCH, flows+correct, intents CRUD, decisions+/verify, conflicts+accept/adjust/cancel, health/diagnosis, demand/*, links, paths/evaluate, scenarios/{name}+stop, experiments, settings PUT, safe-state/reset, ml/*, qoe/formulas, ask, agent/info+logs, sessions, keys/rotate, audit, dev/control+reset | `frontend/src/mock/handlers/index.ts` |
| Mock fixtures | JSON for every surface | `frontend/src/mock/data/*.json` |
| QoE lib | `callQoE, gameQoE, streamQoE, bulkQoE, qoeBand, fairness` (client-side) | `frontend/src/lib/qoe/index.ts` |
| Host env | Figma-Make preview: Vite dev server auto-runs on `$PORT` (8443); Figma plugins inside `vite.config.ts` — **must not be removed** | `frontend/AGENTS.md`, `CLAUDE.md` |

### 2.2 Gaps / problems

1. `frontend/docs/AUDIT.md` is **stale** (claims pages missing; they exist).
2. **No git repo** — `git init` + baseline commit required first.
3. **Zero tests** (vitest + Playwright + testing-library installed, no test files).
4. No scripts: only `dev/build/preview/format` — missing `lint` (eslint.config.js exists, unused), `typecheck`, `test`, `e2e`.
5. Per-page **transport-wiring audit** not yet done (which controls call Transport vs fake).
6. Contract mismatch: UI uses bare paths (`/state/snapshot`); backend spec is `/api/v1/...` → transports add the prefix; **UI paths unchanged**.
7. Case style: UI is camelCase; FastAPI defaults snake_case → agent emits camelCase via pydantic alias generator.
8. Missing everywhere: `/agent/update-check`, `/models/updates`, idempotency keys, pagination, ETag, `Retry-After`, `/metrics/internal`.
9. **Dev machine is Windows** — netns/tc gates run in CI (privileged Docker) and/or WSL2; local Windows loop uses mock + Docker lab profile.

---

### 2.3 Current status (2026-11-19)

- **Console transport:** `RelayTransport` connects to the agent's real API and
  WebSocket stream; `DirectTransport` targets an HTTPS LAN host. There is no
  browser-local mock transport in `frontend/src`.
- **Live agent data:** snapshot, metrics history, flows, devices, QoE, capacity,
  policy decisions, intents, conflicts, health, sessions, settings, ML metrics,
  demand forecasts, and the decision hash chain all come from the running agent.
- **ML:** the classifier trains locally at startup when no artifact exists,
  persists versioned joblib artifacts, reports holdout accuracy/F1/baseline/
  confusion matrix, and records live ML-vs-rule inference counts. Model versions
  can be retrained and rolled back from the UI. Training data is the deterministic
  lab generator (plus approved corrections) and is labelled synthetic.
- **Forecasting:** capacity uses Holt-Winters when enough measured history exists,
  otherwise a linear-trend or persistence fallback, with a confidence gate that
  can feed planning capacity. Per-class demand uses EWMA over measured bytes/s.
- **Honest limitations:** no qdisc is changed by this build (`shaping: false`);
  Windows/macOS/OpenWrt host shaping is not available; multi-link failover needs a
  second real WAN; federated learning remains feature-flagged; upstream router and
  two-device hardware acceptance have not been run here.

## 3. Locked architectural decisions

- **A1** Existing UI is the contract source of truth. Backend shapes match `frontend/src/types/index.ts` (camelCase) or adapt at the transport boundary. Real changes happen **once** in `packages/contracts`, updating both sides. Never break working screens.
- **A2** `frontend/` stays where it is (Figma-host preview + proven toolchain); it **is** apps/web. Monorepo grows around it. Do not rewrite `vite.config.ts`.
- **A3** `packages/contracts` = OpenAPI + JSON examples + generated TS + drift scripts. Frontend imports via vite/tsconfig alias `@eb/contracts` → `../packages/contracts/src` (no workspace linking required).
- **A4** Two loops (slow 30–60 s / fast 1–2 s). Fast loop owns step-detection and emergency reactive shaping.
- **A5** Privilege split: `eb-agentd` (unprivileged) + `eb-enforcerd` (CAP_NET_ADMIN + CAP_NET_RAW only; allow-listed ops; root-owned Unix socket; signed requests; no `shell=True`; read-back verify; dead-man TTL).
- **A6** Privacy: metadata features only; **never store payloads**; no deep inspection; sampling + retention configurable (raw features 24 h, aggregates 30 d).
- **A7** Every phase ends runnable + committed, gate = passing test or reproducible demo.
- **A8** Advanced features (F13–F17, RAG) are flag-gated, independently testable, maturity-labelled.
- **A9** Results published only from repeated runs; case-study numbers are targets, not claims.
- **A10** All TS types generated from FastAPI OpenAPI (`openapi-typescript`); zod schemas kept in sync via CI drift check that **fails on drift**.

---

## 4. Target repository layout

```
experience-budget/
  frontend/                 # EXISTING React+Vite UI (= apps/web; stays put)
  packages/contracts/       # openapi.yaml + examples/ + generated TS + drift-check scripts
  agent/
    pyproject.toml
    eb_agent/
      api/                  # FastAPI app, routes, schemas, deps
      core/                 # loops (slow/fast), lifecycle, reconciliation
      config/               # config.toml loader, validation, defaults
      store/                # SQLite WAL, migrations, retention, backup
      security/             # identity keys, pairing, tokens, sessions, audit
      collectors/           # conntrack, optional sampling (nfstream/scapy)
      features/             # per-flow feature pipeline
      classifier/           # rule baseline + RF/HGB, calibration, corrections
      estimator/            # Kalman, probes, step-change (CUSUM)
      forecaster/           # Holt-Winters, optional LightGBM, confidence gate
      qoe/                  # G.107 + game/stream/bulk scorers, fairness
      policy/               # max-min allocation, hysteresis, stability
      negotiation/          # intent conflict negotiation (flag)
      intents/              # intent CRUD + timer service
      enforcer/             # client + op catalog; eb-enforcerd daemon
      verifier/             # closed-loop before/after verification
      safety/               # safe-state, rollback manager, watchdog
      health/               # self-healing diagnostics (flag)
      demand/               # schedules + demand forecast (flag)
      paths/                # multi-link (flag)
      federated/            # federated learning (flag)
      rag/                  # ask-the-router (flag)
      lab/                  # netns topology, workloads, A/B harness, traffic_gen
      reports/              # decision exports, HTML experiment reports
      models/               # model registry, training, evaluation
    tests/
  relay/                    # FastAPI WS relay (stateless, ciphertext-only)
  cli/                      # npm package `experience-budget-agent` (bin: eb-agent)
  docker/                   # Dockerfile.agent, Dockerfile.relay, compose + profiles
  scripts/                  # reset_lab.sh, scenario injectors, release helpers
  docs/                     # AUDIT, ARCHITECTURE, QOE_FORMULAS, SECURITY, LIMITATIONS,
                            #  LICENSES, WINDOWS, RUNBOOK, model cards, API reference
  .github/workflows/        # ci.yml, release.yml
  Makefile                  # test, lint, build, lab-demo, reset-lab, contracts
  pnpm-workspace.yaml       # frontend + packages/* + cli
```

---

## 5. Build phases (one commit per phase)

### Phase 0 — Audit, baseline, contracts, monorepo skeleton
**Goal:** trustworthy baseline; single source of contract truth; repo under version control.

- [x] Git repository and baseline frontend commits already exist; add root `.gitignore` for node_modules, dist, `__pycache__`, `.venv`, databases, reports, and generated outputs without ignoring Figma source.
- [x] Baseline run: `pnpm install`; `dev`, `build`, `tsc --noEmit`, `eslint .`; record results; fix what is broken **before** building on it.
- [x] Add scripts: `lint`, `typecheck`, `test` (vitest); smoke test rendering Home with MockTransport.
- [x] **Deep UI audit** → rewrite `frontend/docs/AUDIT.md` (mirrored to `docs/AUDIT.md`): table of 15 console pages + 4 public pages with `done | partial | missing` + file paths; current data contract (Snapshot, Device, Flow, Intent, Decision, Settings, endpoints) incl. mismatches vs §8; **per-component wiring audit** (controls that call Transport vs fake); env vars; prioritised TODO (§16).
- [x] Create `packages/contracts/`: `openapi.yaml` (all §8 endpoints, camelCase, error schema `{code,message,details}`), `examples/*.json`, `scripts/drift-check.mjs` (OpenAPI ↔ zod ↔ examples).
- [x] Frontend alias `@eb/contracts` → `../packages/contracts/src` (tsconfig + vite resolve.alias); smoke-test build.
- [x] Root `README.md` (roadmap stub), `Makefile` (test/lint/build), `pnpm-workspace.yaml`.

**Tests:** vitest smoke green; drift-check green against current schemas.
**Gate G0:** build + typecheck + lint + smoke tests pass; AUDIT.md committed.
**Commit:** `chore(phase-0): audit baseline, contracts package, monorepo skeleton`

### Phase 1 — Reproducible lab
**Goal:** one command → baseline CSV (call + ISO download).

- [x] `agent/eb_agent/lab/topology.py`: netns household namespaces + veth + router namespace + netem on WAN side; deterministic setup/teardown; `scripts/reset_lab.sh` (one-command state reset).
- [x] `lab/workloads.py`: iperf3 bulk, ping RTT, flent (RRUL), synthetic call/game/stream generators.
- [x] `lab/traffic_gen.py`: deterministic seeded trace generator — call (small periodic UDP), game (tiny frequent packets, low rate), stream (chunked bursts), bulk (saturating TCP), noise, seeds, **encrypted/same-port subset** where port rules fail; exports dataset CSV + model card input.
- [x] `lab/harness.py`: run a controller profile over identical workloads/seeds/link traces; CSV collector (call p95 RTT, jitter p95/p99, bulk progress, worst-device QoE, CPU, packets/s).
- [x] Controllers v0: `unmanaged`, `cake_static`.
- [x] Makefile targets `lab-demo`, `reset-lab`; `docs/lab.md`.

**Tests:** traffic-gen determinism (fixed seed); topology smoke `@pytest.mark.netns` (CI privileged).
**Gate G1:** one command yields baseline CSV on Linux/WSL2/CI.
**Commit:** `feat(phase-1): reproducible netns lab + baseline CSV`

### Phase 2 — Core enforcement
**Goal:** bounded, reversible, verified `tc`/nft changes.

- [x] `enforcer/catalog.py`: allow-listed operation schemas — interface names must match discovered interfaces, rates within bounds, MACs/IPs validated; operation IDs for idempotency; **dry-run** prints exact commands; bounds re-enforced in the enforcer (never trust the controller).
- [x] `eb-enforcerd` (privileged daemon): root-owned Unix socket; signed requests; constant-time token check; apply → **read-back via `tc -j`** → report success/mismatch; auto-revert on mismatch/failure; records prior state.
- [x] `eb-agentd` enforcer client: snapshot (`tc -j qdisc/class/filter show`, `nft list ruleset`) → apply → verify → rollback on mismatch; decision-log entries.
- [x] Topology modes (`config.mode`): `gateway` (WAN+LAN ifaces, forwarding on, nftables NAT; dnsmasq/hostapd documented, optional), `bridge`, `lab` (netns+veth+netem), `monitor` (read-only).
- [x] Egress: CAKE on WAN iface, `bandwidth = estimate × shaping_factor (default 0.95)`, flags `diffserv4, nat, dual-srchost/dual-dsthost`, optional `ack-filter`.
- [x] Ingress: IFB + `tc filter ... action mirred` + CAKE `ingress` mode.
- [x] Per-class/device policy: nftables `inet` sets keyed by MAC/IP (v4+v6) → DSCP/fwmark per traffic class/device; CAKE tins via DSCP.
- [x] Fallback: HTB root + per-class HTB classes + `fq_codel` leaves + `tc filter fw` when `sch_cake` unavailable (startup module check + dry run).
- [x] IPv4 + IPv6; interface auto-detection with manual override + `doctor` check.
- [x] `safety/safe_state.py`: known-safe config (plain conservative CAKE or no shaping); persist + snapshot before every change.

**Tests:** `FakeTcRunner`/`FakeNft` unit tests (no root); netns integration (apply/read-back/rollback); bounds-rejection property tests; idempotency (re-apply same op ID).
**Gate G2:** apply/read-back + safe-state tests pass (CI privileged).
**Commit:** `feat(phase-2): privileged enforcer, CAKE/HTB fallback, nft marking, safe-state`

**Evidence (2026-11-20):** `pytest -m "not netns"` → 204 passed; `unshare -Urn ... pytest -m netns` → 7 passed (real CAKE/TBF/netem/HTB/fq_codel/IFB/mirred/nft). `agent/tests/test_enforcer_hardening.py` covers empty/wrong-rate read-back rejection, exact dry-run rendering, HMAC signature/replay/stale rejection, idempotent replay and conflict, safe-state persistence before apply, MAC-only v4/v6 nft rules, decision logging. `agent/tests/test_enforcer_netns.py` proves apply → `tc -j`/`nft list` read-back → prior-state rollback on a real kernel.

### Phase 3 — Storage + API + pairing/auth + relay + CLI + real UI transport
**Goal:** pair from the website; see live snapshot.

- [ ] `store/`: SQLite **WAL** schema v1 (§7 tables); versioned migrations runner; retention jobs; VACUUM schedule; integrity check at startup; backup/restore commands; parameterised queries only.
- [ ] `core/`: config loader (`/etc/eb-agent/config.toml`, root:eb-agent 0640; dev fallback `~/.local/share/eb-agent/`); slow loop (30–60 s) + fast loop (1–2 s); snapshot assembly each slow tick + light `tick` messages on fast loop; metrics history with **downsampling** (raw 1 s → 15 min, 10 s → 24 h, 1 min → 30 d); structured JSON logging + request IDs.
- [ ] `api/`: FastAPI `/api/v1` — every §8 endpoint; pydantic **camelCase aliases**; typed responses; error schema; idempotency keys on mutating calls; pagination; ETag for snapshot; rate-limit + `Retry-After`; `/metrics/internal` (local-only, Prometheus-style); request size limits.
- [ ] `security/`: Ed25519 signing + X25519 agreement keys (0600), `agent_id`; pairing secret → short key `EB-XXXX-XXXX-XXXX` + link `https://<site>/connect?key=EB-...#pk=<agent X25519 pubkey>` (`#` fragment never reaches a server) + terminal QR; keys expire (15 min default), single-use, regenerable (`eb-agent pair`); multi-browser sessions with owner + read-only viewer roles; sessions list/revoke; access + refresh tokens bound to session key; immediate revocation; brute-force lockout on pairing.
- [ ] **E2E crypto:** browser ephemeral X25519 → HKDF shared secret with agent pubkey → AES-GCM (WebCrypto) per-message nonces + replay protection (counters/timestamps) on every API/WS message. Relay sees ciphertext only.
- [ ] `relay/`: agent keeps **outbound** WSS (NAT-friendly, no port forwarding); browser joins room by `agent_id`; opaque routing; rate limits, max message size, connection limits, heartbeat/ping, reconnect with jittered backoff, presence (`online/offline/last_seen`); no payload persistence; structured logs without secrets; one-command deploy (`docker compose up`); env `RELAY_URL`.
- [ ] **Direct LAN mode:** agent HTTPS on `:7443`, self-signed cert with fingerprint shown in pairing link + pinned by UI; mDNS `_ebagent._tcp`; CORS allow-list (configured web origin only); mixed-content limits explained in UI (`localhost` works).
- [ ] `cli/` npm package `experience-budget-agent`, bin `eb-agent` (commander; Node ≥ 20; thin; **never implicit sudo**; no heavy postinstall): full command set in §10.
- [ ] **UI transport work:** implement `RelayTransport` (real protocol + WebCrypto E2E) and `DirectTransport` (origin + mixed-content checks, pinned fingerprint); Connect page: key/link parsing incl. `#pk=`, transport choice, E2E handshake status, session list, forget-agent, multi-agent switcher, expired/used-key errors; WS auto-reconnect; freshness pill (Live/Stale/Offline); **mode banners from `/capabilities`** (Gateway / Bridge / Lab-EMULATED / Monitor-only / Safe-state / Reactive-fallback); keep mock for dev (`VITE_TRANSPORT=mock|relay|direct`).
- [ ] On restart: agent re-reads kernel state, reconciles with DB, adopts or restores last known safe snapshot; crashed agentd leaves last applied safe config (enforcer dead-man switch).

**Tests:** migration tests; API contract tests vs `packages/contracts`; pairing/token unit tests; WebCrypto E2E handshake tests (vitest + Node crypto interop); relay rate-limit/size/heartbeat tests; CLI smoke tests; restart-reconciliation test.
**Gate G3:** on Linux/WSL2 — `npx experience-budget-agent init` → pair on the website → Home shows live snapshot.
**Commit:** `feat(phase-3): storage, api, pairing, relay, cli, real UI transport`

### Phase 4 — Analytics (F1–F4)
**Goal:** classification, capacity estimation, forecasting, QoE — measured and honest.

- [ ] `collectors/` + `features/`: per-flow stats from conntrack (netlink) + optional packet sampling (nfstream/scapy AF_PACKET) in capture windows; features only: packet length stats (mean/std/min/max/percentiles), IAT stats, direction ratio, bytes/packets/s, burstiness, duration, protocol (TCP/UDP/QUIC), port/protocol hints; **no payloads**; retention configurable (raw 24 h, aggregates 30 d).
- [ ] `classifier/`: deterministic rule baseline (STUN/TURN/RTP ranges, game ports, 443-heavy long downstream = stream, long high-volume = bulk) + RF and HistGradientBoosting (pick best by macro-F1 on held-out set); classes `call, game, stream, bulk, unknown`; confidence = max calibrated probability (`CalibratedClassifierCV`); below threshold (default 0.6) → `unknown`, **rule baseline decides policy**.
- [ ] Explainability: per-flow top feature contributions (importances/permutation) via API.
- [ ] Correction loop: `POST /flows/{id}/correct` → untrusted review queue; ML Lab approve/reject; approved labels join training set; `POST /ml/retrain` background task evaluates vs held-out, **promotes only if macro-F1 does not regress**, keeps previous version for rollback (`/ml/rollback`).
- [ ] `/ml/metrics`: accuracy, macro-F1, per-class P/R, confusion matrix, encrypted-subset vs port-rule baseline. **Target macro-F1 ≥ 0.80** on held-out synthetic set.
- [ ] `estimator/`: hand-written Kalman (numpy) over noisy usable-throughput + RTT-derived signals; state = capacity (+trend); output estimate + variance → uncertainty band (e.g. 1.96σ). Light active probes (short bursts / iperf3 / packet-pair / ICMP RTT), rate-limited, never exceeding a configured fraction of capacity, **disabled when link already saturated**. Step-change detector (CUSUM/innovation) on fast loop → immediate reactive shaping + decision log. Report MAE/MAPE + CI coverage vs known lab capacity. **Target 5–10% MAPE** on stable traces.
- [ ] `forecaster/`: Holt-Winters (statsmodels) daily seasonality over stored capacity history; optional LightGBM (hour, weekday, recent capacity, lags) **used only when rolling validation beats Holt-Winters by a margin**; **confidence gate** — forecast influences policy only if confidence ≥ threshold and recent error within tolerance, else auto-switch to `reactive_fallback` (UI mode badge), re-enter adaptive after N good windows; output series with lo/hi band, next-dip start/depth/duration; pre-emptive bounded shaper lowering with configurable lead time (60–120 s default) under rate-change limits; seeded **EMULATED** "evening congestion" daily trace for demos; compare vs reactive controller (cake-autorate-style) on call p95 latency/jitter during dip.
- [ ] `qoe/`: server scorers matching `frontend/src/lib/qoe` + **`docs/QOE_FORMULAS.md`** + `/qoe/formulas` (full details §6.4). Real metrics where possible (per-class RTT/jitter probes, `tc -s` sojourn, qdisc drops/retransmits); unavailable metric → `unknown` + reduced score confidence, never invented.

**Tests:** golden QoE tests vs published formulas; classifier eval with fixed seeds (target gate); estimator MAPE + coverage; forecaster gate unit tests; unknown-path E2E.
**Gate G4:** measured macro-F1 / MAPE / forecast-vs-reactive table in `reports/`; unknown path works end-to-end.
**Commit:** `feat(phase-4): classifiers, kalman estimator, forecaster, qoe engine`

### Phase 5 — Policy + intents + verifier + decision log (F5, F7–F9)
**Goal:** max-min allocation; timed boosts that survive restarts; verified changes; explainable append-only log.

- [ ] `policy/`: bisection on target t for maximum minimum-QoE s.t. Σ required rates ≤ usable capacity × (1 − headroom), per-flow minimum floors via progressive filling/water-filling; use **lower confidence bound** of capacity; intents/urgency as secondary tie-breakers; per-flow/per-device allocation + DSCP/mark assignments + CAKE bandwidth; **infeasible** flag with explicit degradation proposal (never silently violate); plain-language rationale.
- [ ] Stability: hysteresis; minimum dwell (10 s default); max rate change per step (20% default); cooldown after rollback; oscillation detector.
- [ ] Policy metrics: worst-device QoE, minimum throughput/progress, Jain index, **starvation count = 0 when floors feasible**.
- [ ] `intents/`: typed intent (device+class, goal, priority, deadline, duration, allowed trade-offs, `min_service_floor` always true); validate → negotiate feasibility → return `intent_id, feasibility, expiry, impact_preview` (what others give up); **SQLite-backed timer service** (`expires_at`) with asyncio scheduler; survives restarts (boot recompute + expire overdue); auto-revert to prior safe policy + verify revert; floor + bounds stay active during boosts; `DELETE /intents/{id}` cancel + revert; list with remaining seconds.
- [ ] `verifier/`: after each applied change — pre-window metrics → settle time (2–3 fast intervals, bounded) → post-window; keep if target metric improved beyond noise margin and no floor/safety violation; else rollback or escalate (uncertain/risky); paired statistical check (Wilcoxon / bootstrap CI) when enough samples; verdict `kept | rolled_back | escalated` + evidence to decision log; lab-measured rollback correctness + false keep/revert rate.
- [ ] Decision log (`store` + `reports/`): every policy change/revert/intent/mode switch/safe-state event → `Decision` (id, ts, trigger, templated plain-language reason e.g. *"Slowed D4's bulk download to protect D1's call because call QoE fell to 0.41; verified: call QoE 0.41 → 0.78, kept"*, before/after QoE per device, evidence JSON (inputs, forecast, candidate policy, commands, read-back result), verdict) — **append-only, hash-chained**; filters, search, pagination, CSV/JSON export.
- [ ] **UI:** decision hash-chain verified badge (wired to `/decisions/verify`); server-driven intent countdown; export buttons; optimistic UI only for reversible actions.

**Tests:** allocation property tests (floors satisfied; starvation=0 when feasible; monotonicity); intent restart-survival; verifier injected-bad-change → rolled_back; hash-chain tamper detection; policy stability tests.
**Gate G5:** intent countdown/expiry correct across agent restart; injected bad change rolled back with logged verdict.
**Commit:** `feat(phase-5): max-min policy, timed intents, closed-loop verifier, decision log`

### Phase 6 — A/B harness + Scenario Lab (F11)
**Goal:** `make lab-demo` → HTML report with 4 controllers + CIs; UI drives real runs.

- [ ] Controllers: `unmanaged`, `cake_static` (correctly set rate), `cake_autorate_like` (reactive delay/load), `experience_budget`. **No strawmen.**
- [ ] Scenarios (UI Scenario Lab + CLI): (1) ISO download during a call; (2) WAN drop 100→20 Mbps (+ predictable evening dip + random unannounced dip as controls); (3) three TVs + a gamer; plus Wi-Fi-loss-like impairment (shaping must **NOT** claim to fix it) and infeasible-intent-conflict case.
- [ ] `eb-agent lab run --scenario <name> --seed <n> --trials <k> --controllers ...` (§10); identical workloads/seeds/link traces per controller; netns topology from Phase 1.
- [ ] Stats: median, p95, bootstrap CIs over metrics (call p95 RTT, jitter p95/p99, bulk progress, worst-device QoE, CPU, packets/s).
- [ ] Report: Jinja2 + Plotly HTML — side-by-side graphs, table, config, tool versions, seeds, environment, **EMULATED label**, raw CSV/JSON.
- [ ] Agent endpoints: `POST /scenarios/{name}`, `POST /scenarios/stop`, `POST /experiments`, `GET /experiments/{id}`, `GET /experiments/{id}/report`; experiment progress via WS `scenario`/snapshot events.
- [ ] **UI:** Scenario Lab real control (start/stop/restore) + Experiments real run progress via WS + report download; mock retained for dev.
- [ ] Perf budget recorded: agent < 5–10% of one core at lab traffic; report packets/s, flows, interval, CPU.

**Tests:** harness replay determinism (same seed ⇒ same workload); report generation golden test; scenario API tests; controller interface tests.
**Gate G6:** `make lab-demo` produces the HTML report (4 controllers, CIs, EMULATED).
**Commit:** `feat(phase-6): a/b harness, scenarios, html reports, ui wiring`

### Phase 7 — Negotiation + self-healing + demand (F13–F15)
**Goal:** flagged advanced features, honest about uncertainty.

- [ ] `negotiation/` (flag `advanced.negotiation`): gather intents → capacity budget + QoE deficits **with uncertainty** → hard constraints (floor, bounds, safety, expiry, non-negotiables) → max-min first, urgency/importance tie-break → **1 preferred + ≤2 alternatives** (e.g. preserve call+gaming; reduce stream resolution; pause only backups) with estimated QoE trade-offs → user approval for material trade-offs → commit + monitor; re-open on condition change or high-urgency arrival. `GET /conflicts`, `POST /conflicts/{id}/accept|adjust|cancel`; infeasible combos explained in plain language. Metrics: worst-device QoE, negotiation time, accept/reject rate, floor violations.
- [ ] `health/` (flag `advanced.self_healing`): collect loss, RTT/jitter, queue delay, interface errors/drops, CPU/mem, WAN reachability, device disconnect history, Wi-Fi RSSI/PHY/retries **only when exposed** (else `unknown`); symptom classification → ranked root-cause hypotheses (bufferbloat, WAN instability, router CPU, cable errors, Wi-Fi interference, device-specific) with confidence + corroborating signals → permitted diagnostics → recommend or execute allow-listed low-risk fix. **Guardrails:** never change Wi-Fi channel on packet loss alone; channel changes need AP/router API (recommendation only here); uncertain diagnosis = recommend only; no reboots/isolation without explicit consent. `GET /health/diagnosis`. Metrics: precision/recall, root-cause ranking accuracy, false-remediation rate, MTTD/MTTR.
- [ ] `demand/` (flag `advanced.demand`): opt-in schedules (user-created, optional ICS import; **no covert calendar scraping**); historical aggregate demand by time/day; forecast start/duration/class; pre-compute policy, activate ~5 min before; monitor + expire; low-confidence = recommend only; users can disable learning, inspect/delete schedules, override. `GET/POST/DELETE /demand/schedules`, `GET /demand/forecast`. Metrics: precision/recall, lead time, false-preparation rate, p95 latency during known events.
- [ ] **UI:** Conflicts (options + accept/adjust/cancel), Health (diagnoses + signals + recommendations), Demand (schedules CRUD + forecast) wired to real endpoints; mock kept for dev.

**Tests:** negotiation feasibility (floors met; infeasible explained); health golden diagnosis cases; demand schedule precision tests; guardrail tests (unsafe action refused).
**Gate G7:** feasible conflicts meet floors; infeasible ones explained; no unsafe action at low confidence.
**Commit:** `feat(phase-7): conflict negotiation, self-healing, demand forecasting`

### Phase 8 — Docker/Windows + packaging + release + docs
**Goal:** Windows path works and is honestly labelled.

- [ ] `docker/Dockerfile.agent` (multi-stage, non-root where possible — only enforcer needs capabilities, healthchecks, resource limits, read-only rootfs where feasible), `docker/Dockerfile.relay`, `docker/docker-compose.yml` profiles: `agent`, `relay`, `lab`, `federated`, `web`.
- [ ] Linux: `network_mode: host`, `cap_add: [NET_ADMIN, NET_RAW]` for real gateway mode.
- [ ] **Windows (Docker Desktop + WSL2):** container shapes traffic only inside the Docker/WSL2 VM, **not the Windows host's real network** → Windows profile defaults to **Lab mode** (emulated household, netns/veth/netem inside container) + **Monitor mode**; UI **and** CLI show persistent banner *"LAB / EMULATED — not shaping this PC's real traffic"*; document the real option (Linux VM/mini-PC as gateway). `eb-agent init --docker` runs compose, mounts named volume for `/var/lib/eb-agent`, prints pairing key/link, works in PowerShell. `docs/WINDOWS.md` step-by-step + known limitations.
- [ ] systemd unit `eb-agent.service` (watchdog + heartbeat), `eb-enforcerd` unit; install scripts with explicit confirmation; revert script; show what will change before changing it (forwarding sysctls, nftables NAT; DHCP/hostapd opt-in only).
- [ ] Release workflow: dependency pinning, `pip-audit` + `npm audit`, checksums/signature verification for downloaded artifacts, version pinning, npm publish (dry-run in CI), licence report → `docs/LICENSES.md`.
- [ ] Docs suite: README (Linux + Windows quick start), ARCHITECTURE (data plane vs control plane diagram), QOE_FORMULAS, SECURITY (STRIDE), LIMITATIONS (emulated parts; real-hardware gaps: Wi-Fi remediation needs AP/driver APIs, multi-WAN session continuity, scale testing), RUNBOOK (safe-state recovery), model cards, API reference (from contracts), WINDOWS, lab guide.

**Tests:** compose config lint; image builds in CI; Windows-path smoke (WSL2); doctor checks.
**Gate G8:** `docker compose --profile lab up` on Windows shows live EMULATED data in UI; CI fully green.
**Commit:** `feat(phase-8): docker/windows profiles, packaging, release, docs`

### Phase 9 — Flagged advanced: multi-link, federated, Ask (F16, F17, RAG)
**Goal:** independently testable advanced features.

- [ ] `paths/` (flag `advanced.multilink`): requires two eligible WAN interfaces; per-link RTT/loss/throughput/cap/state probes; path scoring; ordinary traffic stays on preferred low-cost link; selected critical flows (nft mark + policy routing tables / `ip rule`) move to backup when thresholds persist; return after stability window; **document that failover can break sessions** (NAT/IP change); measure failover time, loss, interrupted sessions, cellular data used. `POST /paths/evaluate`, `GET /links`; lab-emulated with two veth uplinks; **hidden in UI when no second interface reported**.
- [ ] `federated/` (flag `advanced.federated`, opt-in; local-only default): local trainer on privacy-filtered features; update protection (clipping, optional Gaussian noise); secure-aggregation-style masking in reference coordinator; model registry with versions, canary rollout, rollback; rejection of malformed/poisoned updates (norm checks, schema checks, size/rate limits); docker-compose simulation with 3–5 agent containers + coordinator; `POST /models/updates` (coordinator); document that **FL alone is not a privacy guarantee**. Metrics: global model F1, per-home variance, update overhead, privacy audit notes. **Never federate schedules or browsing history.**
- [ ] `rag/` (flag `advanced.ask`): small local model (0.5–1B, e.g. Qwen2.5-0.5B/1B GGUF via llama.cpp or Ollama; **download on demand, never bundled**); index decision log, failures, diagnosis records (small sentence model embeddings or BM25 fallback); `POST /ask` retrieves top-k, answers **with citations to decision IDs**, **refuses to guess without evidence**, never executes actions (read-only), latency budget + memory limits configurable, fully offline.
- [ ] **UI:** Links page (conditional), Federated status panel, Ask-the-router with citations list.
- [ ] Stretch (only if time allows): QoE degradation predictor — predict device turning red in 5–10 s from queue growth + delay trends (gradient boosting) vs fixed-threshold baseline; metrics: seconds earlier + false-alarm rate; **informational alerts only, never auto-acts**.

**Tests:** failover integration (lab); update-validation + poison-rejection tests; ask refusal-without-evidence test; citation correctness.
**Gate G9:** each feature demoed via its own command/test.
**Commit:** `feat(phase-9): multilink, federated, ask-the-router (flagged, tested)`

### Phase 10 — Hardening + performance + final polish
**Goal:** fault-proof, fast, honest, polished.

- [ ] Fault-injection suite (F12): kill agentd mid-apply; bad tc output; missing module; DB corruption → safe state + measured recovery time; enforcer unreachable; watchdog timeout; repeated rollbacks; estimator divergence; manual `POST /safe-state/reset`.
- [ ] Dead-man switch verified: aggressive policies carry TTL, reverted by enforcer itself if not refreshed.
- [ ] Security pass vs `docs/SECURITY.md` checklist (STRIDE); audit-log coverage of every control action (who/when/what/result); secrets handling re-check; dependency audit.
- [ ] Perf budget enforcement: agent < 5–10% of one core at lab traffic; CPU/pps/flows/interval in A/B report.
- [ ] **UI completion:** onboarding wizard (mode detection → interface confirmation → device naming/assignment → privacy choices → flag opt-ins → safe-state test); Agent & Security page (version + update check, CPU/mem, loop timings, DB size, relay presence, sessions + revoke, rotate key, audit log viewer, logs viewer, diagnostics export); banner system final pass.
- [ ] Playwright e2e (against real agent in lab mode): pair → scenario → intent boost → decision → A/B → download report.
- [ ] Final honesty review: all EMULATED labels, copy, docs, `/capabilities` flags consistent.

**Tests:** fault suite; e2e suite; perf assertions; accessibility/contrast check (`frontend/scripts/check-contrast.ts` retained).
**Gate G10:** fault suite + e2e green; perf numbers in report; §17 DoD checklist signed.
**Commit:** `chore(phase-10): hardening, performance, polish, final docs`

---

## 6. Feature reference spec (what "done" means per feature)

### F1 — Privacy-safe flow classifier
- Inputs: conntrack per-flow stats + optional packet sampling windows (nfstream or scapy AF_PACKET). Features only (len/IAT stats, direction ratio, rates, burstiness, duration, protocol, port hints). **Never payloads.** Retention: raw features 24 h, aggregates 30 d (configurable).
- Baseline: deterministic port/protocol/device-hint rules.
- Model: RF + HistGradientBoosting, pick best macro-F1 on held-out set; calibrated confidence; threshold 0.6 default → `unknown` + rule baseline decides policy.
- Data: synthetic generator (`lab/traffic_gen.py`) with encrypted/same-port subset; dataset CSV + model card.
- Explainability: per-flow top feature contributions.
- Correction loop → review queue (untrusted) → approve/reject → retrain guarded (no macro-F1 regression) → versioned models + rollback.
- Metrics endpoint incl. confusion matrix + encrypted-subset vs baseline. **Target: macro-F1 ≥ 0.80.**

### F2 — Capacity estimator
- Hand-written Kalman (numpy): state capacity (+trend), noisy throughput + RTT-derived observations, variance → uncertainty band (1.96σ).
- Light rate-limited probes (bursts/iperf3/packet-pair/ICMP RTT) to re-anchor; disabled when link saturated by user traffic.
- CUSUM/innovation step-change detector on fast loop → immediate reactive shaping + decision entry.
- Metrics: MAE/MAPE + CI coverage vs known lab capacity. **Target 5–10% MAPE** on stable traces.

### F3 — Capacity forecaster
- Holt-Winters daily seasonality (statsmodels) over capacity history; optional LightGBM (hour, weekday, recent capacity, lags) **only if** rolling-val error beats HW by margin.
- Confidence gate: forecast used only if confidence ≥ threshold and recent error within tolerance; else `reactive_fallback` mode (UI badge); re-enter adaptive after N good windows.
- Outputs: series + lo/hi band, next-dip start/depth/duration, confidence; pre-emptive bounded lowering lead 60–120 s (config), rate-change limits respected.
- Seeded EMULATED evening-congestion trace for demos; comparison vs reactive controller on call p95 latency/jitter during dip.

### F4 — Per-device QoE (formulas published in `docs/QOE_FORMULAS.md` + served at `/qoe/formulas`)
- **Call (simplified ITU-T G.107 E-model):** effective delay `d = RTT/2 + jitter buffer (2×jitter) + codec delay`; `Id = 0.024d + 0.11(d−177.3)·H(d−177.3)`; `Ie_eff = Ie + (95−Ie)·Ppl/(Ppl+Bpl)`; `R = 93.2 − Id − Ie_eff`; `MOS = 1 + 0.035R + 7e−6·R(R−60)(100−R)` (clamp 1–4.5); `qoe = (MOS−1)/3.5`. State constants (Ie, Bpl for Opus-like codec) documented.
- **Game:** `0.55·f(rtt;30,150) + 0.30·f(jitter;5,40) + 0.15·f(loss;0,2%)`, `f(x;good,bad)` = 1 at/below good, 0 at/above bad, linear between.
- **Stream:** `0.60·min(1, throughput/target_bitrate) + 0.25·(1 − clip(CoV(bitrate)/0.5, 0, 1)) + 0.15·(1 − rebuffer_proxy)`; `rebuffer_proxy` = fraction of windows with throughput < target bitrate.
- **Bulk:** `min(1, progress_rate / fair_expected_rate)`, floor-violation flag below guaranteed minimum.
- Normalised 0–1; bands: green ≥ 0.75, amber 0.5–0.75, red < 0.5 (configurable). Device QoE = weighted min/mean over active flows (default: min weighted by priority); no flows → `idle` (not red). Household QoE + Jain fairness over devices.
- Real measurements where possible (per-class RTT/jitter probes, `tc -s` sojourn, qdisc drops/retransmits); unavailable → `unknown` + reduced confidence.

### F5 — Max-min policy engine
- Inputs: capacity estimate (lower CI bound), per-flow demand + QoE-vs-rate curves, floors, rate bounds, active intents + weights.
- Bisection on t for max-min QoE s.t. Σ rates ≤ usable × (1 − headroom), floors via progressive filling; intents/urgency tie-break; typed `Policy` (class rates, device rates, DSCP/marks, CAKE bandwidth) + plain-language rationale; infeasible → explicit degradation proposal.
- Stability: hysteresis, dwell 10 s, max change 20%/step, rollback cooldown, oscillation detector.
- Metrics: worst-device QoE, min throughput/progress, Jain, starvation count (0 when feasible).

### F6 — Enforcer + `eb-enforcerd`
- Modes: `gateway` / `bridge` / `lab` / `monitor`. CAKE egress (×0.95 shaping, diffserv4, nat, dual-srchost/dual-dsthost, optional ack-filter); IFB+mirred ingress CAKE; nft inet sets (v4+v6) → DSCP/fwmark; HTB+fq_codel fallback (detected at startup).
- Safety: snapshot → apply → **read-back verify** → auto-revert on mismatch/failure; operation IDs (idempotent); dry-run; bounds re-enforced; signed requests; allow-list catalog only; no `shell=True`; prior-state recording.

### F7 — Temporary intent API
- Typed intent; validation; negotiator feasibility; returns `intent_id, feasibility, expiry, impact_preview`; `min_service_floor` always true.
- SQLite timer (`expires_at`) + asyncio scheduler; restart-safe (boot recompute, expire overdue); auto-revert + verify; DELETE cancels + reverts; list with remaining seconds.

### F8 — Closed-loop verifier
- pre-window → settle (2–3 fast intervals, bounded) → post-window; keep only if improved beyond noise margin and no floor/safety violation; else rollback or escalate; paired Wilcoxon/bootstrap when enough samples; verdicts `kept | rolled_back | escalated` + evidence; lab-measured rollback correctness + false keep/revert rates.

### F9 — Decision log
- Every change/revert/intent/mode-switch/safe-state event → append-only hash-chained `Decision` (trigger, templated reason, before/after QoE, evidence JSON, verdict); filters, search, pagination, CSV/JSON export.

### F10-style live data
- Snapshot per slow tick + light fast-loop ticks; history downsampling: 1 s→15 min, 10 s→24 h, 1 min→30 d; WS events `snapshot, tick, decision, intent, mode, alert, scenario`.

### F11 — A/B harness
- 4 controllers; 5+ scenarios; identical seeds/workloads; CSV metrics; median/p95 + bootstrap CIs; Jinja2+Plotly HTML report (graphs, table, config, versions, seeds, env, EMULATED, raw CSV/JSON); `scripts/reset_lab.sh`; `make lab-demo`; strong baselines (correct CAKE + cake-autorate-style).

### F12 — Safe-state & rollback manager
- Known-safe config persisted; snapshot before every change; triggers: command failure, read-back mismatch, enforcer unreachable, controller exception, watchdog timeout, repeated rollbacks, estimator divergence, manual reset; systemd watchdog + heartbeat; aggressive policies carry TTL (enforcer dead-man revert); fault-injection tests with measured recovery time.

### F13 — Conflict negotiation (flag `advanced.negotiation`)
- Budget + deficits (with uncertainty) → hard constraints → max-min + urgency tie-break → 1 preferred + ≤2 alternatives with QoE trade-offs → approval for material trade-offs → commit + monitor → re-open on change/high-urgency. Endpoints + metrics per §5 Phase 7.

### F14 — Self-healing (flag `advanced.self_healing`)
- Signals incl. `unknown` handling → ranked hypotheses + confidence → permitted diagnostics → recommend or allow-listed low-risk fix; guardrails per §5 Phase 7; `/health/diagnosis`; P/R, ranking accuracy, false-remediation, MTTD/MTTR.

### F15 — Demand awareness (flag `advanced.demand`)
- Opt-in schedules (+ optional ICS import), historical aggregates, forecast start/duration/class, pre-compute + activate ~5 min early, monitor/expire, disable learning + inspect/delete/override; endpoints + metrics per §5 Phase 7.

### F16 — Multi-link (flag `advanced.multilink`)
- ≥2 WAN interfaces required; per-link probes; path scoring; policy-routing failover for selected critical flows; return after stability window; session-break honesty; measured failover metrics; lab two-veth emulation; UI hidden without second interface.

### F17 — Federated learning (flag `advanced.federated`, opt-in, local default)
- Privacy-filtered local training; clipping + optional noise; masked secure aggregation; registry + canary + rollback; poison rejection; 3–5 container simulation; coordinator endpoint; metrics + privacy audit notes; never federate schedules/history; FL ≠ privacy guarantee.

### RAG — Ask the router (flag `advanced.ask`)
- Local 0.5–1B GGUF (on-demand download, never bundled); index decisions/failures/diagnoses; top-k retrieval; cited answers (decision IDs); refuse without evidence; read-only; offline; latency/memory budgets.

### QoE degradation predictor (stretch)
- Predict red in 5–10 s from queue growth + delay trends (gradient boosting) vs fixed-threshold baseline; seconds-earlier + false-alarm metrics; alerts only, never auto-acts.

---

## 7. Storage (SQLite, WAL)

Tables (versioned migrations): `agent_identity, sessions, devices, flows, flow_features, metrics_raw, metrics_agg, qoe_scores, capacity_history, forecasts, intents, conflicts, policies, policy_applications, decisions (hash-chained), corrections, models, experiments, experiment_runs, schedules, health_events, links, audit_log, settings, feature_flags`.

- Retention jobs; VACUUM schedule; integrity check at startup; backup/restore commands; **parameterised queries only**.
- Config `/etc/eb-agent/config.toml` (root:eb-agent 0640); data `/var/lib/eb-agent/` (SQLite WAL `eb.db`, models, reports, logs, `identity.key` 0600); dev fallback `~/.local/share/eb-agent/`.

---

## 8. API contract (`/api/v1`, OpenAPI → `packages/contracts`)

`GET /capabilities` (mode, flags, available modules, emulated?) · `GET /state/snapshot` · `WS /stream` (`snapshot, tick, decision, intent, mode, alert, scenario`) · `GET /metrics` · `GET/PATCH /devices` · `GET /flows` · `POST /flows/{id}/correct` · `POST/GET/DELETE /intents` · `GET /conflicts` (+ accept/adjust/cancel) · `GET /decisions` · `GET /health/diagnosis` · `GET/POST/DELETE /demand/schedules` · `GET /demand/forecast` · `GET /links` · `POST /paths/evaluate` · `POST /scenarios/{name}` · `POST /scenarios/stop` · `POST /experiments` · `GET /experiments/{id}` · `GET /experiments/{id}/report` · `GET/PUT /settings` · `POST /safe-state/reset` · `GET /ml/metrics` · `GET/POST /ml/corrections` · `POST /ml/retrain` · `POST /ml/rollback` · `GET /qoe/formulas` · `POST /ask` · `POST /models/updates` · `GET /agent/info` · `GET /agent/logs` · `POST /agent/update-check` · `GET/DELETE /sessions` · `POST /keys/rotate` · `GET /audit` · `GET /metrics/internal` (local-only).

Cross-cutting: typed pydantic responses (**camelCase**); idempotency keys on mutating calls; request size limits; pagination; ETag on snapshot; consistent error `{code, message, details}`; `Retry-After` on rate limit; structured JSON logging with request IDs; TS types generated (`openapi-typescript`); zod ↔ OpenAPI ↔ examples **drift check fails CI**. UI keeps its bare paths; transports add the `/api/v1` prefix for relay/direct.

---

## 9. Pairing, relay, security

- Pairing: `EB-XXXX-XXXX-XXXX` short key + `#pk=` link + QR; 15-min expiry, single-use, regenerable; owner + read-only roles; multi-browser sessions; brute-force lockout.
- E2E: browser ephemeral X25519 → HKDF → AES-GCM per-message nonces + replay counters/timestamps (WebCrypto). Relay forwards ciphertext only.
- Relay: outbound-WSS from agent; room routing by `agent_id`; rate/size/conn limits; heartbeat; jittered-backoff reconnect; presence; no payload storage; secrets-free logs; one-command compose deploy.
- Direct LAN: HTTPS :7443 self-signed + fingerprint pinning; mDNS `_ebagent._tcp`; strict CORS allow-list; mixed-content guidance.
- Hardening: strict CORS/CSP; no secrets in logs/URLs after first read; constant-time comparisons; lockout; audit log of every control action; input validation everywhere; allow-list enforcer + signed requests; dependency pinning; `pip-audit`/`npm audit` in CI; no committed secrets; untrusted-input handling for corrections + model updates; `docs/SECURITY.md` STRIDE threat model.

---

## 10. CLI (`experience-budget-agent`, bin `eb-agent`, Node ≥ 20)

`init` (OS detect, prerequisites, mode choice native/`--docker`, dirs/venv or image pull, systemd unit + sudoers/caps after explicit confirmation, migrations, identity generation, print key+link+QR) · `start | stop | restart | status` · `pair` (new key) · `sessions` · `rotate-keys` · `doctor` (python version, tc, sch_cake, ifb, nft, conntrack, iperf3, interfaces, permissions, kernel modules, IPv6, Docker, time sync; prints fixes) · `logs [-f]` · `config get|set` · `mode set gateway|bridge|lab|monitor` · `lab run|reset|list` · `safe-reset` · `export-diagnostics` (redacted bundle) · `update` · `uninstall --purge` (reverts tc/nft first, removes service, optional data removal).

Usage: `npx experience-budget-agent init` or `npm i -g experience-budget-agent && eb-agent init`. No implicit sudo; checksums/signatures for downloaded artifacts; pinned versions.

---

## 11. Linux native vs Docker/Windows (honest split)

- **Linux (primary, real shaping):** Ubuntu 22.04/24.04; machine on traffic path (gateway/bridge). `init` documents + optionally scripts `net.ipv4/ip6 forwarding`, nftables NAT; DHCP/DNS/hostapd opt-in only; always show what changes + keep revert script. Requires `iproute2`, `sch_cake` (fallback HTB+fq_codel), `ifb`, `nftables`, `conntrack`, optional `iperf3`, `flent`.
- **Docker (all platforms; required for Windows):** profiles `agent, relay, lab, federated, web`. Linux: host network + NET_ADMIN/NET_RAW for real gateway. **Windows:** shapes only inside Docker/WSL2 VM → defaults to Lab + Monitor with persistent **"LAB / EMULATED — not shaping this PC's real traffic"** banner (UI + CLI); real option = Linux VM/mini-PC gateway; `docs/WINDOWS.md`.

---

## 12. Remaining UI work (page-by-page)

| Page | Work remaining |
|---|---|
| Connect | real key/link parse incl. `#pk=`, transport choice, E2E handshake status, sessions, forget-agent, multi-agent switcher, expired/used errors (P3) |
| Onboarding wizard (new) | mode detect, iface confirm, device naming/assignment, privacy choices, flag opt-ins, safe-state test (P10) |
| Agent & Security | version/update check, CPU/mem, loop timings, DB size, relay presence, sessions/rotate, audit + logs viewers, diagnostics export (P8/P10) |
| Banners | driven by `/capabilities`: Gateway / Bridge / Lab-EMULATED / Monitor-only / Safe-state / Reactive-fallback (P3) |
| Home | wire banners + freshness pill (P3) |
| Intent | server-driven countdown, expiry/cancel real (P5) |
| Decisions | hash-chain verified badge, filters, export (P5) |
| Experiments | real run progress via WS, report download (P6) |
| Scenario Lab | real scenario control with stop/restore (P6) |
| ML Lab | retrain button, model versions, rollback — real (P4) |
| Conflicts / Health / Demand | real endpoints (P7) |
| Links | conditional on second interface (P9) |
| Ask | citations list (P9) |
| Settings | all thresholds + flags persisted to agent (P3+) |
| Realtime | WS auto-reconnect, freshness pill, optimistic UI only for reversible actions (P3/P10) |
| Tests | update unit/component tests; Playwright e2e vs real agent in lab mode (per phase, final P10) |

---

## 13. Quality, tests, performance

- pytest per module (fixed seeds); `FakeTcRunner`/`FakeNft`; `@pytest.mark.netns` integration (CI privileged runner/Docker).
- Property tests: max-min allocation (floors satisfied; no starvation when feasible; monotonicity); enforcer bounds.
- Golden tests: QoE formulas; decision hash chain; report generation.
- Contract tests: OpenAPI ↔ zod ↔ mock ↔ generated TS (drift CI).
- Fault-injection suite (F12); E2E Playwright: pair → scenario → intent → decision → A/B → report.
- Performance budget: agent < 5–10% of one core at lab traffic; report packets/s, flows, interval, CPU in A/B report.
- **CI (GitHub Actions):** ruff · mypy · pytest (+ netns job) · frontend lint/typecheck/test/build · contract drift check · `pip-audit` · `npm audit` · Docker build · licence report (`docs/LICENSES.md`).

---

## 14. Documentation deliverables

`README.md` (Linux + Windows quick start) · `docs/AUDIT.md` · `docs/ARCHITECTURE.md` (data vs control plane diagram) · `docs/QOE_FORMULAS.md` · `docs/SECURITY.md` (STRIDE) · `docs/LIMITATIONS.md` (emulated parts; Wi-Fi remediation needs AP/driver APIs; multi-WAN session continuity; scale testing) · `docs/LICENSES.md` · `docs/WINDOWS.md` · `docs/RUNBOOK.md` (safe-state recovery) · model cards (classifier + forecaster) · API reference (generated) · lab guide.

---

## 15. Status tables

### Features

| # | Feature | Now | Phase | Proof |
|---|---|---|---|---|
| F1 | Flow classifier + corrections | mock only | 4 | macro-F1 ≥ 0.80 |
| F2 | Kalman estimator + probes + CUSUM | none | 4 | MAPE 5–10% |
| F3 | Forecaster + confidence gate | mock only | 4 | forecast-vs-reactive table |
| F4 | QoE scorers | client-side mock | 4 | golden formula tests |
| F5 | Max-min policy + stability | none | 5 | property tests |
| F6 | Enforcer + eb-enforcerd | none | 2 | apply/read-back/rollback tests |
| F7 | Timed intents | mock only | 5 | restart-survival test |
| F8 | Verifier | mock only | 5 | bad-change → rolled_back |
| F9 | Decision log | client mock | 5 | tamper + export tests |
| F10 | Live data + WS | mock | 3 | G3 pair → live snapshot |
| F11 | A/B harness + report | mock results | 6 | `make lab-demo` |
| F12 | Safe-state manager | UI mock | 2+10 | fault suite + recovery time |
| F13 | Negotiation | UI mock | 7 | feasibility tests |
| F14 | Self-healing | UI mock | 7 | golden diagnosis cases |
| F15 | Demand | UI mock | 7 | schedule precision tests |
| F16 | Multi-link | UI mock | 9 | two-veth failover test |
| F17 | Federated | toggle only | 9 | container sim + poison tests |
| — | Ask-the-router RAG | UI mock | 9 | refusal + citation tests |
| — | QoE degradation predictor | none | 10 stretch | seconds-earlier metric |

### UI pages (15 console + 4 public)

| Page | Route | File | Status |
|---|---|---|---|
| Home | `/app` | `features/home/Home.tsx` | done (mock) → banners P3 |
| Usage Tree | `/app/tree` | `features/tree/Tree.tsx` | done (mock) |
| Live Metrics | `/app/metrics` | `features/metrics/Metrics.tsx` | done (mock) |
| Intent Control | `/app/intent` | `features/intent/Intent.tsx` | done (mock) → countdown P5 |
| Decision Log | `/app/decisions` | `features/decisions/Decisions.tsx` | done (mock) → badge/export P5 |
| Experiments A/B | `/app/experiments` | `features/experiments/Experiments.tsx` | partial → real P6 |
| Settings & Safety | `/app/settings` | `features/settings/Settings.tsx` | done (mock) → persist P3+ |
| About | `/app/about` | `features/public/PublicPages.tsx` | done |
| Scenario Lab | `/app/lab` | `features/lab/Lab.tsx` | partial → real P6 |
| ML Lab | `/app/ml` | `features/ml/ML.tsx` | done (mock) → real P4 |
| Devices | `/app/devices` | `features/devices/Devices.tsx` | done (mock) |
| Conflicts | `/app/conflicts` | `features/advanced/Advanced.tsx` | done (mock) → real P7 |
| Health | `/app/health` | same | done (mock) → real P7 |
| Demand | `/app/demand` | same | done (mock) → real P7 |
| Links | `/app/links` | same | done (mock) → conditional P9 |
| Agent & Security | `/app/agent` | same | partial → P8/P10 |
| Landing | `/welcome` | `features/public/PublicPages.tsx` | done (mock) |
| Install | `/install` | same | done (mock) |
| Connect | `/connect` | same | partial → real handshake P3 |
| About (public) | `/about` | same | done |

---

## 16. Prioritised TODO

- **P0 blocking:** git init · lint/typecheck/test scripts · stale AUDIT.md rewrite · transport-wiring audit · contracts package + drift check · camelCase backend contract.
- **P1 core path:** Relay/Direct transports + E2E crypto · agent skeleton + storage · pairing + relay · CLI init/pair · Connect flow.
- **P2 intelligence:** collectors/features · classifier · estimator · forecaster · QoE server-side.
- **P3 control:** enforcer (early, per Phase 2) · policy · intents · verifier · decision log.
- **P4 proof:** lab + A/B + reports · scenarios · experiments/lab wiring.
- **P5 advanced (flagged):** negotiation · health · demand · multilink · federated · ask.
- **P6 ship:** docker/windows · release · docs · hardening · e2e · polish.

---

## 17. Definition of done & final output

Done when:
1. Ubuntu: `npx experience-budget-agent init` → key/link → pair on website → live household data every 30–60 s.
2. Create/cancel timed intent; correct a label (queue → approve → retrain/rollback).
3. Run the three scenarios + an A/B experiment; read "why" for every decision; reset to safe state.
4. Windows: Docker lab profile with clear EMULATED labelling.
5. F1–F12 implemented + tested; F13–F17 + RAG flagged and honestly labelled.
6. Web lint/typecheck/tests/build green; CI green; docs complete.

Final output (end of project): built-vs-remaining summary; feature-by-feature status table (F1–F17 + RAG + predictor); exact run commands (Linux, Docker/Windows, lab demo, tests); known limitations; measured-results table (**measured numbers only**).

---

## 18. Honesty red lines

EMULATED labels on all synthetic traces · QoE = proxy with published formulas · `unknown` over guesses · no "only one in the market" claims · adaptive rate control alone ≠ novel · Windows/Docker never shapes Windows host traffic · improvement targets ≠ claims · results only from repeated runs · never store payloads · infeasible never silently violated.

---

## 19. Exact run commands (reference)

```bash
# Web (existing UI)
cd frontend && pnpm install && pnpm dev            # mock backend
VITE_TRANSPORT=relay pnpm dev                      # real agent via relay
VITE_TRANSPORT=direct pnpm dev                     # LAN agent

# Pair + run agent (Ubuntu)
npx experience-budget-agent init                   # prints key + link + QR
eb-agent start | status | doctor | sessions | pair | safe-reset | mode set lab

# Docker / Windows
docker compose --profile lab up                    # EMULATED lab household
docker compose --profile agent --profile relay up  # Linux real gateway mode

# Lab + experiments
make lab-demo                                      # 4-controller A/B → HTML report
make reset-lab                                     # one-command lab reset
eb-agent lab run --scenario wan-drop --seed 42 --trials 5 \
  --controllers unmanaged,cake_static,cake_autorate_like,experience_budget

# Tests
pytest -m "not netns" && pytest -m netns           # agent (netns job needs privilege)
cd frontend && pnpm lint && pnpm typecheck && pnpm test && pnpm exec playwright test

# Contracts
make contracts && node packages/contracts/scripts/drift-check.mjs
```

**Performance gates:** classifier macro-F1 ≥ 0.80 · estimator MAPE 5–10% · 100→20 Mbps handled ≤ 2–5 s · agent CPU < 5–10% of one core · starvation = 0 when floors feasible · intent expiry correct across restart.
