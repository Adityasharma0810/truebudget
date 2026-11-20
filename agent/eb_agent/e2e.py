"""End-to-end encryption for the relay transport.

Browser and agent derive the same AES-256-GCM key from an X25519 agreement:
the browser generates an ephemeral keypair, sends its public key, and both
sides run HKDF-SHA256 over the shared secret.  Every message carries a
monotonic counter and an explicit timestamp; replayed or stale messages are
rejected.  The relay only ever sees base64 ciphertext envelopes.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO = b"eb-e2e-v1"
AAD_PREFIX = b"eb-e2e-v1"
MAX_CLOCK_SKEW_S = 120.0


class E2EError(ValueError):
    """A ciphertext envelope failed authentication or replay checks."""


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except Exception as error:  # noqa: BLE001
        raise E2EError("invalid base64") from error


def generate_keypair() -> tuple[bytes, bytes]:
    private = X25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    raw_private = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return raw_private, public


def derive_key(private_bytes: bytes, peer_public_bytes: bytes, salt: bytes | None = None) -> bytes:
    private = X25519PrivateKey.from_private_bytes(private_bytes)
    peer = X25519PublicKey.from_public_bytes(peer_public_bytes)
    shared = private.exchange(peer)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt or b"",
        info=INFO,
    ).derive(shared)


def fingerprint(public_bytes: bytes) -> str:
    import hashlib

    return hashlib.sha256(public_bytes).hexdigest()


@dataclass
class SecureChannel:
    key: bytes
    send_counter: int = 0
    recv_counter: int = -1
    last_seen: float = 0.0
    _aes: AESGCM = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.key) != 32:
            raise E2EError("AES-256-GCM key must be 32 bytes")
        self._aes = AESGCM(self.key)

    def seal(self, payload: dict | list | str, *, ts: float | None = None) -> dict:
        self.send_counter += 1
        envelope = {
            "n": self.send_counter,
            "ts": float(ts if ts is not None else time.time()),
            "plain": payload,
        }
        raw = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
        nonce = self.send_counter.to_bytes(4, "big") + os.urandom(8)
        aad = AAD_PREFIX + self.send_counter.to_bytes(4, "big")
        ciphertext = self._aes.encrypt(nonce, raw, aad)
        return {"n": self.send_counter, "iv": _b64(nonce), "ct": _b64(ciphertext)}

    def open(self, envelope: dict) -> dict | list | str:
        try:
            counter = int(envelope["n"])
            nonce = _unb64(str(envelope["iv"]))
            ciphertext = _unb64(str(envelope["ct"]))
        except (KeyError, TypeError, ValueError) as error:
            raise E2EError("malformed envelope") from error
        if counter <= self.recv_counter:
            raise E2EError("replayed counter")
        if len(nonce) != 12:
            raise E2EError("invalid nonce length")
        aad = AAD_PREFIX + counter.to_bytes(4, "big")
        try:
            raw = self._aes.decrypt(nonce, ciphertext, aad)
        except Exception as error:  # noqa: BLE001 - cryptography raises InvalidTag
            raise E2EError("authentication failed") from error
        try:
            envelope_data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as error:
            raise E2EError("invalid plaintext") from error
        ts = float(envelope_data.get("ts") or 0.0)
        if ts and abs(time.time() - ts) > MAX_CLOCK_SKEW_S:
            raise E2EError("stale message")
        self.recv_counter = counter
        self.last_seen = time.time()
        return envelope_data.get("plain")
