"""Retrain the QoE degradation warner on REAL agent measurements.

Input: the JSONL log written by ``eb_agent.qoe_warner`` when ``EB_QOE_LOG``
is set.  Every logged record is a real measurement (RTT/jitter/loss from the
ping probe, per-flow RTT from ``ss``, per-class flow counts, Kalman capacity,
interface throughput).  Nothing simulated enters training.

Labelling (matching the bundle's own definition): a sample at time ``t`` is
positive when the device's real QoE drops below ``red_threshold`` (0.5) at any
sample in ``(t, t + horizon_s]``.

Output: a joblib bundle in the exact format ``eb_runtime.QoEWarner`` expects::

    {"models": {5: model5, 10: model10}, "thresholds": {5: tau5, 10: tau10},
     "red_threshold": 0.5, "features": QOE_FEATURES,
     "fallback_qoe_level": {5: fb, 10: fb}, ...}

Thresholds are chosen on a held-out chronological validation split by
maximising F1 on real data.

Usage:
    python train_qoe_warner_real.py <qoe_log.jsonl> <output.joblib> [--horizon 10]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np

QOE_FEATURES = [
    "qdelay_ms", "qd_d3", "qd_d10", "qd_max10", "rho", "rho_d5", "cap", "cap_ratio5",
    "cap_ratio15", "mode_fq", "n_call_net", "n_game_net", "n_stream_net", "n_bulk_net",
    "delay", "jitter", "loss", "loss_ema", "rtt_d3", "thr_over_cap", "n_flows",
    "has_call", "has_game", "has_stream", "has_bulk", "qoe_now", "qoe_d3", "qoe_d10",
    "qoe_min10", "buf_min", "buf_d3",
]
RED_THRESHOLD = 0.5
FALLBACK_QOE_LEVEL = 0.75


def load_log(path: Path) -> list[dict]:
    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def build_dataset(entries: list[dict], horizon_s: int) -> tuple[list[dict], list[int]]:
    """Return (feature_rows, labels) using only real logged records."""
    per_device: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        dev = str(e.get("device") or "")
        if not dev:
            continue
        per_device[dev].append(e)
    for dev in per_device:
        per_device[dev].sort(key=lambda e: float(e.get("t") or 0.0))

    feats: list[dict] = []
    labels: list[int] = []
    for dev, entries in per_device.items():
        n = len(entries)
        for i, e in enumerate(entries):
            f = e.get("features")
            if not isinstance(f, dict):
                continue  # red/inactive samples carry no feature vector
            t = float(e.get("t") or 0.0)
            # label: any real sample in (t, t + horizon_s] with qoe < red threshold
            positive = 0
            for j in range(i + 1, n):
                tj = float(entries[j].get("t") or 0.0)
                if tj <= t:
                    continue
                if tj > t + horizon_s:
                    break
                qj = entries[j].get("qoe")
                if qj is not None and not (isinstance(qj, float) and math.isnan(qj)):
                    if float(qj) < RED_THRESHOLD:
                        positive = 1
                        break
            feats.append(f)
            labels.append(positive)
    return feats, labels


def pick_threshold(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Threshold maximising F1 on the validation split, clamped to a sane range."""
    best_tau, best_f1 = 0.5, -1.0
    for tau in np.unique(np.round(scores, 4)):
        if tau < 0.05 or tau > 0.95:
            continue
        pred = (scores >= tau).astype(int)
        tp = int(((pred == 1) & (y_true == 1)).sum())
        fp = int(((pred == 1) & (y_true == 0)).sum())
        fn = int(((pred == 0) & (y_true == 1)).sum())
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
        if f1 > best_f1:
            best_f1, best_tau = f1, float(tau)
    return best_tau if best_f1 >= 0 else 0.5


def train_horizon(feats: list[dict], labels: list[int], horizon_s: int) -> dict | None:
    from lightgbm import LGBMClassifier

    n_pos = sum(labels)
    n = len(labels)
    if n < 40 or n_pos < 5:
        print(
            f"[horizon {horizon_s}] insufficient real data: {n} samples, "
            f"{n_pos} positives - skipping"
        )
        return None

    cut = max(1, int(n * 0.8))
    X = np.array([[f.get(k, float("nan")) for k in QOE_FEATURES] for f in feats])
    y = np.array(labels)
    X_tr, y_tr = X[:cut], y[:cut]
    X_va, y_va = X[cut:], y[cut:]
    if len(np.unique(y_tr)) < 2:
        print(f"[horizon {horizon_s}] training split has one class only - skipping")
        return None

    model = LGBMClassifier(
        n_estimators=600, learning_rate=0.05, num_leaves=31, min_child_samples=10,
        scale_pos_weight=max(1.0, float((y_tr == 0).sum()) / max(1, int((y_tr == 1).sum()))),
        random_state=42, verbose=-1,
    )
    model.fit(X_tr, y_tr)

    if len(np.unique(y_va)) < 2:
        # Validation split has no positives: pick the threshold on the training
        # split instead (still real data) and say so.
        scores_tr = model.predict_proba(X_tr)[:, 1]
        tau = pick_threshold(y_tr, scores_tr)
        print(
            f"[horizon {horizon_s}] validation split single-class; "
            f"tau={tau:.4f} picked on the training split"
        )
    else:
        scores = model.predict_proba(X_va)[:, 1]
        tau = pick_threshold(y_va, scores)
        pred = (scores >= tau).astype(int)
        tp = int(((pred == 1) & (y_va == 1)).sum())
        fp = int(((pred == 1) & (y_va == 0)).sum())
        fn = int(((pred == 0) & (y_va == 1)).sum())
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
        print(
            f"[horizon {horizon_s}] real validation: n={len(y_va)} pos={int(y_va.sum())} "
            f"tau={tau:.4f} F1={f1:.3f} (tp={tp} fp={fp} fn={fn})"
        )

    return {
        "model": model,
        "tau": tau,
        "n": n,
        "positives": n_pos,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--horizons", type=int, nargs="*", default=[5, 10])
    args = ap.parse_args()

    entries = load_log(args.log)
    if not entries:
        print("log is empty - nothing to train on")
        return 1
    print(f"loaded {len(entries)} real records from {args.log}")

    models: dict[int, object] = {}
    thresholds: dict[int, float] = {}
    summary: dict[str, dict] = {}
    for h in args.horizons:
        feats, labels = build_dataset(entries, h)
        res = train_horizon(feats, labels, h)
        if res is None:
            continue
        models[h] = res["model"]
        thresholds[h] = res["tau"]
        summary[str(h)] = {
            "n": res["n"], "positives": res["positives"], "tau": res["tau"],
        }

    if not models:
        print("no horizon could be trained on the available real data")
        return 1

    bundle = {
        "models": models,
        "thresholds": thresholds,
        "red_threshold": RED_THRESHOLD,
        "features": QOE_FEATURES,
        "fallback_qoe_level": {h: FALLBACK_QOE_LEVEL for h in models},
        "source": "real_agent_measurements",
        "trained_at": time.time(),
        "summary": summary,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.out)
    print(f"saved real-trained warner bundle to {args.out}")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
