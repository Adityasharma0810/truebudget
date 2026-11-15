"""SQLite WAL storage layer for the Experience Budget agent.

Schema v1 (§7 tables): agent_identity, sessions, devices, flows, flow_features,
metrics_raw, metrics_agg, qoe_scores, capacity_history, forecasts, intents,
conflicts, policies, policy_applications, decisions (hash-chained), corrections,
models, experiments, experiment_runs, schedules, health_events, links, audit_log,
settings, feature_flags.

All queries are parameterised.  WAL mode, integrity check at startup, retention
jobs, VACUUM schedule, backup/restore commands.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Iterable

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS schema_version (
  version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS agent_identity (
  agent_id TEXT PRIMARY KEY,
  public_key TEXT NOT NULL,
  private_key TEXT NOT NULL,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner','viewer')),
  created_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  revoked_at REAL,
  access_token_hash TEXT,
  refresh_token_hash TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  mac TEXT,
  ipv4 TEXT,
  ipv6 TEXT,
  assigned_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS flows (
  flow_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  class TEXT NOT NULL,
  src_ip TEXT,
  dst_ip TEXT,
  src_port INTEGER,
  dst_port INTEGER,
  protocol TEXT,
  first_seen REAL NOT NULL,
  last_seen REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS flow_features (
  flow_id TEXT PRIMARY KEY,
  pkt_len_mean REAL, pkt_len_std REAL, pkt_len_min REAL, pkt_len_max REAL,
  iat_mean REAL, iat_std REAL, iat_min REAL, iat_max REAL,
  direction_ratio REAL, bytes_per_s REAL, packets_per_s REAL,
  burstiness REAL, duration_s REAL, protocol TEXT, port_hint TEXT,
  updated_at REAL NOT NULL,
  FOREIGN KEY(flow_id) REFERENCES flows(flow_id)
);

CREATE TABLE IF NOT EXISTS metrics_raw (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  device_id TEXT,
  flow_id TEXT,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'conntrack'
);

CREATE TABLE IF NOT EXISTS metrics_agg (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts_bucket REAL NOT NULL,
  granularity TEXT NOT NULL CHECK (granularity IN ('15m','24h','30d')),
  device_id TEXT,
  metric TEXT NOT NULL,
  avg_value REAL, min_value REAL, max_value REAL, p95_value REAL,
  count INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS qoe_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  device_id TEXT NOT NULL,
  class TEXT NOT NULL,
  qoe REAL,
  band TEXT,
  confidence REAL,
  source TEXT NOT NULL DEFAULT 'probes'
);

CREATE TABLE IF NOT EXISTS capacity_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  capacity_mbps REAL NOT NULL,
  variance REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'kalman'
);

CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  horizon_s REAL NOT NULL,
  capacity_mbps REAL NOT NULL,
  lo_mbps REAL, hi_mbps REAL,
  confidence REAL NOT NULL,
  method TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intents (
  intent_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL,
  traffic_class TEXT NOT NULL,
  goal TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 0,
  deadline REAL,
  duration_s REAL,
  min_service_floor INTEGER NOT NULL DEFAULT 1,
  expires_at REAL NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active','expired','cancelled','reverted')),
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS conflicts (
  conflict_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('open','accepted','adjusted','cancelled')),
  created_at REAL NOT NULL,
  resolved_at REAL
);

CREATE TABLE IF NOT EXISTS policies (
  policy_id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  capacity_mbps REAL NOT NULL,
  headroom REAL NOT NULL,
  rationale TEXT NOT NULL,
  infeasible INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS policy_applications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  policy_id TEXT NOT NULL,
  op_id TEXT NOT NULL,
  applied_at REAL NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('applied','rolled_back','escalated')),
  FOREIGN KEY(policy_id) REFERENCES policies(policy_id)
);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  trigger TEXT NOT NULL,
  reason TEXT NOT NULL,
  before_qoe TEXT NOT NULL,
  after_qoe TEXT NOT NULL,
  evidence TEXT NOT NULL,
  verdict TEXT NOT NULL CHECK (verdict IN ('kept','rolled_back','escalated')),
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY,
  flow_id TEXT NOT NULL,
  suggested_class TEXT NOT NULL,
  confirmed_class TEXT,
  reviewed_by TEXT,
  reviewed_at REAL,
  state TEXT NOT NULL CHECK (state IN ('pending','approved','rejected')),
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
  model_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  version INTEGER NOT NULL,
  path TEXT NOT NULL,
  macro_f1 REAL,
  active INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
  experiment_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('queued','running','stopped','completed')),
  created_at REAL NOT NULL,
  stopped_at REAL
);

CREATE TABLE IF NOT EXISTS experiment_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL,
  controller TEXT NOT NULL,
  seed INTEGER NOT NULL,
  trial INTEGER NOT NULL,
  metrics TEXT NOT NULL,
  completed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
  schedule_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  start_ts REAL NOT NULL,
  duration_s REAL NOT NULL,
  traffic_class TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active','expired','disabled')),
  created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS health_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  signal TEXT NOT NULL,
  value REAL,
  hypothesis TEXT,
  confidence REAL
);

CREATE TABLE IF NOT EXISTS links (
  link_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  capacity_mbps REAL,
  rtt_ms REAL,
  loss_pct REAL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT NOT NULL,
  result TEXT NOT NULL,
  details TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_flags (
  flag TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  maturity TEXT NOT NULL DEFAULT 'experimental',
  updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_raw_ts ON metrics_raw(ts);
CREATE INDEX IF NOT EXISTS idx_metrics_raw_device ON metrics_raw(device_id);
CREATE INDEX IF NOT EXISTS idx_metrics_agg_bucket ON metrics_agg(ts_bucket);
CREATE INDEX IF NOT EXISTS idx_qoe_scores_ts ON qoe_scores(ts);
CREATE INDEX IF NOT EXISTS idx_capacity_history_ts ON capacity_history(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_intents_expires ON intents(expires_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);
"""

