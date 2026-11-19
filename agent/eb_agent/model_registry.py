"""Runtime model registry for the flow classifier.

The agent trains a local classifier from the deterministic lab generator (and
approved human corrections), stores the artifact under the agent data
directory, and records every version plus its honest holdout metrics in SQLite.
Nothing here talks to a network service.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from eb_agent.classifier import FlowClassifier
from eb_agent.store import Store

MODEL_KIND = "classifier"


def model_dir(store: Store, data_dir: Path | str | None = None) -> Path:
    base = Path(data_dir) if data_dir is not None else store.path.parent
    path = base / "models" / MODEL_KIND
    path.mkdir(parents=True, exist_ok=True)
    return path


def _next_version(store: Store) -> int:
    row = store.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 AS version FROM models WHERE kind=?",
        (MODEL_KIND,),
    ).fetchone()
    return int(row["version"] if row else 1)


def _active_row(store: Store) -> Any:
    return store.execute(
        "SELECT model_id, kind, version, path, macro_f1, active, created_at "
        "FROM models WHERE kind=? AND active=1 ORDER BY version DESC LIMIT 1",
        (MODEL_KIND,),
    ).fetchone()


def _metrics_for(store: Store, model_id: str) -> dict[str, Any]:
    row = store.execute(
        "SELECT metrics FROM model_metrics WHERE model_id=?", (model_id,)
    ).fetchone()
    if not row:
        return {}
    try:
        return json.loads(row["metrics"])
    except (TypeError, ValueError):
        return {}


def _record(
    store: Store,
    *,
    version: int,
    path: Path,
    metrics: dict[str, Any],
    activate: bool,
) -> str:
    model_id = f"{MODEL_KIND}-v{version}-{int(time.time())}"
    now = time.time()
    if activate:
        store.execute(
            "UPDATE models SET active=0 WHERE kind=?", (MODEL_KIND,)
        )
    store.execute(
        "INSERT INTO models(model_id, kind, version, path, macro_f1, active, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            model_id,
            MODEL_KIND,
            version,
            str(path),
            float(metrics.get("macro_f1") or 0.0),
            1 if activate else 0,
            now,
        ),
    )
    store.execute(
        "INSERT OR REPLACE INTO model_metrics(model_id, metrics, created_at) VALUES (?, ?, ?)",
        (model_id, json.dumps(metrics), now),
    )
    store.commit()
    return model_id


def train_and_activate(
    store: Store,
    classifier: FlowClassifier,
    *,
    data_dir: Path | str | None = None,
    extra_rows: Sequence[dict] | None = None,
    reason: str = "manual",
) -> dict[str, Any]:
    """Train a new model version, save it, and make it active."""

    metrics = classifier.train(extra_rows)
    version = _next_version(store)
    artifact = model_dir(store, data_dir) / f"v{version}.joblib"
    classifier.save(artifact)
    model_id = _record(
        store, version=version, path=artifact, metrics=metrics, activate=True
    )
    metrics.update(
        {
            "model_id": model_id,
            "version": version,
            "artifact": str(artifact),
            "reason": reason,
            "trained_at": time.time(),
        }
    )
    return metrics


def load_active(
    store: Store, classifier: FlowClassifier, *, data_dir: Path | str | None = None
) -> dict[str, Any] | None:
    """Load the active persisted model, or return None if unusable."""

    row = _active_row(store)
    if not row:
        return None
    if classifier.load(Path(row["path"])):
        metrics = _metrics_for(store, row["model_id"])
        metrics.update(
            {
                "model_id": row["model_id"],
                "version": int(row["version"]),
                "artifact": row["path"],
                "loaded": True,
            }
        )
        return metrics
    # The artifact is gone or was written by an incompatible version.  Mark it
    # inactive so a fresh local train can take over without silently claiming
    # that a model is running.
    store.execute("UPDATE models SET active=0 WHERE model_id=?", (row["model_id"],))
    store.commit()
    return None


def ensure_classifier(
    store: Store,
    classifier: FlowClassifier,
    *,
    data_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Load the active model at startup, training one when none exists."""

    loaded = load_active(store, classifier, data_dir=data_dir)
    if loaded is not None:
        return {"ok": True, "loaded": True, "model": loaded}
    metrics = train_and_activate(
        store,
        classifier,
        data_dir=data_dir,
        reason="startup_untrained",
    )
    return {"ok": True, "loaded": False, "trained": True, "model": metrics}


