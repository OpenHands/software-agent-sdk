import json
import os
import socket
import time
from pathlib import Path
from typing import cast

import pytest

from openhands.agent_server import conversation_lease as conversation_lease_module
from openhands.agent_server.conversation_lease import (
    LEASE_FILE_NAME,
    ConversationLease,
    ConversationLeaseHeldError,
    ConversationOwnershipLostError,
    LeasePayload,
)


def _read_lease_payload(conversation_dir: Path) -> LeasePayload:
    return cast(
        LeasePayload,
        json.loads((conversation_dir / LEASE_FILE_NAME).read_text()),
    )


def _expire_lease(conversation_dir: Path) -> None:
    lease_path = conversation_dir / LEASE_FILE_NAME
    payload = json.loads(lease_path.read_text())
    payload["expires_at"] = 0
    lease_path.write_text(json.dumps(payload))


def test_claim_and_renew_persist_same_owner_generation(tmp_path: Path) -> None:
    conversation_dir = tmp_path / "conversation"
    lease = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
        ttl_seconds=0.2,
    )

    claim = lease.claim()
    first_payload = _read_lease_payload(conversation_dir)

    time.sleep(0.01)
    lease.renew(claim.generation)
    renewed_payload = _read_lease_payload(conversation_dir)

    repeated_claim = lease.claim()
    repeated_payload = _read_lease_payload(conversation_dir)

    assert claim.generation == 1
    assert claim.takeover is False
    assert first_payload["owner_instance_id"] == "primary"
    assert renewed_payload["generation"] == 1
    assert renewed_payload["expires_at"] > first_payload["expires_at"]
    assert repeated_claim.generation == 1
    assert repeated_claim.takeover is False
    assert repeated_payload["owner_instance_id"] == "primary"
    assert repeated_payload["generation"] == 1


def test_claim_rejects_different_owner_while_lease_is_live(tmp_path: Path) -> None:
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
    )
    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )

    primary.claim()

    with pytest.raises(ConversationLeaseHeldError) as exc_info:
        secondary.claim()

    assert exc_info.value.conversation_dir == conversation_dir
    assert exc_info.value.owner_instance_id == "primary"


def test_takeover_bumps_generation_and_blocks_stale_owner_writes(
    tmp_path: Path,
) -> None:
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
    )
    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )

    primary_claim = primary.claim()
    _expire_lease(conversation_dir)

    secondary_claim = secondary.claim()
    payload = _read_lease_payload(conversation_dir)

    assert secondary_claim.generation == primary_claim.generation + 1
    assert secondary_claim.takeover is True
    assert payload["owner_instance_id"] == "secondary"
    assert payload["generation"] == secondary_claim.generation

    with pytest.raises(ConversationOwnershipLostError):
        primary.renew(primary_claim.generation)

    with pytest.raises(ConversationOwnershipLostError):
        with primary.guarded_write(primary_claim.generation):
            pass

    with secondary.guarded_write(secondary_claim.generation):
        assert _read_lease_payload(conversation_dir)["owner_instance_id"] == "secondary"


def test_release_keeps_new_owner_lease_intact_after_takeover(tmp_path: Path) -> None:
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
    )
    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )

    primary_claim = primary.claim()
    _expire_lease(conversation_dir)
    secondary_claim = secondary.claim()

    primary.release(primary_claim.generation)
    assert (conversation_dir / LEASE_FILE_NAME).exists()

    secondary.release(secondary_claim.generation)
    assert not (conversation_dir / LEASE_FILE_NAME).exists()


def test_claim_writes_owner_host_and_pid(tmp_path: Path) -> None:
    conversation_dir = tmp_path / "conversation"
    lease = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
    )

    lease.claim()
    payload = _read_lease_payload(conversation_dir)

    assert payload["owner_host"] == socket.gethostname()
    assert payload["owner_pid"] == os.getpid()


