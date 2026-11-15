# Experience Budget UI and API audit

Audit scope: the committed React/Vite application under `frontend/`, its
transport boundary, Zod schemas, mock server, and the target API contract in
`packages/contracts/openapi.yaml`. This describes the repository as it exists;
the mock's simulated behavior is not evidence of live network control.

## Repository baseline

- The Git repository and tracked frontend already exist. The app is the
  Figma-Make project in `frontend/`; its Vite plugins and preview setup are
  intentionally retained.
- All 15 console routes and four public routes are implemented. The old audit
  claiming they were missing was stale.
- `MockTransport` is the only working transport. It dispatches into the
  in-browser mock server. `RelayTransport` throws for requests and connect;
  `DirectTransport` inherits that stub.
- No agent API, relay service, CLI, Linux enforcer, or live transport is
  present. The screen copy labels the lab as emulated, but some values and
  controls are still mock data.
- Python 3.14 is available in this Linux environment. The project-local
  `.venv/` is the required location for future Python tools and packages.

## Page inventory and wiring

| Page | Route | File | Status | Data and control wiring |
|---|---|---|---|---|
| Home | `/app` | `src/features/home/Home.tsx` | done (mock) | Snapshot, capabilities, settings, metrics, decisions, and intents; boosts and safe-state actions use mock API. |
| Usage Tree | `/app/tree` | `src/features/tree/Tree.tsx` | done (mock) | Snapshot from console context; flow corrections enqueue mock requests; view/selection controls are local. |
| Live Metrics | `/app/metrics` | `src/features/metrics/Metrics.tsx` | done (mock) | Metrics query; range/device/table controls are local. |
| Intent Control | `/app/intent` | `src/features/intent/Intent.tsx` | done (mock) | Lists, creates, and cancels intents against the mock; countdown is client-driven. |
| Decision Log | `/app/decisions` | `src/features/decisions/Decisions.tsx` | done (mock) | Reads decisions; verification, Ask, and export actions use the mock or browser-local behavior. |
| Experiments A/B | `/app/experiments` | `src/features/experiments/Experiments.tsx` | partial | Reads mock experiments and metrics; start/report controls use mock requests; no real report backend or WS progress. |
| Settings & Safety | `/app/settings` | `src/features/settings/Settings.tsx` | done (mock) | Reads and saves mock settings; no agent persistence. |
| About (console) | `/app/about` | `src/features/public/PublicPages.tsx` | done | Static product information. |
| Scenario Lab | `/app/lab` | `src/features/lab/Lab.tsx` | partial | Reads mock metrics; start/stop/restore actions mutate only in-browser emulated state. |
| ML Lab | `/app/ml` | `src/features/ml/ML.tsx` | done (mock) | Reads mock metrics/corrections; retrain, rollback, and review are simulated. |
| Devices | `/app/devices` | `src/features/devices/Devices.tsx` | done (mock) | Snapshot/metrics and device updates use mock requests; presentation controls are local. |
| Conflicts | `/app/conflicts` | `src/features/advanced/Advanced.tsx` | done (mock) | Reads and mutates mock conflicts. |
| Health | `/app/health` | `src/features/advanced/Advanced.tsx` | done (mock) | Reads mock diagnoses; recommendations are not live remediations. |
| Demand | `/app/demand` | `src/features/advanced/Advanced.tsx` | done (mock) | Mock schedule CRUD and forecast. |
| Links | `/app/links` | `src/features/advanced/Advanced.tsx` | done (mock) | Reads mock links; path evaluation is emulated. |
| Agent & Security | `/app/agent` | `src/features/advanced/Advanced.tsx` | partial | Reads mock info, sessions, audit, and logs; key/session actions do not contact an agent. |
| Landing | `/welcome` | `src/features/public/PublicPages.tsx` | done (mock) | Reads mock snapshot/capabilities. |
| Install | `/install` | `src/features/public/PublicPages.tsx` | done (mock) | Instructions and copy/tab controls are browser-local. |
| Connect | `/connect` | `src/features/public/PublicPages.tsx` | partial | Demo key and mock `connect`; no pairing protocol, key exchange, or real session. |
| About (public) | `/about` | `src/features/public/PublicPages.tsx` | done | Static information and QoE formulas from mock API. |

### Transport-wiring audit

All server-shaped UI reads go through `useApi`/`useSnapshot`, which call
`transport.request`. Mutations use `useTask` or `request`. With the default
`VITE_TRANSPORT=mock`, those operations reach the mock engine. No page talks to
the network directly. The few local tabs, filters, expansions, clipboard
actions, and modal state are browser-only interactions.

