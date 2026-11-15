"""Unit tests for eb_agent.security (no root required)."""

from __future__ import annotations

import time

import pytest

from eb_agent.security import (
    AgentIdentity,
    PairingManager,
    SessionManager,
    generate_identity,
    make_pairing_key,
    make_pairing_link,
    sign_challenge,
    verify_signature,
)


def test_generate_identity() -> None:
    ident = generate_identity("test-agent")
    assert ident.agent_id == "test-agent"
    assert ident.signing_public_hex()
    assert ident.agreement_public_hex()


def test_make_pairing_key_format() -> None:
    key = make_pairing_key()
    parts = key.split("-")
    assert parts[0] == "EB"
    assert len(parts) == 5
    assert all(len(p) == 8 for p in parts[1:])


def test_make_pairing_link() -> None:
    link = make_pairing_link("https://example.com", "agent-1", "abc123")
    assert "https://example.com/connect?key=" in link
    assert "#pk=abc123" in link


def test_pairing_consume_valid() -> None:
    class FakeStore:
        pass

    pm = PairingManager(FakeStore())
    ps = pm.create("agent-1", "pubhex")
    assert ps.is_valid()
    consumed = pm.consume(ps.key)
    assert consumed is not None
    assert consumed.used is True


def test_pairing_consume_invalid() -> None:
    class FakeStore:
        pass

    pm = PairingManager(FakeStore())
    assert pm.consume("EB-0000-0000-0000-0000") is None


def test_pairing_single_use() -> None:
    class FakeStore:
        pass

    pm = PairingManager(FakeStore())
    ps = pm.create("agent-1", "pubhex")
    pm.consume(ps.key)
    assert pm.consume(ps.key) is None


def test_pairing_expiry() -> None:
    class FakeStore:
        pass

    pm = PairingManager(FakeStore())
    ps = pm.create("agent-1", "pubhex")
    ps.expires_at = time.time() - 1  # expired
    assert pm.consume(ps.key) is None


def test_pairing_lockout() -> None:
    class FakeStore:
        pass

    pm = PairingManager(FakeStore())
    for _ in range(5):
        pm.consume("EB-0000-0000-0000-0000")
    assert pm.is_locked_out("EB-0000-0000-0000-0000") is True


def test_session_create_revoke() -> None:
    class FakeStore:
        pass

    sm = SessionManager(FakeStore())
    session = sm.create("agent-1", role="owner")
    assert session.role == "owner"
    assert sm.get(session.session_id) is not None
    assert sm.revoke(session.session_id) is True
    assert sm.get(session.session_id) is None


def test_session_viewer_role() -> None:
    class FakeStore:
        pass

    sm = SessionManager(FakeStore())
    session = sm.create("agent-1", role="viewer")
    assert session.role == "viewer"


def test_sign_verify_roundtrip() -> None:
    ident = generate_identity("agent-1")
    challenge = b"hello world"
    sig = sign_challenge(ident.signing_private_key, challenge)
    assert verify_signature(ident.signing_public_key, challenge, sig) is True
    assert verify_signature(ident.signing_public_key, challenge, "bad") is False
    assert verify_signature(ident.signing_public_key, b"different", sig) is False


def test_identity_save_chmod(tmp_path) -> None:
    ident = generate_identity("agent-1")
    ident.save(tmp_path)
    assert (tmp_path / "identity.key").exists()
    assert (tmp_path / "agreement.key").exists()
    mode = (tmp_path / "identity.key").stat().st_mode & 0o777
    assert mode == 0o600
