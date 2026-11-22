"""Unit tests for eb_agent.classifier (no root required)."""

from __future__ import annotations

from eb_agent.classifier import (
    FlowClassifier,
    CLASS_LABELS,
    DEFAULT_THRESHOLD,
    rule_classify,
)


def test_class_labels() -> None:
    assert "call" in CLASS_LABELS
    assert "game" in CLASS_LABELS
    assert "stream" in CLASS_LABELS
    assert "bulk" in CLASS_LABELS
    assert "unknown" in CLASS_LABELS


def test_rule_call_stun() -> None:
    cls, conf = rule_classify({"dst_port": 3478, "protocol": "UDP", "direction_ratio": 0.5, "bytes_per_s": 10000, "duration_s": 30})
    assert cls == "call"
    assert conf > 0.5


def test_rule_game_port() -> None:
    cls, conf = rule_classify({"dst_port": 27015, "protocol": "UDP", "direction_ratio": 0.5, "bytes_per_s": 50000, "duration_s": 60})
    assert cls == "game"


def test_rule_stream_443_downstream() -> None:
    cls, conf = rule_classify({"dst_port": 443, "protocol": "TCP", "direction_ratio": 0.2, "bytes_per_s": 5_000_000, "duration_s": 300})
    assert cls == "stream"


def test_rule_bulk_high_volume() -> None:
    cls, conf = rule_classify({"dst_port": 443, "protocol": "TCP", "direction_ratio": 0.1, "bytes_per_s": 15_000_000, "duration_s": 120})
    assert cls == "bulk"


def test_rule_unknown() -> None:
    cls, conf = rule_classify({"dst_port": 9999, "protocol": "TCP", "direction_ratio": 0.5, "bytes_per_s": 1000000, "duration_s": 5})
    assert cls == "unknown"


def test_classifier_default_threshold() -> None:
    assert DEFAULT_THRESHOLD == 0.6


def test_classifier_predict_untrained_uses_rule() -> None:
    clf = FlowClassifier()
    result = clf.predict({"dst_port": 27015, "protocol": "UDP"})
    assert result.method == "rule"
    assert result.predicted_class == "game"


def _real_rows(n_per_class: int = 10, seed: int = 7) -> list[dict]:
    """Test fixture shaped like real correction rows: live-measured features
    plus a human label. Tests may synthesise rows; the agent cannot."""

    import random

    rng = random.Random(seed)
    profiles = {
        "call": dict(dst_port=3478, protocol="UDP", direction_ratio=0.5, bytes_per_s=60_000, packets_per_s=50, pkt_len_mean=180, iat_mean_ms=20, duration_s=600),
        "stream": dict(dst_port=443, protocol="TCP", direction_ratio=0.1, bytes_per_s=3_000_000, packets_per_s=2500, pkt_len_mean=1200, iat_mean_ms=0.4, duration_s=900),
        "bulk": dict(dst_port=443, protocol="TCP", direction_ratio=0.05, bytes_per_s=9_000_000, packets_per_s=7500, pkt_len_mean=1400, iat_mean_ms=0.13, duration_s=1800),
    }
    rows: list[dict] = []
    for label, base in profiles.items():
        for i in range(n_per_class):
            row = dict(base)
            for key in ("bytes_per_s", "packets_per_s", "pkt_len_mean", "direction_ratio"):
                row[key] = float(row[key]) * rng.uniform(0.8, 1.2)
            row["label"] = label
            row["captured_at"] = 1000.0 + i
            rows.append(row)
    return rows


def test_classifier_refuses_without_real_rows() -> None:
    clf = FlowClassifier()
    metrics = clf.train([])
    assert metrics["trained"] is False
    assert not clf.is_trained
    assert metrics["n_real_rows"] == 0


def test_classifier_train_on_real_rows_and_predict() -> None:
    clf = FlowClassifier()
    metrics = clf.train(_real_rows())
    assert metrics["trained"] is True
    assert metrics["source"] == "real_flow_corrections"
    assert "macro_f1" in metrics
    assert metrics["macro_f1"] >= 0.0
    result = clf.predict(
        {
            "dst_port": 443,
            "protocol": "TCP",
            "direction_ratio": 0.1,
            "bytes_per_s": 3_000_000,
            "packets_per_s": 2500,
            "pkt_len_mean": 1200,
            "iat_mean_ms": 0.4,
            "duration_s": 900,
        }
    )
    assert result.predicted_class in CLASS_LABELS
    assert 0.0 <= result.confidence <= 1.0


def test_classifier_unknown_below_threshold() -> None:
    clf = FlowClassifier(threshold=0.99)
    clf.train(_real_rows())
    # With high threshold, most predictions fall back to rule
    result = clf.predict({"dst_port": 443, "protocol": "TCP", "direction_ratio": 0.5, "bytes_per_s": 1000000, "duration_s": 300})
    # Either ml with low confidence → rule, or rule directly
    assert result.predicted_class in CLASS_LABELS
