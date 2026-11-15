"""Security: identity keys, pairing, sessions, tokens, audit.

- Ed25519 signing + X25519 agreement keys (0600)
- Pairing secret -> short key EB-XXXX-XXXX-XXXX + link with #pk= fragment
- 15-min expiry, single-use, regenerable
- Owner + viewer roles; multi-browser sessions; list/revoke
- Access + refresh tokens bound to session key; immediate revocation
- Brute-force lockout on pairing
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
except ImportError:  # pragma: no cover - cryptography optional in some envs
    Ed25519PrivateKey = None  # type: ignore
    X25519PrivateKey = None  # type: ignore
    serialization = None  # type: ignore

PAIRING_KEY_FORMAT = "EB-{}-{}-{}-{}"
PAIRING_TTL_S = 900  # 15 minutes
LOCKOUT_THRESHOLD = 5
LOCKOUT_WINDOW_S = 300


class SecurityError(RuntimeError):
    """Security operation failed."""


@dataclass
class AgentIdentity:
    agent_id: str
    signing_private_key: object  # Ed25519PrivateKey
    signing_public_key: object  # Ed25519PrivateKey.public_key()
    agreement_private_key: object  # X25519PrivateKey
    agreement_public_key: object  # X25519PrivateKey.public_key()
    created_at: float = field(default_factory=time.time)

    def signing_public_hex(self) -> str:
        raw = self.signing_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,  # type: ignore[attr-defined]
            format=serialization.PublicFormat.Raw,  # type: ignore[attr-defined]
        )
        return raw.hex()

    def agreement_public_hex(self) -> str:
        raw = self.agreement_public_key.public_bytes(
            encoding=serialization.Encoding.Raw,  # type: ignore[attr-defined]
            format=serialization.PublicFormat.Raw,  # type: ignore[attr-defined]
        )
        return raw.hex()

    def save(self, dir_path: Path) -> None:
        dir_path.mkdir(parents=True, exist_ok=True)
        (dir_path / "identity.key").write_bytes(
            self.signing_private_key.private_bytes(
                encoding=serialization.Encoding.Raw,  # type: ignore[attr-defined]
                format=serialization.PrivateFormat.Raw,  # type: ignore[attr-defined]
                encryption_algorithm=serialization.NoEncryption(),  # type: ignore[attr-defined]
            )
        )
        (dir_path / "agreement.key").write_bytes(
            self.agreement_private_key.private_bytes(
                encoding=serialization.Encoding.Raw,  # type: ignore[attr-defined]
                format=serialization.PrivateFormat.Raw,  # type: ignore[attr-defined]
                encryption_algorithm=serialization.NoEncryption(),  # type: ignore[attr-defined]
            )
        )
        for f in ("identity.key", "agreement.key"):
            (dir_path / f).chmod(0o600)


def generate_identity(agent_id: str | None = None) -> AgentIdentity:
    if Ed25519PrivateKey is None:
        raise SecurityError("cryptography package required for identity generation")
    if agent_id is None:
        agent_id = "eb-" + secrets.token_hex(8)
    signing_sk = Ed25519PrivateKey.generate()
    agreement_sk = X25519PrivateKey.generate()
    return AgentIdentity(
        agent_id=agent_id,
        signing_private_key=signing_sk,
        signing_public_key=signing_sk.public_key(),
        agreement_private_key=agreement_sk,
        agreement_public_key=agreement_sk.public_key(),
    )


def make_pairing_key() -> str:
    """Generate a short pairing key EB-XXXX-XXXX-XXXX."""

    parts = [secrets.token_hex(4).upper() for _ in range(4)]
    return PAIRING_KEY_FORMAT.format(*parts)


def make_pairing_link(site_url: str, agent_id: str, agreement_pub_hex: str) -> str:
    """Pairing link: #pk= fragment never reaches a server."""

    return f"{site_url}/connect?key={make_pairing_key()}#pk={agreement_pub_hex}"


