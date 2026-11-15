"""Flow classifier: rule baseline + RF/HGB with calibrated confidence.

Classes: call, game, stream, bulk, unknown.
Below threshold (default 0.6) → unknown, rule baseline decides policy.
Never inspects payloads; features only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split

from eb_agent.lab.traffic_gen import CLASSES, generate_flow_dataset

CLASS_LABELS = list(CLASSES)
DEFAULT_THRESHOLD = 0.6

# ---- rule baseline ---------------------------------------------------------

GAME_PORTS = frozenset({3074, 3075, 3076, 3478, 3479, 3480, 27015, 27016, 27017, 27018, 27019, 27020})
STUN_TURN_PORTS = frozenset({3478, 3479, 3480, 5349})
RTP_PORT_RANGE = range(16384, 32768)


def rule_classify(features: dict) -> tuple[str, float]:
    """Deterministic rule-based classification. Returns (class, confidence)."""

    proto = str(features.get("protocol", "")).upper()
    dst_port = features.get("dst_port")
    src_port = features.get("src_port")
    direction_ratio = float(features.get("direction_ratio", 0.5))
    bytes_per_s = float(features.get("bytes_per_s", 0.0))
    duration_s = float(features.get("duration_s", 0.0))
    burstiness = float(features.get("burstiness", 0.0))

    ports = set()
    for p in (dst_port, src_port):
        if p is not None:
            ports.add(int(p))

    # Call: STUN/TURN ports (before game/RTP overlap)
    if ports & STUN_TURN_PORTS:
        return "call", 0.85

    # Game: known game ports, tiny packets, low rate
    if ports & GAME_PORTS:
        return "game", 0.85

    # Call: RTP port range, low bitrate, symmetric
    if any(p in RTP_PORT_RANGE for p in ports):
        return "call", 0.8
    if proto == "UDP" and 0.4 <= direction_ratio <= 0.6 and bytes_per_s < 500_000:
        return "call", 0.65

    # Game: tiny UDP packets, low rate, long duration
    if proto == "UDP" and bytes_per_s < 100_000 and duration_s > 30:
        return "game", 0.6

    # Bulk: very long high-volume, one direction (check before stream)
    if bytes_per_s > 10_000_000 and duration_s > 60 and abs(direction_ratio - 0.5) > 0.3:
        return "bulk", 0.85

    # Stream: 443-heavy long downstream, sustained bitrate
    if 443 in ports and direction_ratio < 0.3 and bytes_per_s > 1_000_000:
        return "stream", 0.8
    if proto == "TCP" and direction_ratio < 0.3 and bytes_per_s > 2_000_000:
        return "stream", 0.7

    # Bulk: long high-volume, one direction
    if bytes_per_s > 5_000_000 and duration_s > 60 and abs(direction_ratio - 0.5) > 0.3:
        return "bulk", 0.8

    return "unknown", 0.5


# ---- ML classifier ---------------------------------------------------------


@dataclass
class ClassifierResult:
    predicted_class: str
    confidence: float
    probabilities: dict[str, float]
    method: str  # "rule" or "ml"
    top_features: list[tuple[str, float]]


class FlowClassifier:
    """Rule baseline + ML (RF/HGB) classifier with calibrated confidence."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._model = None
        self._calibrated = None
        self._feature_names: list[str] = []
        self._trained = False

    def _feature_vector(self, features: dict) -> list[float]:
        return [
            float(features.get("pkt_len_mean", 0)),
            float(features.get("pkt_len_std", 0)),
            float(features.get("pkt_len_min", 0)),
            float(features.get("pkt_len_max", 0)),
            float(features.get("iat_mean", 0)),
            float(features.get("iat_std", 0)),
            float(features.get("iat_min", 0)),
            float(features.get("iat_max", 0)),
            float(features.get("direction_ratio", 0.5)),
            float(features.get("bytes_per_s", 0)),
            float(features.get("packets_per_s", 0)),
            float(features.get("burstiness", 0)),
            float(features.get("duration_s", 0)),
            # protocol one-hot-ish
            1.0 if str(features.get("protocol", "")).upper() == "TCP" else 0.0,
            1.0 if str(features.get("protocol", "")).upper() == "UDP" else 0.0,
            float(features.get("src_port", 0) or 0),
            float(features.get("dst_port", 0) or 0),
        ]

    def _train_on_dataset(self, rows: Sequence[dict]) -> None:
        X = np.array([self._feature_vector(r) for r in rows])
        y = np.array([str(r["label"]) for r in rows])

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        rf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=1)
        hgb = HistGradientBoostingClassifier(random_state=42)

        rf.fit(X_train, y_train)
        hgb.fit(X_train, y_train)

        rf_f1 = f1_score(y_test, rf.predict(X_test), average="macro")
        hgb_f1 = f1_score(y_test, hgb.predict(X_test), average="macro")

        best = rf if rf_f1 >= hgb_f1 else hgb
        self._model = CalibratedClassifierCV(best, cv=3, method="isotonic")
        self._model.fit(X_train, y_train)
        self._trained = True

    def train(self) -> dict:
        """Train on the synthetic traffic-gen dataset. Returns metrics."""

        rows = generate_flow_dataset(seed=42, rows_per_class=200)
        self._feature_names = [
            "pkt_len_mean", "pkt_len_std", "pkt_len_min", "pkt_len_max",
            "iat_mean", "iat_std", "iat_min", "iat_max",
            "direction_ratio", "bytes_per_s", "packets_per_s",
            "burstiness", "duration_s",
            "proto_tcp", "proto_udp", "src_port", "dst_port",
        ]
        self._train_on_dataset(rows)

        # Evaluate on held-out
        test_rows = generate_flow_dataset(seed=99, rows_per_class=50)
        X_test = np.array([self._feature_vector(r) for r in test_rows])
        y_test = np.array([str(r["label"]) for r in test_rows])
        y_pred = self._model.predict(X_test)
        macro_f1 = float(f1_score(y_test, y_pred, average="macro"))
        return {"macro_f1": macro_f1, "n_test": len(test_rows)}

    def predict(self, features: dict) -> ClassifierResult:
        """Classify a flow. ML if trained and confident, else rule baseline."""

        rule_class, rule_conf = rule_classify(features)

        if not self._trained:
            return ClassifierResult(
                predicted_class=rule_class,
                confidence=rule_conf,
                probabilities={rule_class: rule_conf},
                method="rule",
                top_features=[],
            )

        X = np.array([self._feature_vector(features)])
        probs = self._model.predict_proba(X)[0]
        classes = list(self._model.classes_)
        prob_dict = {str(c): float(p) for c, p in zip(classes, probs)}
        best_idx = int(np.argmax(probs))
        best_class = str(classes[best_idx])
        best_conf = float(probs[best_idx])

        if best_conf < self.threshold:
            return ClassifierResult(
                predicted_class=rule_class,
                confidence=rule_conf,
                probabilities=prob_dict,
                method="rule",
                top_features=self._top_features(features),
            )

        return ClassifierResult(
            predicted_class=best_class,
            confidence=best_conf,
            probabilities=prob_dict,
            method="ml",
            top_features=self._top_features(features),
        )

    def _top_features(self, features: dict) -> list[tuple[str, float]]:
        """Simple feature importance proxy: magnitude of deviation from baseline."""

        scored: list[tuple[str, float]] = []
        for key in ("bytes_per_s", "packets_per_s", "direction_ratio", "burstiness", "duration_s", "pkt_len_mean"):
            val = abs(float(features.get(key, 0) or 0))
            scored.append((key, val))
        scored.sort(key=lambda x: -x[1])
        return scored[:5]


# ---- singleton -------------------------------------------------------------

_classifier: FlowClassifier | None = None


def get_classifier() -> FlowClassifier:
    global _classifier
    if _classifier is None:
        _classifier = FlowClassifier()
    return _classifier