| Surface | UI paths/actions | Current implementation |
|---|---|---|
| Console shell | `/state/snapshot`, `/settings`, `/capabilities`, `/intents`, `/safe-state/reset` | Mock reads and mutations; client freshness timer. |
| Home | `/metrics`, `/decisions`, `/intents` | Mock queries; intent boost uses mock mutation. |
| Tree / Devices | snapshot, `/flows/{id}/correct`, device update | Mock state and mutation; device form is not live. |
| Metrics | `/metrics?range=…&device=…` | Mock query; selectors are local. |
| Intent / Decisions | `/intents`, `/decisions`, `/decisions/verify`, `/ask` | Mock query/mutation; exports are client-side. |
| Scenario Lab / Experiments | `/scenarios/{name}`, `/scenarios/stop`, `/experiments`, `/experiments/{id}/report`, `/metrics` | Mock state only; no lab process, report service, or WS. |
| ML Lab | `/ml/metrics`, `/ml/corrections`, `/ml/retrain`, `/ml/rollback` | Mock result data and simulated model actions. |
| Conflicts / Health / Demand / Links | `/conflicts`, `/health/diagnosis`, `/demand/schedules`, `/demand/forecast`, `/links`, `/paths/evaluate` | Mock responses and actions. |
| Agent & Security | `/agent/info`, `/agent/logs`, `/sessions`, `/keys/rotate`, `/audit`, update check | Mock values/actions; no identity or session service. |
| Public pages | `/state/snapshot`, `/capabilities`, `/qoe/formulas`; `transport.connect` | Mock snapshot/capabilities/formulas and demo connection only. |
| Developer panel | `/dev/control`, `/capabilities`, scenario/reset controls | Development-only mock controls. |

## Current data contract

`frontend/src/types/index.ts` defines Zod schemas for `Snapshot`, `Device`,
`Flow`, `IntentInput`, `Intent`, `Decision`, `Settings`, `Error`, and supporting
enums/shapes. Remaining UI records (`MetricPoint`, `Scenario`, `Policy`,
`Link`, `Diagnosis`, `Conflict`, `Schedule`, `Experiment`, `Capabilities`,
`Correction`, `MLData`, `AgentInfo`, `Session`, `LogEntry`, `AskAnswer`) are
TypeScript interfaces. Names and JSON fields use camelCase, with `cls` as the
existing flow-class key. The dev-only `MockControl` is not an agent API type.

The target API is rooted at `/api/v1`; the UI intentionally keeps bare paths
and a real transport must add that prefix. The mock transport does not add it.
`packages/contracts/openapi.yaml` lists the §8 operations and core schemas.
OpenAPI-to-TypeScript generation is still pending; the alias entry exists, but
the current generated-type module is empty.
Known gaps between current UI/mock behavior and the target contract:

- There is no backend to enforce any contract, authorization, error handling,
  idempotency, pagination, rate limits, ETag, or `Retry-After`.
- Current intent cancellation passes `DELETE /intents?id=…`; the target phase-5
  API specifies resource cancellation at `/intents/{id}`. Align the UI and
  contract when replacing the mock handler.
- `Decision.evidence` is intentionally `unknown`; other non-Zod UI interfaces
  have not yet been validated against a live response.
- Relay/direct transports do not currently prepend `/api/v1`; they are stubs.
- Mock-only `/dev/*` routes are not part of the production contract.

## Environment and baseline checks

- `VITE_TRANSPORT`: `mock` (default), `relay`, or `direct`.
- `PORT`: Vite dev/preview port (default `8443`); `FIGMA_DEV_SERVER_HOST`
  selects the bind host; `FIGMA_PUBLIC_URL` sets the build base URL.
- Baseline recorded 2026-11-15 on Linux (Node 24.22.2, pnpm 12.18.0):
  - `pnpm install --frozen-lockfile` (root and `frontend/`) — pass. The
    workspace pins `tldts`/`tldts-core` to 7.0.5 so the lockfile satisfies the
    active minimum-release-age supply-chain policy.
  - `pnpm lint` — pass.
  - `pnpm typecheck` — pass.
  - `pnpm test` — pass (2 files, 8 tests).
  - `pnpm build` — pass (Vite production build).
  - `pnpm contracts` — pass (50 operations, 28 frontend schemas, 7 examples).
  - `pnpm dev` — serves `HTTP 200` on the configured port.
- Git is already initialized. Python work must use the root `.venv/`; Python
  dependency downloads must not target the system interpreter.

## Prioritized remaining work

1. **P0:** finish locked frontend install under the supply-chain policy; pass
   lint, typecheck, unit smoke test, and build; validate the alias and contracts.
2. **P1:** implement agent/storage/pairing/relay/CLI plus E2E crypto and real
   transports; replace demo-only Connect.
3. **P2:** privacy-safe collection, classifier, estimator, forecaster, and
   server QoE with measured test gates.
4. **P3:** enforcer, policy, timed intents, verifier, and persistent decision
   log; require Linux/netns integration evidence for real shaping.
5. **P4–P6:** measured lab/experiments, flag-gated advanced APIs, Docker/Windows
   honesty, docs, CI, security, performance, and end-to-end hardening.

Refer to the master plan in `task.md` for phase gates and completion criteria.