def test_claim_takes_over_when_previous_owner_pid_is_dead(
    tmp_path: Path,
) -> None:
    """Simulates a non-graceful shutdown: a dead PID still owns a live lease.

    Without the crash-recovery check the second claim would fail with
    ``ConversationLeaseHeldError`` until the TTL elapsed and the
    conversation would be skipped on agent-server restart.
    """
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
        ttl_seconds=3600.0,  # Make sure the TTL is far from elapsing.
    )
    primary_claim = primary.claim()
    payload = _read_lease_payload(conversation_dir)
    # Sanity: lease is nominally valid.
    assert payload["expires_at"] > time.time() + 60

    # Forge a lease that points at a PID guaranteed not to exist on this
    # host. PID 2**31 - 1 is well beyond /proc/sys/kernel/pid_max in any
    # real environment.
    dead_pid = 2**31 - 1
    forged = dict(payload)
    forged["owner_pid"] = dead_pid
    (conversation_dir / LEASE_FILE_NAME).write_text(json.dumps(forged))

    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )
    secondary_claim = secondary.claim()

    new_payload = _read_lease_payload(conversation_dir)
    assert secondary_claim.takeover is True
    assert secondary_claim.generation == primary_claim.generation + 1
    assert new_payload["owner_instance_id"] == "secondary"
    assert new_payload["owner_pid"] == os.getpid()


def test_claim_blocks_takeover_when_owner_is_on_a_different_host(
    tmp_path: Path,
) -> None:
    """Liveness checks must not fire for owners on other hosts.

    If the lease was written by a peer agent-server running on a
    different machine, our local PID table tells us nothing about
    whether that process is alive, so we must fall back to the TTL.
    """
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
        ttl_seconds=3600.0,
    )
    primary.claim()

    payload = _read_lease_payload(conversation_dir)
    forged = dict(payload)
    forged["owner_host"] = "some-other-host"
    forged["owner_pid"] = 2**31 - 1  # would be "dead" if checked locally
    (conversation_dir / LEASE_FILE_NAME).write_text(json.dumps(forged))

    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )
    with pytest.raises(ConversationLeaseHeldError):
        secondary.claim()


def test_claim_falls_back_to_ttl_for_legacy_lease_files(
    tmp_path: Path,
) -> None:
    """Lease files written by older versions don't include host/pid.

    They must continue to behave exactly as before: TTL-only expiry
    decides whether a takeover may occur.
    """
    conversation_dir = tmp_path / "conversation"
    conversation_dir.mkdir(parents=True)
    legacy_payload = {
        "owner_instance_id": "primary",
        "generation": 7,
        "expires_at": time.time() + 3600.0,
    }
    (conversation_dir / LEASE_FILE_NAME).write_text(json.dumps(legacy_payload))

    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )
    with pytest.raises(ConversationLeaseHeldError):
        secondary.claim()


def test_owner_pid_check_treats_unknown_errors_as_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """We must err on the side of not stealing live leases."""
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
        ttl_seconds=3600.0,
    )
    primary.claim()

    def _raise_oserror(_pid: int, _sig: int) -> None:
        raise OSError("EPERM-like error from a sandbox")

    monkeypatch.setattr(conversation_lease_module.os, "kill", _raise_oserror)

    # Forge the lease so it points at a PID that is NOT this process
    # (otherwise the same-process short-circuit kicks in before
    # _is_pid_alive is consulted).
    payload = _read_lease_payload(conversation_dir)
    forged = dict(payload)
    forged["owner_pid"] = os.getpid() + 1
    (conversation_dir / LEASE_FILE_NAME).write_text(json.dumps(forged))

    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )
    with pytest.raises(ConversationLeaseHeldError):
        secondary.claim()


def test_claim_self_pid_match_is_not_treated_as_dead(tmp_path: Path) -> None:
    """A lease referring to *this* process must never be considered dead.

    Otherwise a same-process re-entry (e.g. tests, or a fast restart that
    happens to reuse the same PID) could erroneously trigger a takeover.
    """
    conversation_dir = tmp_path / "conversation"
    primary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="primary",
        ttl_seconds=3600.0,
    )
    primary.claim()

    # The lease already has owner_pid == os.getpid(). A different-owner
    # claim must still be rejected.
    secondary = ConversationLease(
        conversation_dir=conversation_dir,
        owner_instance_id="secondary",
    )
    with pytest.raises(ConversationLeaseHeldError):
        secondary.claim()
