"""Lab scenario definitions and HTML report generator for Phase 6."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class Scenario:
    name: str
    description: str
    wan_capacity_mbps: float
    duration_s: float
    workloads: list[dict] = field(default_factory=list)
    seed: int = 42
    trials: int = 3


SCENARIOS: dict[str, Scenario] = {
    "saturating_call": Scenario(
        name="saturating_call",
        description="Saturating bulk download + simultaneous video call",
        wan_capacity_mbps=50.0,
        duration_s=60,
        workloads=[
            {"kind": "bulk", "target_mbps": None, "duration": 60},
            {"kind": "call", "target_mbps": None, "duration": 60},
        ],
        seed=42,
        trials=3,
    ),
    "gaming_download": Scenario(
        name="gaming_download",
        description="Game session during large background download",
        wan_capacity_mbps=100.0,
        duration_s=60,
        workloads=[
            {"kind": "bulk", "target_mbps": None, "duration": 60},
            {"kind": "game", "target_mbps": None, "duration": 60},
        ],
        seed=7,
        trials=3,
    ),
    "mixed_household": Scenario(
        name="mixed_household",
        description="Mixed household: stream, call, game, bulk",
        wan_capacity_mbps=75.0,
        duration_s=90,
        workloads=[
            {"kind": "stream", "target_mbps": 6.0, "duration": 90},
            {"kind": "call", "target_mbps": None, "duration": 90},
            {"kind": "game", "target_mbps": None, "duration": 90},
            {"kind": "bulk", "target_mbps": None, "duration": 90},
        ],
        seed=99,
        trials=3,
    ),
    "bulk_backfill": Scenario(
        name="bulk_backfill",
        description="Bulk backfill when idle (headroom harvesting)",
        wan_capacity_mbps=40.0,
        duration_s=60,
        workloads=[
            {"kind": "bulk", "target_mbps": None, "duration": 60},
        ],
        seed=2024,
        trials=3,
    ),
}


def get_scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario: {name}; available: {sorted(SCENARIOS)}")
    return SCENARIOS[name]


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS)


def generate_html_report(rows: list[dict], out_path: str) -> str:
    """Generate a simple HTML A/B report from lab CSV rows."""

    # Aggregate per controller
    controllers: dict[str, list[dict]] = {}
    for row in rows:
        controllers.setdefault(row.get("controller", "unknown"), []).append(row)

    rows_html = ""
    for ctrl in sorted(controllers):
        ctrl_rows = controllers[ctrl]
        avg_worst_qoe = sum(r.get("worstQoE", 0) for r in ctrl_rows) / max(1, len(ctrl_rows))
        avg_call_p95 = sum(r.get("call_p95_ms", 0) for r in ctrl_rows) / max(1, len(ctrl_rows))
        avg_bulk = sum(r.get("bulk_mbps", 0) for r in ctrl_rows) / max(1, len(ctrl_rows))
        rows_html += f"""
        <tr>
            <td><strong>{ctrl}</strong></td>
            <td>{len(ctrl_rows)}</td>
            <td>{avg_worst_qoe:.3f}</td>
            <td>{avg_call_p95:.1f} ms</td>
            <td>{avg_bulk:.1f} Mbps</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Experience Budget Lab Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 2rem; }}
  h1 {{ color: #2563eb; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ border: 1px solid #d1d5db; padding: 0.75rem; text-align: left; }}
  th {{ background: #f3f4f6; }}
  tr:nth-child(even) {{ background: #f9fafb; }}
  .badge {{ display: inline-block; padding: 0.125rem 0.5rem; border-radius: 0.25rem;
    font-size: 0.875rem; color: white; }}
  .badge-green {{ background: #10b981; }}
  .badge-amber {{ background: #f59e0b; }}
  .badge-red {{ background: #ef4444; }}
</style>
</head>
<body>
  <h1>Experience Budget Lab Report</h1>
  <p><strong>Generated:</strong> emulated lab run</p>
  <table>
    <thead>
      <tr><th>Controller</th><th>Trials</th><th>Avg Worst QoE</th><th>Avg Call p95</th><th>Avg Bulk Mbps</th></tr>
    </thead>
    <tbody>
      {rows_html}
    </tbody>
  </table>
  <p><em>All traffic emulated. No real user data captured.</em></p>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
