from __future__ import annotations

from collections import Counter

from eb_agent.lab.traffic_gen import (
    CLASSES,
    dataset_manifest,
    generate_flow_dataset,
    generate_trace,
    poisson_intervals,
)


def test_trace_is_deterministic_for_seed() -> None:
    first = generate_trace("call", seed=42, duration_s=2.0)
    second = generate_trace("call", seed=42, duration_s=2.0)
    assert first == second
    assert len(first.events) > 50
    assert first.metadata["emulated"] is True


def test_trace_changes_with_seed() -> None:
    first = generate_trace("stream", seed=1, duration_s=2.0)
    second = generate_trace("stream", seed=2, duration_s=2.0)
    assert first.events != second.events


def test_encrypted_same_port_subset_exists() -> None:
    trace = generate_trace("stream", seed=7, duration_s=3.0, encrypted_ratio=1.0)
    assert trace.events
    assert all(event.encrypted for event in trace.events)
    assert all(event.port == 443 for event in trace.events)
    assert trace.metadata["encrypted_fraction"] == 1.0


def test_flow_dataset_is_deterministic_and_balanced() -> None:
    first = generate_flow_dataset(seed=11, rows_per_class=20)
    second = generate_flow_dataset(seed=11, rows_per_class=20)
    assert first == second
    counts = Counter(str(row["label"]) for row in first)
    assert counts == {label: 20 for label in CLASSES}
    encrypted = sum(1 for row in first if row["encrypted"])
    assert encrypted > 0
    assert all(row["emulated"] is True for row in first)
    assert all("payload" not in key for row in first for key in row)


def test_dataset_manifest_reports_labels_and_privacy_note() -> None:
    rows = generate_flow_dataset(seed=3, rows_per_class=5)
    manifest = dataset_manifest(rows, seed=3)
    assert manifest["rows"] == 25
    assert manifest["emulated"] is True
    assert "Synthetic metadata only" in str(manifest["note"])
    assert set(manifest["labels"]) == set(CLASSES)


def test_poisson_intervals_are_deterministic() -> None:
    assert poisson_intervals(4, 100.0, 2.0) == poisson_intervals(4, 100.0, 2.0)
    intervals = poisson_intervals(4, 100.0, 2.0)
    assert len(intervals) > 100
    assert intervals == tuple(sorted(intervals))