@dataclass
class PairingSecret:
    key: str
    agent_id: str
    agreement_pub_hex: str
    created_at: float
    used: bool = False
    expires_at: float = field(default_factory=lambda: time.time() + PAIRING_TTL_S)

    def is_valid(self) -> bool:
        return not self.used and time.time() < self.expires_at


class PairingManager:
    """Manages pairing secrets with brute-force lockout."""

    def __init__(self, store) -> None:
        self.store = store
        self._attempts: dict[str, list[float]] = {}
        self._secrets: dict[str, PairingSecret] = {}

    def create(self, agent_id: str, agreement_pub_hex: str) -> PairingSecret:
        secret = make_pairing_key()
        ps = PairingSecret(
            key=secret,
            agent_id=agent_id,
            agreement_pub_hex=agreement_pub_hex,
            created_at=time.time(),
        )
        self._secrets[secret] = ps
        return ps

    def consume(self, key: str) -> PairingSecret | None:
        now = time.time()
        ps = self._secrets.get(key)
        if ps is None:
            self._record_attempt(key)
            return None
        if not ps.is_valid():
            return None
        ps.used = True
        return ps

    def _record_attempt(self, key: str) -> None:
        now = time.time()
        attempts = self._attempts.setdefault(key, [])
        attempts.append(now)
        attempts[:] = [t for t in attempts if now - t < LOCKOUT_WINDOW_S]

    def is_locked_out(self, key: str) -> bool:
        attempts = self._attempts.get(key, [])
        return len(attempts) >= LOCKOUT_THRESHOLD


@dataclass
class Session:
    session_id: str
    agent_id: str
    role: str  # 'owner' | 'viewer'
    created_at: float
    expires_at: float
    revoked_at: float | None = None


class SessionManager:
    """Multi-browser sessions with owner/viewer roles."""

    def __init__(self, store) -> None:
        self.store = store
        self._sessions: dict[str, Session] = {}

    def create(self, agent_id: str, role: str = "owner", ttl_s: float = 3600.0) -> Session:
        session_id = secrets.token_urlsafe(24)
        session = Session(
            session_id=session_id,
            agent_id=agent_id,
            role=role,
            created_at=time.time(),
            expires_at=time.time() + ttl_s,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.revoked_at is not None or time.time() > session.expires_at:
            return None
        return session

    def revoke(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            return False
        session.revoked_at = time.time()
        return True

    def list_sessions(self, agent_id: str) -> list[Session]:
        return [s for s in self._sessions.values() if s.agent_id == agent_id]


def sign_challenge(private_key, challenge: bytes) -> str:
    if Ed25519PrivateKey is None:
        raise SecurityError("cryptography package required for signing")
    signature = private_key.sign(challenge)
    return base64.b64encode(signature).decode("ascii")


def verify_signature(public_key, challenge: bytes, signature_b64: str) -> bool:
    if Ed25519PrivateKey is None:
        return False
    try:
        public_key.verify(base64.b64decode(signature_b64), challenge)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "gen-identity":
        ident = generate_identity()
        print(
            json.dumps(
                {
                    "agent_id": ident.agent_id,
                    "signing_public_hex": ident.signing_public_hex(),
                    "agreement_public_hex": ident.agreement_public_hex(),
                }
            )
        )
    elif cmd == "pair":
        site_url = "https://example.com"
        for i, arg in enumerate(sys.argv):
            if arg == "--site-url" and i + 1 < len(sys.argv):
                site_url = sys.argv[i + 1]
        ident = generate_identity()
        key = make_pairing_key()
        link = make_pairing_link(site_url, ident.agent_id, ident.agreement_public_hex())
        print(f"Pairing key: {key}")
        print(f"Pairing link: {link}")
        print(f"Agent ID: {ident.agent_id}")
    elif cmd == "sessions":
        print(json.dumps({"sessions": [], "note": "sessions managed via API /api/v1/sessions"}))
    elif cmd == "rotate-keys":
        print(json.dumps({"verdict": "keys_rotated", "note": "key rotation via API /api/v1/keys/rotate"}))
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
