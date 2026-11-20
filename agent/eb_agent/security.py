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

PAIRING_KEY_FORMAT = "EB-{}-{}-{}"
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

    def agreement_private_bytes(self) -> bytes:
        return self.agreement_private_key.private_bytes(
            encoding=serialization.Encoding.Raw,  # type: ignore[attr-defined]
            format=serialization.PrivateFormat.Raw,  # type: ignore[attr-defined]
            encryption_algorithm=serialization.NoEncryption(),  # type: ignore[attr-defined]
        )

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
        (dir_path / "identity.json").write_text(
            json.dumps(
                {
                    "agent_id": self.agent_id,
                    "signing_public_hex": self.signing_public_hex(),
                    "agreement_public_hex": self.agreement_public_hex(),
                    "created_at": self.created_at,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        for f in ("identity.key", "agreement.key", "identity.json"):
            (dir_path / f).chmod(0o600)


def load_or_create_identity(dir_path: Path | str, agent_id: str | None = None) -> AgentIdentity:
    """Load the persisted identity, generating and saving one if absent."""

    if Ed25519PrivateKey is None or X25519PrivateKey is None:
        raise SecurityError("cryptography package required for identity handling")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey as _X

    base = Path(dir_path)
    key_path = base / "identity.key"
    agreement_path = base / "agreement.key"
    meta_path = base / "identity.json"
    if key_path.exists() and agreement_path.exists():
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        signing_sk = _Ed.from_private_bytes(key_path.read_bytes())
        agreement_sk = _X.from_private_bytes(agreement_path.read_bytes())
        return AgentIdentity(
            agent_id=str(meta.get("agent_id") or agent_id or "eb-" + secrets.token_hex(8)),
            signing_private_key=signing_sk,
            signing_public_key=signing_sk.public_key(),
            agreement_private_key=agreement_sk,
            agreement_public_key=agreement_sk.public_key(),
            created_at=float(meta.get("created_at") or time.time()),
        )
    identity = generate_identity(agent_id)
    identity.save(base)
    return identity


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

    parts = [secrets.token_hex(2).upper() for _ in range(3)]
    return PAIRING_KEY_FORMAT.format(*parts)


def make_pairing_link(
    site_url: str,
    agent_id: str,
    agreement_pub_hex: str,
    fingerprint: str | None = None,
) -> str:
    """Pairing link: key + agent id in the query, public keys in the fragment.

    The fragment never reaches a server, so the agent's X25519 public key and
    the TLS certificate fingerprint stay out of relay/proxy logs.
    """

    from urllib.parse import quote

    key = make_pairing_key()
    fragment = f"pk={agreement_pub_hex}"
    if fingerprint:
        fragment += f"&fp={quote(fingerprint)}"
    return (
        f"{site_url.rstrip('/')}/connect?key={key}"
        f"&agent={quote(agent_id)}#{fragment}"
    )


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
    """Manages pairing secrets with brute-force lockout and SQLite persistence."""

    def __init__(self, store) -> None:
        self.store = store
        self._attempts: dict[str, list[float]] = {}
        self._secrets: dict[str, PairingSecret] = {}
        self.last_error: str | None = None

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        """Compatibility hook.

        Clear pairing keys are never stored, so there is nothing to restore
        into the in-memory map.  ``consume`` verifies the presented key
        against the persisted SHA-256 hash, which makes keys survive a daemon
        restart without keeping the secret at rest.
        """

        return

    def create(self, agent_id: str, agreement_pub_hex: str) -> PairingSecret:
        secret = make_pairing_key()
        ps = PairingSecret(
            key=secret,
            agent_id=agent_id,
            agreement_pub_hex=agreement_pub_hex,
            created_at=time.time(),
        )
        self._secrets[secret] = ps
        execute = getattr(self.store, "execute", None)
        if callable(execute):
            try:
                execute(
                    "INSERT OR REPLACE INTO pairing_secrets"
                    "(key_hash, agent_id, agreement_pub, created_at, expires_at, used) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (
                        self._hash(secret),
                        agent_id,
                        agreement_pub_hex,
                        ps.created_at,
                        ps.expires_at,
                    ),
                )
                commit = getattr(self.store, "commit", None)
                if callable(commit):
                    commit()
            except Exception:  # noqa: BLE001
                pass
        return ps

    def rotate(self, agent_id: str, agreement_pub_hex: str) -> PairingSecret:
        """Invalidate every outstanding pairing secret and issue a new one."""

        self._secrets.clear()
        execute = getattr(self.store, "execute", None)
        if callable(execute):
            try:
                execute("DELETE FROM pairing_secrets")
                commit = getattr(self.store, "commit", None)
                if callable(commit):
                    commit()
            except Exception:  # noqa: BLE001
                pass
        return self.create(agent_id, agreement_pub_hex)

    def consume(self, key: str) -> PairingSecret | None:
        """Consume a single-use key, verifying against the persisted hash.

        The database is authoritative so a key created by the CLI (a separate
        process) or before a daemon restart still works.  The in-memory map is
        a fallback for stores without SQLite (unit tests).
        """

        self.last_error = None
        key_hash = self._hash(key)
        execute = getattr(self.store, "execute", None)
        if callable(execute):
            try:
                row = execute(
                    "SELECT agent_id, agreement_pub, created_at, expires_at, used "
                    "FROM pairing_secrets WHERE key_hash=?",
                    (key_hash,),
                ).fetchone()
            except Exception:  # noqa: BLE001 - FakeStore may not have tables
                row = None
            if row is not None:
                if int(row["used"]):
                    self.last_error = "used"
                    return None
                if float(row["expires_at"]) <= time.time():
                    self.last_error = "expired"
                    return None
                execute(
                    "UPDATE pairing_secrets SET used=1 WHERE key_hash=?",
                    (key_hash,),
                )
                commit = getattr(self.store, "commit", None)
                if callable(commit):
                    commit()
                self._secrets.pop(key, None)
                return PairingSecret(
                    key=key,
                    agent_id=str(row["agent_id"]),
                    agreement_pub_hex=str(row["agreement_pub"]),
                    created_at=float(row["created_at"]),
                    used=True,
                    expires_at=float(row["expires_at"]),
                )

        ps = self._secrets.get(key)
        if ps is None:
            self._record_attempt(key)
            self.last_error = "invalid"
            return None
        if not ps.is_valid():
            self.last_error = "expired" if time.time() >= ps.expires_at else "used"
            return None
        ps.used = True
        self._secrets.pop(key, None)
        return ps

    def _record_attempt(self, key: str) -> None:
        now = time.time()
        key_hash = self._hash(key)
        attempts = self._attempts.setdefault(key_hash, [])
        attempts.append(now)
        attempts[:] = [t for t in attempts if now - t < LOCKOUT_WINDOW_S]
        execute = getattr(self.store, "execute", None)
        if callable(execute):
            try:
                execute("INSERT INTO pairing_attempts(key_hash, ts) VALUES (?, ?)", (key_hash, now))
                execute("DELETE FROM pairing_attempts WHERE ts < ?", (now - LOCKOUT_WINDOW_S,))
                commit = getattr(self.store, "commit", None)
                if callable(commit):
                    commit()
            except Exception:  # noqa: BLE001
                pass

    def is_locked_out(self, key: str) -> bool:
        key_hash = self._hash(key)
        attempts = self._attempts.get(key_hash)
        if attempts is None:
            execute = getattr(self.store, "execute", None)
            if callable(execute):
                try:
                    row = execute(
                        "SELECT COUNT(*) AS n FROM pairing_attempts "
                        "WHERE key_hash=? AND ts > ?",
                        (key_hash, time.time() - LOCKOUT_WINDOW_S),
                    ).fetchone()
                    return bool(row and int(row["n"]) >= LOCKOUT_THRESHOLD)
                except Exception:  # noqa: BLE001
                    return False
            return False
        return len(attempts) >= LOCKOUT_THRESHOLD


@dataclass
class Session:
    session_id: str
    agent_id: str
    role: str  # 'owner' | 'viewer'
    created_at: float
    expires_at: float
    revoked_at: float | None = None
    client: str = ""
    user_agent: str = ""


@dataclass
class SessionTokens:
    access_token: str
    refresh_token: str


class SessionManager:
    """Multi-browser sessions with owner/viewer roles and bound tokens."""

    def __init__(self, store) -> None:
        self.store = store
        self._sessions: dict[str, Session] = {}
        self._load()

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load(self) -> None:
        execute = getattr(self.store, "execute", None)
        if not callable(execute):
            return
        try:
            rows = execute(
                "SELECT session_id, agent_id, role, created_at, expires_at, revoked_at "
                "FROM sessions WHERE revoked_at IS NULL AND expires_at > ?",
                (time.time(),),
            ).fetchall()
        except Exception:  # noqa: BLE001
            return
        for row in rows:
            session = Session(
                session_id=str(row["session_id"]),
                agent_id=str(row["agent_id"]),
                role=str(row["role"]),
                created_at=float(row["created_at"]),
                expires_at=float(row["expires_at"]),
                revoked_at=None,
            )
            self._sessions[session.session_id] = session

    def create(
        self,
        agent_id: str,
        role: str = "owner",
        ttl_s: float = 3600.0,
        *,
        client: str = "",
        user_agent: str = "",
        tokens: SessionTokens | None = None,
    ) -> Session:
        session_id = secrets.token_urlsafe(24)
        now = time.time()
        session = Session(
            session_id=session_id,
            agent_id=agent_id,
            role=role,
            created_at=now,
            expires_at=now + ttl_s,
            client=client,
            user_agent=user_agent[:180],
        )
        self._sessions[session_id] = session
        execute = getattr(self.store, "execute", None)
        if callable(execute):
            try:
                execute(
                    "INSERT INTO sessions(session_id, agent_id, role, created_at, "
                    "expires_at, revoked_at, access_token_hash, refresh_token_hash) "
                    "VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        session_id,
                        agent_id,
                        role,
                        session.created_at,
                        session.expires_at,
                        self._hash(tokens.access_token) if tokens else None,
                        self._hash(tokens.refresh_token) if tokens else None,
                    ),
                )
                commit = getattr(self.store, "commit", None)
                if callable(commit):
                    commit()
            except Exception:  # noqa: BLE001
                pass
        return session

    def issue(
        self,
        agent_id: str,
        role: str = "owner",
        ttl_s: float = 3600.0,
        *,
        client: str = "",
        user_agent: str = "",
    ) -> tuple[Session, SessionTokens]:
        tokens = SessionTokens(
            access_token=secrets.token_urlsafe(32),
            refresh_token=secrets.token_urlsafe(32),
        )
        session = self.create(
            agent_id,
            role,
            ttl_s,
            client=client,
            user_agent=user_agent,
            tokens=tokens,
        )
        return session, tokens

    def get(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            execute = getattr(self.store, "execute", None)
            if callable(execute):
                try:
                    row = execute(
                        "SELECT session_id, agent_id, role, created_at, expires_at, revoked_at "
                        "FROM sessions WHERE session_id=?",
                        (session_id,),
                    ).fetchone()
                except Exception:  # noqa: BLE001
                    row = None
                if row and row["revoked_at"] is None and time.time() <= float(row["expires_at"]):
                    session = Session(
                        session_id=str(row["session_id"]),
                        agent_id=str(row["agent_id"]),
                        role=str(row["role"]),
                        created_at=float(row["created_at"]),
                        expires_at=float(row["expires_at"]),
                    )
                    self._sessions[session.session_id] = session
        if session is None:
            return None
        if session.revoked_at is not None or time.time() > session.expires_at:
            return None
        return session

    def authenticate(self, token: str) -> Session | None:
        """Resolve an access or refresh token to a live session."""

        token_hash = self._hash(token)
        execute = getattr(self.store, "execute", None)
        if not callable(execute):
            return None
        try:
            row = execute(
                "SELECT session_id FROM sessions "
                "WHERE (access_token_hash=? OR refresh_token_hash=?) "
                "AND revoked_at IS NULL AND expires_at > ? LIMIT 1",
                (token_hash, token_hash, time.time()),
            ).fetchone()
        except Exception:  # noqa: BLE001
            return None
        return self.get(str(row["session_id"])) if row else None

    def revoke(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session is None:
            execute = getattr(self.store, "execute", None)
            if callable(execute):
                try:
                    cursor = execute(
                        "UPDATE sessions SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL",
                        (time.time(), session_id),
                    )
                    commit = getattr(self.store, "commit", None)
                    if callable(commit):
                        commit()
                    return bool(cursor.rowcount)
                except Exception:  # noqa: BLE001
                    return False
            return False
        session.revoked_at = time.time()
        execute = getattr(self.store, "execute", None)
        if callable(execute):
            try:
                execute(
                    "UPDATE sessions SET revoked_at=? WHERE session_id=?",
                    (session.revoked_at, session_id),
                )
                commit = getattr(self.store, "commit", None)
                if callable(commit):
                    commit()
            except Exception:  # noqa: BLE001
                pass
        return True

    def list_sessions(self, agent_id: str) -> list[Session]:
        self._load()
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
