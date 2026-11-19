"""Flow classifier: rule baseline + RF/HGB with calibrated confidence.

Classes: call, game, stream, bulk, unknown.
Below threshold (default 0.6) → unknown, rule baseline decides policy.
Never inspects payloads; features only.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score
from sklearn.model_selection import train_test_split

from eb_agent.lab.traffic_gen import CLASSES, generate_flow_dataset

CLASS_LABELS = list(CLASSES)
DEFAULT_THRESHOLD = 0.6

# Only features the agent can measure for a live flow belong here.  Earlier
# versions also used packet-size / inter-arrival distributions, but the live
# collectors cannot observe those; training on them created a silent domain
# shift (the missing values were zeros).  Keep the training rows and the live
# feature dict on this exact contract.
FEATURE_NAMES = [
    "pkt_len_mean",
    "iat_mean_ms",
    "direction_ratio",
    "bytes_per_s",
    "packets_per_s",
    "duration_s",
    "proto_tcp",
    "proto_udp",
    "dst_port",
]


def normalise_features(row: dict) -> dict:
    """Map training-row aliases onto the live feature contract.

    The lab generator stores human-readable names (``iat_mean_ms``,
    ``down_up_ratio``, ``duration_ms``, ``pkts_per_s``, ``port``); live
    collectors use the short names.  Missing/unmeasured values are not
    invented: they fall back to the documented neutral value.
    """

    down_up = row.get("down_up_ratio")
    direction = row.get("direction_ratio")
    if direction is None:
        # ``down_up_ratio`` is downstream/upstream; the live feature is the
        # outbound share of bytes.  Convert instead of treating them as equal.
        direction = 1.0 / (1.0 + float(down_up)) if down_up is not None else 0.5
    duration_s = row.get("duration_s")
    if duration_s is None and row.get("duration_ms") is not None:
        duration_s = float(row["duration_ms"]) / 1000.0
    iat_mean = row.get("iat_mean_ms", row.get("iat_mean"))
    if iat_mean is None:
        packets_per_s = row.get("packets_per_s", row.get("pkts_per_s"))
        iat_mean = 1000.0 / float(packets_per_s) if packets_per_s else 0.0
    return {
        "pkt_len_mean": float(row.get("pkt_len_mean", 0.0) or 0.0),
        "iat_mean_ms": float(iat_mean or 0.0),
        "direction_ratio": float(direction if direction is not None else 0.5),
        "bytes_per_s": float(row.get("bytes_per_s", 0.0) or 0.0),
        "packets_per_s": float(
            row.get("packets_per_s", row.get("pkts_per_s", 0.0)) or 0.0
        ),
        "duration_s": float(duration_s or 0.0),
        "proto_tcp": 1.0
        if str(row.get("protocol", "")).upper() == "TCP"
        else 0.0,
        "proto_udp": 1.0
        if str(row.get("protocol", "")).upper() == "UDP"
        else 0.0,
        "dst_port": float(row.get("dst_port", row.get("port", 0)) or 0),
    }

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
    model_evaluated: bool = False
    model_class: str | None = None
    model_confidence: float = 0.0
    model_skip_reason: str | None = None


class FlowClassifier:
    """Rule baseline + ML (RF/HGB) classifier with calibrated confidence."""

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._model = None
        self._calibrated = None
        self._feature_names: list[str] = []
        self._trained = False
        self._model_kind = ""
        self._lock = threading.Lock()

    @property
    def is_trained(self) -> bool:
        return bool(self._trained and self._model is not None)

    @property
    def model_kind(self) -> str:
        return self._model_kind

    def _feature_vector(self, features: dict) -> list[float]:
        normalised = normalise_features(features)
        return [float(normalised[name]) for name in FEATURE_NAMES]

    def _train_on_dataset(self, rows: Sequence[dict]) -> None:
        X = np.array([self._feature_vector(r) for r in rows])
        y = np.array([str(r["label"]) for r in rows])

        # A class with a single sample cannot be stratified; fall back to a
        # random split in that (rare, correction-heavy) case.
        counts = {label: int((y == label).sum()) for label in set(y)}
        stratify = y if min(counts.values()) >= 3 else None
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=stratify
        )

        rf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=1)
        hgb = HistGradientBoostingClassifier(random_state=42)

        rf.fit(X_train, y_train)
        hgb.fit(X_train, y_train)

        rf_f1 = f1_score(y_test, rf.predict(X_test), average="macro")
        hgb_f1 = f1_score(y_test, hgb.predict(X_test), average="macro")

        best = rf if rf_f1 >= hgb_f1 else hgb
        self._model_kind = type(best).__name__
        try:
            calibrated = CalibratedClassifierCV(best, cv=3, method="isotonic")
            calibrated.fit(X_train, y_train)
            self._model = calibrated
            self._model_kind = f"{type(best).__name__} + isotonic calibration"
        except Exception:
            # Calibration is an accuracy aid, not a correctness requirement.
            self._model = best
        self._trained = True

    @staticmethod
    def _rule_class(row: dict) -> str:
        observed = normalise_features(row)
        observed["protocol"] = (
            "TCP" if str(row.get("protocol", "")).upper() == "TCP" else "UDP"
        )
        observed["dst_port"] = int(observed["dst_port"])
        return rule_classify(observed)[0]

    def _evaluate(self, rows: Sequence[dict]) -> dict:
        """Evaluate the trained model and the port-rule baseline on the same rows."""

        X = np.array([self._feature_vector(r) for r in rows])
        y_true = np.array([str(r["label"]) for r in rows])
        y_pred = self._model.predict(X)
        labels = [c for c in CLASS_LABELS if c in set(y_true) or c in set(y_pred)]
        matrix = confusion_matrix(y_true, y_pred, labels=labels).tolist()
        baseline_pred = np.array([self._rule_class(row) for row in rows])
        return {
            "accuracy": float(accuracy_score(y_true, y_pred)),
            "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
            "baseline_accuracy": float(accuracy_score(y_true, baseline_pred)),
            "baseline_f1": float(
                f1_score(y_true, baseline_pred, average="macro", zero_division=0)
            ),
            "matrix": matrix,
            "labels": labels,
            "n_test": len(rows),
        }

    def train(self, extra_rows: Sequence[dict] | None = None) -> dict:
        """Train on synthetic lab features plus approved corrections.

        The held-out evaluation uses a *separate* synthetic dataset (seed 99)
        never shown to the model, so the reported matrix is a genuine holdout
        for that data source.  It is still lab data, not ISP measurements —
        callers must keep that label attached.
        """

        with self._lock:
            rows = list(generate_flow_dataset(seed=42, rows_per_class=200))
            extra = [r for r in (extra_rows or []) if r.get("label") in CLASS_LABELS]
            rows.extend(extra)
            self._feature_names = list(FEATURE_NAMES)
            self._train_on_dataset(rows)

            # Evaluate on held-out holdout generated with a different seed.
            test_rows = generate_flow_dataset(seed=99, rows_per_class=50)
            metrics = self._evaluate(test_rows)
            metrics.update(
                {
                    "n_train": len(rows),
                    "n_extra": len(extra),
                    "model_kind": self._model_kind,
                    "threshold": float(self.threshold),
                    "source": "synthetic_lab_holdout",
                    "note": (
                        "Trained and evaluated on the deterministic lab traffic "
                        "generator; held-out seed 99. Not evidence of real-ISP "
                        "accuracy."
                    ),
                }
            )
            return metrics

    def predict(self, features: dict) -> ClassifierResult:
        """Classify a flow. ML if trained and confident, else rule baseline."""

        rule_class, rule_conf = rule_classify(features)
        model = self._model
        trained = self._trained and model is not None

        # Domain guard: the local model expects every feature it was trained
        # on.  A flow without measured packet size, packet rate, duration, or
        # inter-arrival time is out of that domain, so the rule baseline
        # answers instead of forcing an overconfident guess.
        observed = normalise_features(features)
        if trained and any(
            float(observed[name]) <= 0.0
            for name in ("pkt_len_mean", "iat_mean_ms", "packets_per_s", "duration_s")
        ):
            return ClassifierResult(
                predicted_class=rule_class,
                confidence=rule_conf,
                probabilities={rule_class: rule_conf},
                method="rule",
                top_features=[],
                model_skip_reason="domain_guard",
            )

        if not trained:
            return ClassifierResult(
                predicted_class=rule_class,
                confidence=rule_conf,
                probabilities={rule_class: rule_conf},
                method="rule",
                top_features=[],
                model_skip_reason="untrained",
            )

        X = np.array([self._feature_vector(features)])
        probs = model.predict_proba(X)[0]
        classes = list(model.classes_)
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
                model_evaluated=True,
                model_class=best_class,
                model_confidence=best_conf,
            )

        return ClassifierResult(
            predicted_class=best_class,
            confidence=best_conf,
            probabilities=prob_dict,
            method="ml",
            top_features=self._top_features(features),
            model_evaluated=True,
            model_class=best_class,
            model_confidence=best_conf,
        )

    # ---- persistence -------------------------------------------------------

    def save(self, path: Path) -> Path:
        """Persist the fitted model so restarts reuse the same version."""

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if not self.is_trained:
                raise RuntimeError("cannot save an untrained classifier")
            payload = {
                "model": self._model,
                "feature_names": self._feature_names,
                "threshold": self.threshold,
                "model_kind": self._model_kind,
            }
            joblib.dump(payload, path)
        return path

    def load(self, path: Path) -> bool:
        """Load a persisted model. Returns False when incompatible/missing."""

        path = Path(path)
        if not path.is_file():
            return False
        try:
            payload = joblib.load(path)
            model = payload["model"]
            expected = len(self._feature_vector({}))
            if int(getattr(model, "n_features_in_", expected)) != expected:
                return False
            self._model = model
            self._feature_names = list(payload.get("feature_names", []))
            self._model_kind = str(payload.get("model_kind", type(model).__name__))
            self._trained = True
            return True
        except Exception:
            return False

    def _top_features(self, features: dict) -> list[tuple[str, float]]:
        """Deterministic feature summary for the decision panel.

        These are raw observed values, not Shapley/feature-importance scores;
        the label in the UI says so.
        """

        observed = normalise_features(features)
        scored = [(name, float(observed[name])) for name in FEATURE_NAMES]
        scored.sort(key=lambda item: -abs(item[1]))
        return scored[:5]


# ---- singleton -------------------------------------------------------------

_classifier: FlowClassifier | None = None


def get_classifier() -> FlowClassifier:
    global _classifier
    if _classifier is None:
        _classifier = FlowClassifier()
    return _classifier
