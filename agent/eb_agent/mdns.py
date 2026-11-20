"""mDNS advertisement for Direct LAN discovery (``_ebagent._tcp``).

Uses ``zeroconf`` when installed.  The advertised properties contain only the
agent id, TLS fingerprint and API path -- never a pairing key or session token.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

logger = logging.getLogger("eb_agent.mdns")

SERVICE_TYPE = "_ebagent._tcp.local."


def _primary_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def start_mdns(
    *,
    agent_id: str,
    port: int,
    fingerprint: str,
    hostname: str | None = None,
) -> Any | None:
    """Start advertising; returns the Zeroconf instance or ``None``."""

    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("zeroconf is not installed; mDNS discovery is disabled")
        return None
    host = hostname or socket.gethostname() or "eb-agent"
    address = _primary_ipv4()
    info = ServiceInfo(
        SERVICE_TYPE,
        f"Experience Budget Agent ({agent_id}).{SERVICE_TYPE}",
        addresses=[socket.inet_aton(address)],
        port=port,
        properties={
            "agent_id": agent_id,
            "path": "/api/v1",
            "tls_fingerprint": fingerprint,
            "scheme": "https",
        },
        server=f"{host}.local.",
    )
    zeroconf = Zeroconf()
    zeroconf.register_service(info)
    logger.info(
        "mDNS advertised",
        extra={"agent_id": agent_id, "address": address, "port": port},
    )
    return zeroconf


def stop_mdns(zeroconf: Any | None) -> None:
    if zeroconf is None:
        return
    try:
        zeroconf.close()
    except Exception:  # noqa: BLE001
        logger.debug("mDNS shutdown failed", exc_info=True)
