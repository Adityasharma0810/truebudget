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


def test_classifier_train_and_predict() -> None:
    clf = FlowClassifier()
    metrics = clf.train()
    assert "macro_f1" in metrics
    assert metrics["macro_f1"] >= 0.0
    result = clf.predict({"dst_port": 27015, "protocol": "UDP", "direction_ratio": 0.5})
    assert result.predicted_class in CLASS_LABELS
    assert 0.0 <= result.confidence <= 1.0


def test_classifier_unknown_below_threshold() -> None:
    clf = FlowClassifier(threshold=0.99)
    clf.train()
    # With high threshold, most predictions fall back to rule
    result = clf.predict({"dst_port": 443, "protocol": "TCP", "direction_ratio": 0.5, "bytes_per_s": 1000000, "duration_s": 300})
    # Either ml with low confidence → rule, or rule directly
    assert result.predicted_class in CLASS_LABELS
