"""QoE degradation warner — the trained LightGBM bundle, live.

Loads ``machine_learning/qoe_only_bundle`` (config.json + qoe_predictor.joblib
+ its own eb_runtime.py) and feeds it real agent measurements once per fast
tick.  Every input is measured: RTT/jitter/loss from the ping probe, per-class
flow counts from the classifier, capacity from the Kalman estimator,
utilisation from the interface counters.  Nothing is simulated.

Queueing delay is derived honestly as ``rtt - unloaded baseline`` where the
baseline is the minimum RTT observed over the last ten minutes — the standard
varying-RTT decomposition.  ``mode_fq`` reports whether the enforcer actually
has fq_codel applied (0 in monitor mode).  Player buffer depth is not
telemetered anywhere, so it stays None and the bundle's own default applies;
the model was trained to tolerate that.
"""

from __future__ import annotations

import collections
import importlib.util
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

import joblib

logger = logging.getLogger(__name__)

_DEFAULT_BUNDLE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "machine_learning" / "qoe_only_bundle"
)


class QoEWarner:
    """Per-device early warning backed by the trained LightGBM bundle."""

    def __init__(self, bundle_dir: Path | str | None = None) -> None:
        self.bundle_dir = Path(bundle_dir) if bundle_dir else _DEFAULT_BUNDLE_DIR
        self.available = False
        self.source = "unavailable"
        self.horizon_s = 10
        self.threshold: float | None = None
        self.model_kind = "qoe_lightgbm"
        self._warner: Any | None = None
        self._rtt_history: collections.deque[float] = collections.deque(maxlen=120)
        # Optional: log every real inference record (features + qoe) to JSONL
        # for later retraining on real data.  Enabled via EB_QOE_LOG=<path>.
        self._log_path: Path | None = (
            Path(os.environ["EB_QOE_LOG"]) if os.environ.get("EB_QOE_LOG") else None
        )
        self._log_file: Any | None = None
        self._load()

    # ------------------------------------------------------------------ setup

    def _load(self) -> None:
        runtime = self.bundle_dir / "eb_runtime.py"
        config = self.bundle_dir / "config.json"
        # A real-trained artifact (env EB_QOE_MODEL) always wins over the
        # shipped bundle: it was fitted on this agent's own measurements.
        real_model = os.environ.get("EB_QOE_MODEL", "")
        model = Path(real_model) if real_model and Path(real_model).is_file() else None
        if model is None:
            model = self.bundle_dir / "qoe_predictor.joblib"
        if not model.exists():
            model = self.bundle_dir.parent / "saved_models" / "qoe_predictor.joblib"
        if not (runtime.exists() and model.exists() and config.exists()):
            logger.info(
                "QoE bundle not found at %s (model=%s); early warning stays off",
                self.bundle_dir,
                model,
            )
            return
        try:
            spec = importlib.util.spec_from_file_location("eb_runtime_qoe", runtime)
            if spec is None or spec.loader is None:
                raise ImportError("cannot load eb_runtime.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Load the trained bundle directly: ExperienceBudgetModels.load()
            # only looks inside its own folder, and the joblib lives in
            # saved_models/.
            bundle = joblib.load(model)
            horizons = sorted(int(h) for h in bundle.get("thresholds", {}))
            self.horizon_s = 10 if 10 in horizons else (horizons[-1] if horizons else 10)
            self._warner = module.QoEWarner(bundle, horizon_s=self.horizon_s)
            self.threshold = float(self._warner.tau)
            self.available = True
            self.source = "ml_real" if real_model and Path(real_model).is_file() else "ml"
            logger.info(
                "QoE degradation warner loaded (horizon=%ds, threshold=%.3f)",
                self.horizon_s,
                self.threshold or 0.0,
            )
        except Exception as error:  # noqa: BLE001 - a broken bundle must not stop the agent
            logger.warning("QoE bundle failed to load: %s", error)
            self._warner = None
            self.available = False

    # ------------------------------------------------------------------ inputs

    def _queue_delay_ms(self, rtt_ms: float) -> float:
        """Current RTT minus the unloaded baseline (rolling minimum)."""

        if rtt_ms <= 0:
            return 0.0
        self._rtt_history.append(rtt_ms)
        baseline = min(self._rtt_history)
        return max(0.0, rtt_ms - baseline)

    def _network_record(
        self,
        flows: list[dict],
        qoe: dict[str, Any],
        capacity_mbps: float | None,
        throughput_mbps: float | None,
    ) -> dict[str, Any]:
        class_counts = {"call": 0, "game": 0, "stream": 0, "bulk": 0}
        for f in flows or []:
            tc = str(f.get("traffic_class") or "")
            if tc in class_counts:
                class_counts[tc] += 1
        cap = float(capacity_mbps or 0.0)
        rtt_ms, jitter_ms, loss_pct = qoe.get("rtt_ms") or 0.0, qoe.get("jitter_ms") or 0.0, qoe.get("loss_pct") or 0.0
        return {
            "qdelay_ms": self._queue_delay_ms(float(rtt_ms)),
            "rho": (float(throughput_mbps or 0.0) / cap) if cap > 0 else 0.0,
            "cap": cap,
            "mode_fq": 1.0 if getattr(self, "_fq_active", False) else 0.0,
            "n_call_net": float(class_counts["call"]),
            "n_game_net": float(class_counts["game"]),
            "n_stream_net": float(class_counts["stream"]),
            "n_bulk_net": float(class_counts["bulk"]),
            # Network-level probes shared by every device record.
            "delay": float(rtt_ms),
            "jitter": float(jitter_ms),
            "loss": float(loss_pct),
        }

    # ------------------------------------------------------------------ inference

    def warn(
        self,
        devices: list[dict],
        flows: list[dict],
        qoe: dict[str, Any],
        capacity_mbps: float | None,
        throughput_mbps: float | None,
        now: float,
    ) -> dict[str, dict[str, Any]]:
        """Push one record per device; returns {device_id: prediction}."""

        if not self.available or self._warner is None:
            return {}
        net = self._network_record(flows, qoe, capacity_mbps, throughput_mbps)
        per_device_flows: dict[str, list[dict]] = {}
        for f in flows or []:
            per_device_flows.setdefault(str(f.get("device_id") or ""), []).append(f)
        device_qoes = qoe.get("devices", {}) if isinstance(qoe, dict) else {}
        out: dict[str, dict[str, Any]] = {}
        for device in devices or []:
            did = str(device.get("device_id") or "")
            if not did:
                continue
            dev_flows = per_device_flows.get(did, [])
            rec: dict[str, Any] = dict(net)
            dev_qoe = device_qoes.get(did, {}) if isinstance(device_qoes, dict) else {}
            score = dev_qoe.get("qoe") if isinstance(dev_qoe, dict) else None
            if dev_flows and score is not None:
                bytes_per_s = sum(float(f.get("bytes_per_s", 0) or 0) for f in dev_flows)
                classes = {str(f.get("traffic_class") or "") for f in dev_flows}
                rec.update(
                    {
                        "thr": bytes_per_s * 8.0 / 1_000_000.0,
                        "n_flows": float(len(dev_flows)),
                        "has_call": 1.0 if "call" in classes else 0.0,
                        "has_game": 1.0 if "game" in classes else 0.0,
                        "has_stream": 1.0 if "stream" in classes else 0.0,
                        "has_bulk": 1.0 if "bulk" in classes else 0.0,
                        "qoe": float(score),
                        "buf_min": None,  # no player telemetry exists; bundle default applies
                    }
                )
            else:
                rec["qoe"] = None  # inactive device: the warner reports state "inactive"
            try:
                result = self._warner.push(did, float(now), rec)
            except Exception as error:  # noqa: BLE001
                logger.debug("qoe warner push failed for %s: %s", did, error)
                continue
            if isinstance(result, dict):
                p_raw = result.get("p_red")
                p_red = None
                if p_raw is not None:
                    try:
                        p_val = float(p_raw)
                        p_red = round(p_val, 4) if not math.isnan(p_val) else None
                    except (TypeError, ValueError):
                        p_red = None
                q_raw = result.get("qoe")
                q_val = None
                if q_raw is not None:
                    try:
                        q_num = float(q_raw)
                        q_val = round(q_num, 4) if not math.isnan(q_num) else None
                    except (TypeError, ValueError):
                        q_val = None
                out[did] = {
                    "state": str(result.get("state", "unknown")),
                    "alert": bool(result.get("alert", False)),
                    "pRed": p_red,
                    "qoe": q_val,
                    "source": str(result.get("source", self.source)),
                    "horizonS": self.horizon_s,
                }
                self._log_sample(did, float(now), rec, result, q_val, p_red)
        return out

    # ------------------------------------------------------------------ real-sample logging

    def _log_sample(
        self,
        device_id: str,
        now: float,
        rec: dict[str, Any],
        result: dict[str, Any],
        qoe: float | None,
        p_red: float | None,
    ) -> None:
        """Append one real inference record to the EB_QOE_LOG JSONL file.

        Every logged value is a real measurement or derived from one; used to
        retrain the warner on this agent's own traffic history.
        """

        if self._log_path is None:
            return
        try:
            if self._log_file is None:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                self._log_file = self._log_path.open("a", encoding="utf-8")
            entry = {
                "device": device_id,
                "t": now,
                "qoe": qoe,
                "state": str(result.get("state", "unknown")),
                "p_red": p_red,
                "record": {k: v for k, v in rec.items() if v is not None},
                "features": result.get("features"),
            }
            self._log_file.write(json.dumps(entry) + "\n")
            self._log_file.flush()
        except Exception as error:  # noqa: BLE001 - logging must never break inference
            logger.debug("qoe warner log failed: %s", error)

    # ------------------------------------------------------------------ introspection

    def status(self) -> dict[str, Any]:
        cfg_path = self.bundle_dir / "config.json"
        cfg: dict[str, Any] = {}
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text()).get("qoe_predictor", {})
            except ValueError:
                cfg = {}
        return {
            "available": self.available,
            "modelKind": self.model_kind,
            "horizonS": self.horizon_s if self.available else None,
            "threshold": self.threshold,
            "source": self.source,
            "bundleDir": str(self.bundle_dir),
            "enabledInConfig": bool(cfg.get("enabled", False)) if cfg else False,
        }
