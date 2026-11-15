"""Deterministic synthetic traffic traces and classifier features.

The generator is a *model input* generator, not a packet capture.  It never
records payloads.  Every trace and dataset row is reproducible from its seed.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal

TrafficClass = Literal["call", "game", "stream", "bulk", "unknown"]
CLASSES: tuple[TrafficClass, ...] = ("call", "game", "stream", "bulk", "unknown")


@dataclass(frozen=True)
class TraceEvent:
    offset_ms: float
    size_bytes: int
    encrypted: bool
    port: int
    note: str = ""


@dataclass(frozen=True)
class TrafficTrace:
    traffic_class: TrafficClass
    seed: int
    duration_ms: int
    events: tuple[TraceEvent, ...]
    metadata: dict[str, float | int | str | bool] = field(default_factory=dict)

    def csv_rows(self) -> list[dict[str, str | int | float | bool]]:
        return [
            {
                "traffic_class": self.traffic_class,
                "seed": self.seed,
                "offset_ms": round(event.offset_ms, 3),
                "size_bytes": event.size_bytes,
                "encrypted": event.encrypted,
                "port": event.port,
                "note": event.note,
            }
            for event in self.events
        ]


def _jitter(rng: random.Random, amount_ms: float) -> float:
    return rng.uniform(-amount_ms, amount_ms)


def _event(
    t_ms: float,
    size: int,
    rng: random.Random,
    *,
    encrypted_ratio: float = 0.35,
    port: int = 0,
    note: str = "",
) -> TraceEvent:
    encrypted = rng.random() < encrypted_ratio
    # Encrypted traffic often shares 443 with video and bulk; this is exactly
    # the subset where port-only rules fail.
    resolved_port = 443 if encrypted else (port or rng.choice((3478, 19302, 27015, 8080)))
    return TraceEvent(max(0.0, t_ms), size, encrypted, resolved_port, note)


def generate_trace(
    traffic_class: TrafficClass,
    seed: int,
    duration_s: float = 10.0,
    *,
    encrypted_ratio: float = 0.35,
) -> TrafficTrace:
    """Generate a seeded trace for one traffic class.

    Same ``(traffic_class, seed, duration_s, encrypted_ratio)`` always returns
    the same events.
    """

    if traffic_class not in CLASSES:
        raise ValueError(f"unknown traffic class: {traffic_class}")
    if duration_s <= 0:
        raise ValueError("duration_s must be positive")
    rng = random.Random(f"eb-traffic:{traffic_class}:{seed}")
    duration_ms = int(duration_s * 1000)
    events: list[TraceEvent] = []

    if traffic_class == "call":
        t = 0.0
        while t < duration_ms:
            payload = rng.choice((120, 160, 160, 200, 240))
            events.append(
                _event(t, payload, rng, encrypted_ratio=encrypted_ratio, port=3478, note="rtp-like")
            )
            t += 20.0 + _jitter(rng, 2.0)
    elif traffic_class == "game":
        t = 0.0
        while t < duration_ms:
            payload = rng.choice((60, 80, 96, 120, 160))
            events.append(
                _event(t, payload, rng, encrypted_ratio=encrypted_ratio, port=27015, note="game")
            )
            t += 33.0 + _jitter(rng, 5.0)
    elif traffic_class == "stream":
        t = 0.0
        while t < duration_ms:
            burst_start = t
            chunks = rng.randint(6, 12)
            for index in range(chunks):
                size = rng.randint(900, 1400)
                events.append(
                    _event(
                        burst_start + index * rng.uniform(2.0, 6.0),
                        size,
                        rng,
                        encrypted_ratio=encrypted_ratio,
                        port=443,
                        note="stream-chunk",
                    )
                )
            t += rng.uniform(180.0, 260.0)
    elif traffic_class == "bulk":
        t = 0.0
        while t < duration_ms:
            events.append(
                _event(
                    t,
                    rng.randint(16_000, 64_000),
                    rng,
                    encrypted_ratio=encrypted_ratio,
                    port=443,
                    note="bulk-chunk",
                )
            )
            t += rng.uniform(8.0, 14.0)
    else:  # unknown / noise
        t = 0.0
        while t < duration_ms:
            events.append(
                _event(
                    t,
                    rng.choice((64, 128, 256, 512, 1024, 4096)),
                    rng,
                    encrypted_ratio=max(encrypted_ratio, 0.5),
                    note="noise",
                )
            )
            t += rng.uniform(1.0, 30.0)

    events.sort(key=lambda event: event.offset_ms)
    total_bytes = sum(event.size_bytes for event in events)
    encrypted_events = sum(1 for event in events if event.encrypted)
    metadata: dict[str, float | int | str | bool] = {
        "generator": "eb_agent.lab.traffic_gen.v1",
        "emulated": True,
        "events": len(events),
        "total_bytes": total_bytes,
        "encrypted_fraction": round(encrypted_events / max(1, len(events)), 4),
        "mean_size_bytes": round(total_bytes / max(1, len(events)), 2),
    }
    return TrafficTrace(traffic_class, seed, duration_ms, tuple(events), metadata)


def generate_flow_dataset(seed: int, rows_per_class: int = 60) -> list[dict[str, object]]:
    """Generate privacy-safe per-flow features for classifier training.

    Only metadata features are emitted.  Payload bytes are never generated or
    stored.  A deterministic subset uses encrypted/same-port traffic so the
    port-rule baseline is measured honestly.
    """

    if rows_per_class <= 0:
        raise ValueError("rows_per_class must be positive")
    rng = random.Random(f"eb-flow-dataset:{seed}")
    rows: list[dict[str, object]] = []
    profiles = {
        "call": {
            "protocol": "udp",
            "port": 3478,
            "pkt_mean": 170.0,
            "pkt_std": 25.0,
            "iat_mean": 20.0,
            "iat_std": 2.5,
            "duration": 300_000.0,
            "down_up": 1.0,
            "wifi": 19,
        },
        "game": {
            "protocol": "udp",
            "port": 27015,
            "pkt_mean": 105.0,
            "pkt_std": 22.0,
            "iat_mean": 33.0,
            "iat_std": 6.0,
            "duration": 900_000.0,
            "down_up": 2.0,
            "wifi": 12,
        },
        "stream": {
            "protocol": "tcp",
            "port": 443,
            "pkt_mean": 1_150.0,
            "pkt_std": 180.0,
            "iat_mean": 4.0,
            "iat_std": 2.0,
            "duration": 1_800_000.0,
            "down_up": 22.0,
            "wifi": 35,
        },
        "bulk": {
            "protocol": "tcp",
            "port": 443,
            "pkt_mean": 1_350.0,
            "pkt_std": 120.0,
            "iat_mean": 1.4,
            "iat_std": 0.6,
            "duration": 600_000.0,
            "down_up": 18.0,
            "wifi": 40,
        },
        "unknown": {
            "protocol": "tcp",
            "port": 8443,
            "pkt_mean": 420.0,
            "pkt_std": 310.0,
            "iat_mean": 60.0,
            "iat_std": 55.0,
            "duration": 120_000.0,
            "down_up": 3.0,
            "wifi": 8,
        },
    }
    for label in CLASSES:
        profile = profiles[label]
        for index in range(rows_per_class):
            encrypted = rng.random() < (0.45 if label in ("stream", "bulk") else 0.25)
            port = 443 if encrypted else int(profile["port"])
            pkt_mean = max(40.0, float(profile["pkt_mean"]) * rng.uniform(0.85, 1.15))
            pkt_std = max(0.0, float(profile["pkt_std"]) * rng.uniform(0.7, 1.3))
            iat_mean = max(0.2, float(profile["iat_mean"]) * rng.uniform(0.85, 1.15))
            iat_std = max(0.0, float(profile["iat_std"]) * rng.uniform(0.7, 1.3))
            duration = max(1_000.0, float(profile["duration"]) * rng.uniform(0.7, 1.3))
            down_up = max(0.2, float(profile["down_up"]) * rng.uniform(0.8, 1.2))
            bytes_per_s = pkt_mean * 1000.0 / iat_mean
            rows.append(
                {
                    "flow_id": f"syn-{label}-{seed}-{index:04d}",
                    "label": label,
                    "seed": seed,
                    "duration_ms": round(duration, 2),
                    "pkt_len_mean": round(pkt_mean, 2),
                    "pkt_len_std": round(pkt_std, 2),
                    "pkt_len_min": round(max(40.0, pkt_mean - 2.5 * pkt_std), 2),
                    "pkt_len_max": round(pkt_mean + 2.5 * pkt_std, 2),
                    "iat_mean_ms": round(iat_mean, 3),
                    "iat_std_ms": round(iat_std, 3),
                    "down_up_ratio": round(down_up, 3),
                    "bytes_per_s": round(bytes_per_s, 2),
                    "pkts_per_s": round(1000.0 / iat_mean, 3),
                    "burstiness": round(iat_std / max(iat_mean, 0.001), 3),
                    "protocol": profile["protocol"],
                    "port": port,
                    "wifi_rssi_dbm": int(profile["wifi"]) - rng.randint(0, 8),
                    "encrypted": encrypted,
                    "emulated": True,
                    "generator": "eb_agent.lab.traffic_gen.dataset.v1",
                }
            )
    rng.shuffle(rows)
    return rows


def write_trace_csv(trace: TrafficTrace, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = trace.csv_rows()
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["traffic_class"])
        writer.writeheader()
        writer.writerows(rows)
    return target


def write_dataset_csv(rows: Iterable[dict[str, object]], path: str | Path) -> Path:
    materialised = list(rows)
    if not materialised:
        raise ValueError("dataset is empty")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(materialised[0]))
        writer.writeheader()
        writer.writerows(materialised)
    return target


def dataset_manifest(rows: Iterable[dict[str, object]], seed: int) -> dict[str, object]:
    materialised = list(rows)
    labels: dict[str, int] = {}
    encrypted = 0
    for row in materialised:
        labels[str(row["label"])] = labels.get(str(row["label"]), 0) + 1
        encrypted += int(bool(row.get("encrypted")))
    return {
        "seed": seed,
        "rows": len(materialised),
        "labels": labels,
        "encrypted_rows": encrypted,
        "features": sorted(
            key
            for key in (materialised[0] if materialised else {})
            if key not in {"flow_id", "label", "generator"}
        ),
        "emulated": True,
        "note": "Synthetic metadata only; no payloads and no real-user data.",
    }


def write_manifest(rows: Iterable[dict[str, object]], seed: int, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dataset_manifest(rows, seed), indent=2) + "\n", encoding="utf-8")
    return target


def poisson_intervals(seed: int, rate_per_s: float, duration_s: float) -> tuple[float, ...]:
    """Deterministic Poisson-ish packet offsets for load generation."""

    rng = random.Random(f"eb-poisson:{seed}:{rate_per_s}:{duration_s}")
    times: list[float] = []
    t = 0.0
    mean_gap = 1000.0 / rate_per_s
    while t < duration_s * 1000.0:
        t += -math.log(max(1e-9, 1 - rng.random())) * mean_gap
        times.append(t)
    return tuple(times)


def trace_as_dict(trace: TrafficTrace) -> dict[str, object]:
    payload = asdict(trace)
    payload["events"] = [asdict(event) for event in trace.events]
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="eb-traffic-gen")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rows-per-class", type=int, default=60)
    parser.add_argument("--out", type=Path, default=Path("reports/lab/flow_dataset.csv"))
    parser.add_argument("--manifest", type=Path, default=Path("reports/lab/flow_dataset.manifest.json"))
    parser.add_argument("--trace-class", choices=CLASSES, default=None)
    parser.add_argument("--trace-duration", type=float, default=10.0)
    parser.add_argument("--trace-out", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = generate_flow_dataset(args.seed, args.rows_per_class)
    path = write_dataset_csv(rows, args.out)
    manifest_path = write_manifest(rows, args.seed, args.manifest)
    print(f"wrote {len(rows)} synthetic metadata rows to {path}")
    print(f"wrote manifest to {manifest_path}")
    if args.trace_class:
        trace = generate_trace(args.trace_class, args.seed, args.trace_duration)
        target = args.trace_out or Path(f"reports/lab/trace-{args.trace_class}.csv")
        print(f"wrote {len(trace.events)} trace events to {write_trace_csv(trace, target)}")
    print("EMULATED synthetic metadata; no payloads stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
