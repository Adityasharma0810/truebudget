#!/usr/bin/env bash
# Reset an interrupted Experience Budget lab. Safe to run without sudo.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHONPATH=agent .venv/bin/python - <<'PY'
from eb_agent.lab.topology import reset_stale_labs

count = reset_stale_labs()
print(f"reset {count} recorded lab namespace process(es)")
PY

# The lab is designed to run in a private user+network namespace.  Any qdiscs
# it created disappear with that namespace; never touch the host's real links.
rm -f /tmp/eb-lab-state-*.json
echo "lab reset complete (host network untouched)"
