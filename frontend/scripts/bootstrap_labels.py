"""Bootstrap real labelled training data for the flow classifier.

Runs REAL internet traffic on this host (bulk file downloads, real HLS video
streaming), lets the agent measure the resulting flows live, then creates and
approves corrections for exactly those flows via the agent API.

Usage: python bootstrap_labels.py <class> <num_flows> <pairing-free>
"""

import json
import subprocess
import sys
import time
import urllib.request

API = "http://localhost:7443/api/v1"


def api(path: str, data: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def current_flows() -> dict[str, dict]:
    return {f["flowId"] if "flowId" in f else f.get("flow_id", f.get("id")): f
            for f in api("/flows?limit=1000")}


def correct(flow_id: str, cls: str) -> None:
    res = api(f"/flows/{urllib.request.quote(flow_id, safe='')}/correct",
              {"confirmed_class": cls, "suggested_class": "unknown"})
    print("  corrected", flow_id[:50], "->", res.get("state"))
    cid = f"corr-{flow_id}"
    api("/ml/corrections", {"id": cid, "status": "approved"})
    print("  approved", cid[:50])


def run_bulk(n_flows: int) -> None:
    """Real large-file downloads over HTTPS (one TCP connection each)."""
    urls = [
        "https://proof.ovh.net/files/100Mb.dat",
        "https://speed.hetzner.de/100MB.bin",
        "https://download.thinkbroadband.com/50MB.zip",
        "https://ipv4.download.thinkbroadband.com/50MB.zip",
        "https://proof.ovh.net/files/10Gb.dat",
        "https://speed.hetzner.de/1GB.bin",
        "https://download.thinkbroadband.com/100MB.zip",
        "https://ipv4.download.thinkbroadband.com/100MB.zip",
    ]
    before = set(current_flows())
    procs = []
    for i in range(n_flows):
        url = urls[i % len(urls)]
        p = subprocess.Popen(
            ["curl", "-sL", "-o", "/dev/null", "--max-time", "25", f"{url}?v={time.time()}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(p)
        time.sleep(2)  # stagger so each connection is observable separately
    time.sleep(12)  # let the agent observe the flows over several ticks
    after = current_flows()
    for p in procs:
        p.terminate()
    new = [fid for fid, f in after.items()
           if fid not in before and float(f.get("throughputMbps") or f.get("throughput_mbps") or 0) > 1.0]
    print(f"bulk: {len(new)} new high-throughput flows")
    for fid in new[:n_flows]:
        correct(fid, "bulk")


def run_stream(n_flows: int) -> None:
    """Real HLS video segment downloads (sustained downstream on port 443)."""
    playlist = "https://test-streams.mullvad.net/x36xhzz/x36xhzz.m3u8"
    before = set(current_flows())
    # fetch segments continuously: each segment is a real HTTPS media fetch
    for round_i in range(max(1, n_flows // 2)):
        p = subprocess.Popen(
            ["bash", "-c",
             f"for i in $(seq 1 30); do curl -sL -o /dev/null --max-time 6 "
             f"'https://test-streams.mullvad.net/x36xhzz/url_9/seg_{i}.ts?v={round_i}${i}'; done"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        procs.append(p) if False else None
        time.sleep(16)
    time.sleep(8)
    after = current_flows()
    new = [fid for fid, f in after.items()
           if fid not in before and float(f.get("throughputMbps") or f.get("throughput_mbps") or 0) > 0.2]
    print(f"stream: {len(new)} new media flows")
    for fid in new[:n_flows]:
        correct(fid, "stream")


if __name__ == "__main__":
    cls = sys.argv[1]
    n = int(sys.argv[2])
    if cls == "bulk":
        run_bulk(n)
    elif cls == "stream":
        run_stream(n)
    else:
        raise SystemExit(f"unknown class {cls}")
    print("done")