MIGRATIONS: dict[int, str] = {1: SCHEMA_SQL}


class Store:
    """SQLite WAL store with versioned migrations and retention."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    # ------------------------------------------------------------------ init

    def _init(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        # Ensure the version table exists before checking it.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
        )
        cur = self._conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cur.fetchone()
        current = row["version"] if row else 0
        for version in sorted(MIGRATIONS):
            if version > current:
                self._conn.executescript(MIGRATIONS[version])
                self._conn.execute(
                    "INSERT OR REPLACE INTO schema_version(version) VALUES (?)",
                    (version,),
                )
                current = version
        self.integrity_check()

    # ------------------------------------------------------------------ integrity / backup / vacuum

    def integrity_check(self) -> bool:
        cur = self._conn.execute("PRAGMA integrity_check")
        row = cur.fetchone()
        ok = row[0] == "ok"
        if not ok:
            raise RuntimeError(f"database integrity check failed: {row[0]}")
        return ok

    def backup(self, dest: Path | str) -> Path:
        """Online backup using SQLite's backup API (handles WAL correctly)."""

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest_conn = sqlite3.connect(str(dest))
        try:
            self._conn.backup(dest_conn)
        finally:
            dest_conn.close()
        return dest

    def restore(self, src: Path | str) -> None:
        src = Path(src)
        self._conn.close()
        shutil.copy2(src, self.path)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def vacuum(self) -> None:
        self._conn.execute("VACUUM")

    # ------------------------------------------------------------------ generic execute

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, params: Iterable[Iterable]) -> sqlite3.Cursor:
        return self._conn.executemany(sql, [tuple(p) for p in params])

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ retention

    def retention(self, raw_seconds: int = 86400, agg_days: int = 30) -> None:
        """Delete raw features older than raw_seconds; aggregates older than agg_days."""

        now = time.time()
        self._conn.execute(
            "DELETE FROM metrics_raw WHERE ts < ?", (now - raw_seconds,)
        )
        self._conn.execute(
            "DELETE FROM flow_features WHERE updated_at < ?",
            (now - raw_seconds,),
        )
        self._conn.execute(
            "DELETE FROM metrics_agg WHERE ts_bucket < ?",
            (now - agg_days * 86400,),
        )
        self._conn.execute(
            "DELETE FROM qoe_scores WHERE ts < ?", (now - raw_seconds,)
        )
