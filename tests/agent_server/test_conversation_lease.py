import json
import time
from pathlib import Path
from typing import cast

import pytest

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