def rollback(
    store: Store, classifier: FlowClassifier
) -> dict[str, Any]:
    """Activate the previous model version and load its artifact."""

    active = _active_row(store)
    if not active:
        return {
            "ok": False,
            "rolledBack": False,
            "reason": "No active model version exists yet.",
        }
    previous = store.execute(
        "SELECT model_id, kind, version, path, macro_f1, active, created_at "
        "FROM models WHERE kind=? AND version < ? ORDER BY version DESC LIMIT 1",
        (MODEL_KIND, int(active["version"])),
    ).fetchone()
    if not previous:
        return {
            "ok": False,
            "rolledBack": False,
            "activeVersion": int(active["version"]),
            "reason": "No earlier model version is available to roll back to.",
        }
    if not classifier.load(Path(previous["path"])):
        return {
            "ok": False,
            "rolledBack": False,
            "activeVersion": int(active["version"]),
            "reason": (
                "The previous model artifact is missing or incompatible; "
                "nothing was changed."
            ),
        }
    store.execute("UPDATE models SET active=0 WHERE kind=?", (MODEL_KIND,))
    store.execute(
        "UPDATE models SET active=1 WHERE model_id=?", (previous["model_id"],)
    )
    store.commit()
    metrics = _metrics_for(store, previous["model_id"])
    return {
        "ok": True,
        "rolledBack": True,
        "model_id": previous["model_id"],
        "version": int(previous["version"]),
        "macro_f1": float(previous["macro_f1"] or 0.0),
        "metrics": metrics,
        "reason": f"Active model rolled back to v{int(previous['version'])}.",
    }


def active_summary(store: Store) -> dict[str, Any]:
    """Return the active model row plus its stored metrics, if any."""

    row = _active_row(store)
    if not row:
        return {"trained": False, "model": None, "metrics": {}}
    metrics = _metrics_for(store, row["model_id"])
    return {
        "trained": True,
        "model": {
            "model_id": row["model_id"],
            "kind": row["kind"],
            "version": int(row["version"]),
            "path": row["path"],
            "macro_f1": float(row["macro_f1"] or 0.0),
            "created_at": float(row["created_at"]),
        },
        "metrics": metrics,
    }


def list_models(store: Store) -> list[dict[str, Any]]:
    rows = store.execute(
        "SELECT model_id, kind, version, path, macro_f1, active, created_at "
        "FROM models WHERE kind=? ORDER BY version DESC",
        (MODEL_KIND,),
    ).fetchall()
    return [
        {
            "model_id": r["model_id"],
            "kind": r["kind"],
            "version": int(r["version"]),
            "path": r["path"],
            "macro_f1": float(r["macro_f1"] or 0.0),
            "active": bool(r["active"]),
            "created_at": float(r["created_at"]),
            "metrics": _metrics_for(store, r["model_id"]),
        }
        for r in rows
    ]


def approved_correction_rows(store: Store, limit: int = 500) -> list[dict[str, Any]]:
    """Features for approved human corrections, ready for retraining."""

    rows = store.execute(
        "SELECT c.flow_id, c.confirmed_class, f.pkt_len_mean, f.pkt_len_std, "
        "f.pkt_len_min, f.pkt_len_max, f.iat_mean, f.iat_std, f.iat_min, f.iat_max, "
        "f.direction_ratio, f.bytes_per_s, f.packets_per_s, f.burstiness, f.duration_s, "
        "f.protocol, fl.src_port, fl.dst_port "
        "FROM corrections c JOIN flow_features f ON f.flow_id=c.flow_id "
        "LEFT JOIN flows fl ON fl.flow_id=c.flow_id "
        "WHERE c.state='approved' AND c.confirmed_class IS NOT NULL "
        "ORDER BY c.created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        label = item.pop("confirmed_class", None)
        if not label:
            continue
        item["label"] = label
        item["flow_id"] = item.get("flow_id")
        out.append(item)
    return out
