# Reproducible lab

> **EMULATED.** The lab creates a private Linux network namespace with veth
> links, a bridge, and a `tc`/`netem` bottleneck. It never shapes this
> computer's real WAN or Wi-Fi traffic.

## Architecture

```text
device netns 1 ─┐
device netns 2 ─┤
device netns 3 ─┼── veth ── bridge (router netns) ── veth ── WAN netns
device netns 4 ─┘                                      (shaped egress)
```

- The **router** namespace owns `br0`; it is an L2 bridge, so no IP forwarding
  or NAT is needed.
- The **WAN** namespace holds the sink server and the shaped `eth0` veth.
  Download traffic (sink -> devices) crosses that egress qdisc, which is where
  `netem`, `cake`, or the `tbf` fallback is installed.
- Every device is an isolated network namespace with one veth and its own IP
  (`10.200.0.11`, `.12`, ...). The WAN endpoint is `10.200.0.2`.
- The whole topology runs inside `unshare -Urn`, so it works without sudo on
  Linux when unprivileged user namespaces are enabled.
- GSO/TSO/GRO are disabled on the lab veths so queueing is measured in normal
  packets rather than 64 KiB super-packets.

## Commands

```bash
# one command -> baseline CSV (2 controllers, 4 devices, 12 s each)
make lab-demo

# explicit controller / seed / trial run
unshare -Urn env PYTHONPATH=agent .venv/bin/python -m eb_agent.lab.harness \
  --controllers unmanaged,cake_static --seed 42 --duration 12 --trials 2 \
  --out reports/lab/baseline.csv

# reset any interrupted namespace holders
make reset-lab
```

The harness also re-executes itself inside `unshare -Urn` when invoked
directly without a namespace.

## Workloads

The default four-device scenario runs concurrently:

| Device | Workload | Shape |
|---|---|---|
| D1 | call | UDP 160 B every 20 ms, echoed with RTT/jitter/loss |
| D2 | game | UDP 120 B every 33 ms |
| D3 | stream | TCP download paced to 6 Mbps, chunked bursts |
| D4 | bulk | TCP download saturating the link |

`agent/eb_agent/lab/traffic_gen.py` additionally provides deterministic
seeded traces and a privacy-safe synthetic flow dataset
(call/game/stream/bulk/unknown, including an encrypted/same-port-443 subset
where port-only rules fail). No payload material is stored.

```bash
unshare -Urn env PYTHONPATH=agent .venv/bin/python -m eb_agent.lab.traffic_gen \
  --seed 42 --rows-per-class 60 \
  --out reports/lab/flow_dataset.csv \
  --manifest reports/lab/flow_dataset.manifest.json \
  --trace-class call --trace-duration 10
```

`iperf3`, `flent`/RRUL, and external `ping` are optional ecosystem tools; the
default lab uses the built-in synthetic generators so the demo is reproducible
without installing extra system packages. `topology.ping()` uses the system
`ping` binary for the netns smoke test.

## CSV columns

`ts, emulated, controller, backend, seed, trial, duration_s, capacity_mbps,
call_p95_rtt_ms, call_jitter_p95_ms, call_jitter_p99_ms, call_loss_pct,
game_p95_rtt_ms, game_jitter_p95_ms, stream_throughput_mbps,
bulk_throughput_mbps, bulk_progress_mbps, worst_device_qoe, household_qoe,
cpu_percent, packets_per_second, qdisc, errors`

Every row has `emulated=True`. These are synthetic lab measurements, not
real-household claims.

## Tests

```bash
# fast unit tests (no namespace privilege)
make test-agent

# real veth/netem/CAKE integration, wrapped in a user+network namespace
make test-netns
```

The netns tests verify device-to-WAN connectivity, CAKE/TBF installation,
read-back, netem restoration, and one end-to-end harness row.

## Limitations

- Lab traffic is synthetic and EMULATED; it does not prove behaviour on a real
  Wi-Fi link or a specific ISP.
- `netem` is a FIFO delay/rate/loss emulator, not a real bottleneck scheduler.
- CAKE is used when `sch_cake` is available; otherwise the harness falls back
  to TBF and records the backend in the CSV.
- Wi-Fi retries, roaming, and airtime contention are out of scope until real
  AP/driver measurements exist (see `docs/LIMITATIONS.md`).
