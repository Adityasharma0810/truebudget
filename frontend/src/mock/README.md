# Emulated agent

All product data is owned by `data/` JSON fixtures and the seeded engine.
`MockTransport.ts` is the only application file allowed to import this directory.
The engine runs locally in the browser, never shapes traffic, and stores state in
`eb-engine-v1` with a try/catch in-memory fallback. No credentials are sent anywhere.
Snapshot timestamps, intent expiry, decisions, and charts use the engine clock.
Default development speed is 10 simulation seconds per wall-clock second.

## Protocol

Every request resolves after 80–320 ms. Errors expose `{code,message,details?}`.
Bodies and critical response contracts are validated with Zod. All paths below
are relative to the agent API base URL.

| Method | Endpoint                                        | Body / result                                                  |
| ------ | ----------------------------------------------- | -------------------------------------------------------------- |
| GET    | /capabilities                                   | flags, classes, scenarios, formulas, seed                      |
| GET    | /state/snapshot                                 | Snapshot                                                       |
| WS     | /stream                                         | snapshot, tick, decision, intent, mode, alert, scenario events |
| GET    | /metrics?range=15m\|1h\|24h\|30d&device=all\|D1 | MetricPoint[]                                                  |
| GET    | /devices                                        | Device[]                                                       |
| PATCH  | /devices                                        | {id,name,person?} → Device[]                                   |
| GET    | /flows                                          | Flow[]                                                         |
| POST   | /flows/:id/correct                              | {cls} → Flow                                                   |
| GET    | /intents                                        | Intent[]                                                       |
| POST   | /intents                                        | IntentInput → Intent                                           |
| DELETE | /intents?id=:id                                 | cancelled Intent                                               |
| GET    | /conflicts                                      | Conflict[]                                                     |
| POST   | /conflicts/:id/accept                           | {option} → Conflict                                            |
| POST   | /conflicts/:id/adjust                           | {option} → Conflict                                            |
| POST   | /conflicts/:id/cancel                           | {} → Conflict                                                  |
| GET    | /decisions                                      | Decision[] newest first                                        |
| GET    | /decisions/verify                               | {valid,count}                                                  |
| GET    | /health/diagnosis                               | Diagnosis[]                                                    |
| GET    | /demand/schedules                               | Schedule[]                                                     |
| POST   | /demand/schedules                               | {name,device,time,duration} → Schedule[]                       |
| DELETE | /demand/schedules?id=:id                        | Schedule[]                                                     |
| GET    | /demand/forecast                                | Forecast[]                                                     |
| GET    | /links                                          | Link[]                                                         |
| POST   | /paths/evaluate                                 | {recommended,reason,emulated}                                  |
| POST   | /scenarios/:name                                | iso-call\|wan-drop\|three-tvs → Snapshot                       |
| POST   | /scenarios/stop                                 | restored Snapshot                                              |
| GET    | /experiments                                    | Experiment[]                                                   |
| POST   | /experiments                                    | {scenario,seed,trials} → Experiment                            |
| GET    | /experiments/:id                                | Experiment                                                     |
| GET    | /experiments/:id/report                         | Experiment report data (client formats download)               |
| GET    | /settings                                       | Settings                                                       |
| PUT    | /settings                                       | Settings → Settings                                            |
| POST   | /safe-state/reset                               | {restore?:boolean} → Snapshot                                  |
| GET    | /ml/metrics                                     | MLData                                                         |
| GET    | /ml/corrections                                 | Correction[]                                                   |
| POST   | /ml/corrections                                 | {id,status:approved\|rejected} → Correction[]                  |
| POST   | /ml/retrain                                     | MLData                                                         |
| POST   | /ml/rollback                                    | MLData                                                         |
| GET    | /qoe/formulas                                   | Record<class,formula>                                          |
| POST   | /ask                                            | {question} → {text,citations,disclaimer}                       |
| GET    | /agent/info                                     | AgentInfo                                                      |
| GET    | /agent/logs                                     | LogEntry[]                                                     |
| GET    | /sessions                                       | Session[]                                                      |
| DELETE | /sessions?id=:id                                | Session[]                                                      |
| POST   | /keys/rotate                                    | {key,emulated}                                                 |
| GET    | /audit                                          | LogEntry[]                                                     |

Development-only: GET/PATCH `/dev/control` (fault booleans, speed, seed);
POST `/dev/reset` clears persistence before reloading.

`subscribe` emulates WS in memory; it does not open a socket.
Unknown endpoints return `NOT_IMPLEMENTED`. Error fixture keys contain EXPR,
USED, or FAIL. Any other correctly formatted key succeeds in mock mode.
Faults include offline, stale, slow responses, and rate limiting.

## Replacing the mock

Implement `Transport` in `RelayTransport` or `DirectTransport`, including
authenticated requests, snapshot validation, event subscription and reconnect.
Set `VITE_TRANSPORT=relay|direct`. The stubs explicitly refuse connection rather
than pretending to control real hardware. Preserve the contracts in `src/types`.

## Reproducibility and limitations

Seed 4242 is the default. Chart history and A/B outputs use seeded generators.
The decision log is SHA-256 linked, but is browser-local and not tamper-proof.
Historical traces are synthetic, not a recording of real packets.
Device-filtered history uses an emulated allocation estimate.
Classifier accuracy and experiment confidence intervals are illustrative fixtures,
not claims based on real model evaluation.
