"""Create + approve corrections for real flows selected from the agent DB."""

import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

API = "http://localhost:7443/api/v1"
DB = "/tmp/opencode/eb-phase3/eb.db"


def api(path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def recent_flows(since_s: float, min_bps: float = 0.0, max_bps: float = 1e9) -> list[dict]:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT f.flow_id, ff.bytes_per_s, ff.pkt_len_mean, ff.direction_ratio, "
        "ff.duration_s, ff.packets_per_s, ff.iat_mean, ff.updated_at "
        "FROM flows f JOIN flow_features ff ON ff.flow_id=f.flow_id "
        "WHERE ff.updated_at > ? ORDER BY ff.bytes_per_s DESC LIMIT 100",
        (since_s,),
    ).fetchall()
    return [dict(r) for r in rows
            if min_bps <= (r["bytes_per_s"] or 0) <= max_bps]


def correct(flow_id: str, cls: str) -> None:
    fid = urllib.parse.quote(flow_id, safe="")
    res = api(f"/flows/{fid}/correct", {"confirmed_class": cls, "suggested_class": "unknown"})
    cid = f"corr-{flow_id}"
    api("/ml/corrections", {"id": cid, "status": "approved"})
    print(f"  corrected+approved {flow_id[:48]} -> {cls} ({res.get('state')})")


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "label-from-db":
        # label-from-db <class> <since_ts> <min_bps> <max_bps> <max_n>
        cls, since, lo, hi, n = sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), int(sys.argv[6])
        flows = recent_flows(since, lo, hi)
        print(f"{len(flows)} candidate flows")
        for f in flows[:n]:
            correct(f["flow_id"], cls)
    else:
        raise SystemExit("unknown mode")
