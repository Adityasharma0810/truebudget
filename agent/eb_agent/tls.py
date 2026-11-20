"""Self-signed TLS material for Direct LAN mode.

The agent serves HTTPS on :7443 with a locally generated certificate.  The
SHA-256 fingerprint is shown in the pairing link fragment and in
``/api/v1/agent/info`` so the browser can compare it after the user accepts the
certificate.  Browsers do not let JavaScript install a CA or bypass a
certificate warning, so the UI must be honest about that limit: Direct LAN
requires either a trusted certificate or a one-time browser warning acceptance.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
except ImportError:  # pragma: no cover - cryptography is a documented dependency
    x509 = None  # type: ignore


@dataclass
class TLSMaterial:
    cert_path: Path
    key_path: Path
    fingerprint: str


def _lan_ips() -> list[str]:
    ips: list[str] = []
    try:
        result = subprocess.run(
            ["hostname", "-I"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            for value in result.stdout.split():
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    continue
                if value not in ips:
                    ips.append(value)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not ips:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None):
                value = info[4][0]
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    continue
                if value not in ips:
                    ips.append(value)
        except OSError:
            pass
    return ips


def fingerprint_of(cert_path: Path | str) -> str:
    """Return the colon-separated SHA-256 fingerprint of a PEM certificate."""

    path = Path(cert_path)
    if x509 is None or not path.exists():
        return ""
    cert = x509.load_pem_x509_certificate(path.read_bytes())
    digest = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2)).upper()


def ensure_self_signed_cert(
    data_dir: Path | str,
    *,
    hostname: str | None = None,
    extra_names: list[str] | None = None,
) -> TLSMaterial:
    """Generate (or reuse) a self-signed ECDSA P-256 certificate."""

    if x509 is None:
        raise RuntimeError("cryptography package is required for TLS")
    base = Path(data_dir) / "tls"
    base.mkdir(parents=True, exist_ok=True)
    cert_path = base / "cert.pem"
    key_path = base / "key.pem"
    if cert_path.exists() and key_path.exists():
        return TLSMaterial(cert_path, key_path, fingerprint_of(cert_path))

    host = hostname or socket.gethostname() or "eb-agent"
    names = {"localhost", host, f"{host}.local"}
    if extra_names:
        names.update(extra_names)
    san: list[x509.GeneralName] = [x509.DNSName(name) for name in sorted(names) if name]
    ips = {"127.0.0.1", "::1", *_lan_ips()}
    for value in sorted(ips):
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(value)))
        except ValueError:
            continue

    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, host),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Experience Budget local agent"),
        ]
    )
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    try:
        os.chmod(key_path, 0o600)
        os.chmod(cert_path, 0o644)
    except OSError:
        pass
    return TLSMaterial(cert_path, key_path, fingerprint_of(cert_path))
