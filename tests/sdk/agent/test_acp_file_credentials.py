import asyncio
import json
import threading
import time
from collections.abc import Coroutine
from typing import Any, cast
from unittest.mock import patch

import pytest

from openhands.sdk.agent.acp_file_credentials import (
    CODEX_AUTH_SECRET_NAME,
    create_file_credential_lifecycle,
)
from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.credential import (
    CredentialAuthorizationRejected,
    CredentialConflict,
    CredentialNeedsReauthentication,
    CredentialRenewalRejected,
    CredentialRenewalUnavailable,
    CredentialSyncError,
    ResolvedCredential,
)


def _auth(refresh_token: str, access_token: str = "access") -> str:
    return json.dumps(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "refresh_token": refresh_token,
                "access_token": access_token,
            },
        }
    )


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


class MemoryBinding:
    def __init__(self, value: str) -> None:
        self.value = value
        self.generation = 0
        self.replace_calls = 0

    async def load(self) -> ResolvedCredential:
        return ResolvedCredential(self.value, str(self.generation))

    async def replace(self, expected_version: str, value: str) -> str:
        if expected_version != str(self.generation):
            raise CredentialConflict("conflict")
        self.replace_calls += 1
        self.generation += 1
        self.value = value
        return str(self.generation)


class AmbiguousBinding(MemoryBinding):
    async def replace(self, expected_version: str, value: str) -> str:
        await super().replace(expected_version, value)
        raise CredentialSyncError("lost response")


class BlockingBinding(MemoryBinding):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.replace_started = threading.Event()
        self.replace_allowed = threading.Event()

    async def replace(self, expected_version: str, value: str) -> str:
        self.replace_started.set()
        allowed = await asyncio.to_thread(self.replace_allowed.wait, 2)
        assert allowed
        return await super().replace(expected_version, value)


class MissingBinding(MemoryBinding):
    async def replace(self, expected_version: str, value: str) -> str:
        raise CredentialNeedsReauthentication("missing")


class RevokedBinding(MemoryBinding):
    async def replace(self, expected_version: str, value: str) -> str:
        raise CredentialAuthorizationRejected("revoked")


class RecoveringBinding(MemoryBinding):
    def __init__(self, value: str, failures: int) -> None:
        super().__init__(value)
        self.failures = failures

    async def replace(self, expected_version: str, value: str) -> str:
        if self.failures:
            self.failures -= 1
            raise CredentialSyncError("unavailable")
        return await super().replace(expected_version, value)


class CommitThenUnavailableBinding(MemoryBinding):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.load_failures = 0
        self.replace_attempts = 0

    async def load(self) -> ResolvedCredential:
        if self.load_failures:
            self.load_failures -= 1
            raise CredentialSyncError("load unavailable")
        return await super().load()

    async def replace(self, expected_version: str, value: str) -> str:
        self.replace_attempts += 1
        if self.replace_attempts == 1:
            await super().replace(expected_version, value)
            raise CredentialSyncError("lost response")
        return await super().replace(expected_version, value)


class MaintenanceBinding(MemoryBinding):
    def __init__(self, value: str, failures: int = 0) -> None:
        super().__init__(value)
        self.due = False
        self.failures = failures
        self.maintain_calls = 0
        self.unusable = False

    def maintenance_due(self) -> bool:
        return self.due

    async def maintain(self) -> None:
        self.maintain_calls += 1
        if self.failures:
            self.failures -= 1
            self.due = False
            self.unusable = True
            raise CredentialRenewalUnavailable("renewal unavailable")
        self.due = False
        self.unusable = False

    def raise_if_authorization_unusable(self) -> None:
        if self.unusable:
            raise CredentialRenewalUnavailable("renewal unavailable")


class ReauthorizableMaintenanceBinding(MaintenanceBinding):
    def __init__(self, value: str, failures: int = 0) -> None:
        super().__init__(value, failures)
        self.authorization_revision = 0
        self.reject_renewal = False

    async def maintain(self) -> None:
        if self.reject_renewal:
            self.maintain_calls += 1
            self.due = False
            raise CredentialRenewalRejected("renewal rejected")
        await super().maintain()

    def reauthorize(self) -> None:
        self.authorization_revision += 1
        self.reject_renewal = False
        self.failures = 0
        self.due = False
        self.unusable = False


class RenewThenRejectWriteBinding(MemoryBinding):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.authorization_revision = 0
        self.due = False
        self.maintain_calls = 0
        self.replace_attempts = 0
        self.reject_write = True

    def maintenance_due(self) -> bool:
        return self.due

    async def maintain(self) -> None:
        self.maintain_calls += 1
        self.authorization_revision += 1
        self.due = False

    async def replace(self, expected_version: str, value: str) -> str:
        self.replace_attempts += 1
        if self.reject_write:
            raise CredentialAuthorizationRejected("write rejected")
        return await super().replace(expected_version, value)

    def reauthorize(self) -> None:
        self.authorization_revision += 1
        self.reject_write = False


class ExpiringRecoveringBinding(MemoryBinding):
    def __init__(self, value: str, replace_failures: int = 0) -> None:
        super().__init__(value)
        self.authorization_revision = 0
        self.due = False
        self.authorization_valid = True
        self.maintain_calls = 0
        self.replace_failures = replace_failures

    def maintenance_due(self) -> bool:
        return self.due

    async def maintain(self) -> None:
        self.maintain_calls += 1
        self.authorization_revision += 1
        self.authorization_valid = True
        self.due = False

    async def load(self) -> ResolvedCredential:
        if not self.authorization_valid:
            raise CredentialAuthorizationRejected("expired")
        return await super().load()

    async def replace(self, expected_version: str, value: str) -> str:
        if not self.authorization_valid:
            raise CredentialAuthorizationRejected("expired")
        if self.replace_failures:
            self.replace_failures -= 1
            raise CredentialSyncError("source unavailable")
        return await super().replace(expected_version, value)


class BlockingRenewalRejectedBinding(MemoryBinding):
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.due = False
        self.maintain_started = threading.Event()
        self.maintain_allowed = threading.Event()

    def maintenance_due(self) -> bool:
        return self.due

    async def maintain(self) -> None:
        self.maintain_started.set()
        allowed = await asyncio.to_thread(self.maintain_allowed.wait, 2)
        assert allowed
        self.due = False
        raise CredentialRenewalRejected("malformed renewal")


class PersistentRenewalRejectedBinding(MemoryBinding):
    def __init__(self, value: str, replace_failures: int) -> None:
        super().__init__(value)
        self.due = False
        self.maintain_calls = 0
        self.replace_failures = replace_failures
        self.replace_attempts = 0

    def maintenance_due(self) -> bool:
        return self.due

    async def maintain(self) -> None:
        self.maintain_calls += 1
        raise CredentialRenewalRejected("malformed renewal")

    async def replace(self, expected_version: str, value: str) -> str:
        self.replace_attempts += 1
        if self.replace_failures:
            self.replace_failures -= 1
            raise CredentialSyncError("source unavailable")
        return await super().replace(expected_version, value)


def _lifecycle(binding: MemoryBinding, registry: SecretRegistry):
    lifecycle = create_file_credential_lifecycle(
        CODEX_AUTH_SECRET_NAME,
        binding,
        _run,
    )
    assert lifecycle is not None
    env: dict[str, str] = {}
    lifecycle.materialize(registry, env)
    return lifecycle, env


def _wait_for_value(binding: MemoryBinding, value: str) -> None:
    deadline = time.monotonic() + 2
    while binding.value != value and time.monotonic() < deadline:
        time.sleep(0.02)
    assert binding.value == value


def _stop_monitor(lifecycle: Any) -> None:
    runtime = cast(Any, lifecycle)
    runtime._stop.set()
    assert runtime._monitor is not None
    runtime._monitor.join(timeout=1)


def test_background_rotation_writes_through_and_masks() -> None:
    initial = _auth("refresh-r0", "access-r0")
    rotated = _auth("refresh-r1", "access-r1")
    binding = MemoryBinding(initial)
    registry = SecretRegistry()
    lifecycle, env = _lifecycle(binding, registry)
    path = lifecycle.path
    assert path is not None
    try:
        path.write_text(rotated, encoding="utf-8")
        _wait_for_value(binding, rotated)
        assert registry.mask_secrets_in_output(initial) == "<secret-hidden>"
        assert registry.mask_secrets_in_output(rotated) == "<secret-hidden>"
        assert registry.mask_secrets_in_output("access-r1") == "<secret-hidden>"
        assert env["CODEX_HOME"] == str(path.parent)
    finally:
        lifecycle.close()
    assert not path.parent.exists()


def test_materialize_renews_due_authorization_before_load() -> None:
    binding = ExpiringRecoveringBinding(_auth("refresh-r0"))
    binding.authorization_valid = False
    binding.due = True

    lifecycle, _ = _lifecycle(binding, SecretRegistry())

    assert binding.maintain_calls == 1
    lifecycle.close()


def test_mask_tracking_does_not_sleep_or_write() -> None:
    initial = _auth("refresh-r0", "access-r0")
    rotated = _auth("refresh-r1", "access-r1")
    binding = MemoryBinding(initial)
    registry = SecretRegistry()
    lifecycle, _ = _lifecycle(binding, registry)
    assert lifecycle.path is not None
    runtime = cast(Any, lifecycle)
    runtime._stop.set()
    assert runtime._monitor is not None
    runtime._monitor.join(timeout=1)
    try:
        lifecycle.path.write_text(rotated, encoding="utf-8")
        with patch(
            "openhands.sdk.agent.acp_file_credentials.time.sleep",
            side_effect=AssertionError("mask tracking must not sleep"),
        ):
            lifecycle.track_current()
        assert binding.value == initial
        assert binding.replace_calls == 0
        assert registry.mask_secrets_in_output("access-r1") == "<secret-hidden>"
    finally:
        lifecycle.close()


def test_monitor_start_failure_scrubs_runtime_and_allows_retry(tmp_path) -> None:
    binding = MemoryBinding(_auth("refresh-r0"))
    lifecycle = create_file_credential_lifecycle(
        CODEX_AUTH_SECRET_NAME,
        binding,
        _run,
    )
    assert lifecycle is not None
    runtime_dir = tmp_path / "failed-runtime"
    runtime_dir.mkdir()
    env: dict[str, str] = {}

    with (
        patch(
            "openhands.sdk.agent.acp_file_credentials.tempfile.mkdtemp",
            return_value=str(runtime_dir),
        ),
        patch.object(threading.Thread, "start", side_effect=RuntimeError("no thread")),
        pytest.raises(RuntimeError, match="no thread"),
    ):
        lifecycle.materialize(SecretRegistry(), env)

    assert lifecycle.path is None
    assert "CODEX_HOME" not in env
    assert not runtime_dir.exists()

    retry, _ = _lifecycle(binding, SecretRegistry())
    retry.close()


def test_mask_tracking_does_not_wait_for_monitor_write() -> None:
    rotated = _auth("refresh-r1", "access-r1")
    binding = BlockingBinding(_auth("refresh-r0", "access-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    try:
        lifecycle.path.write_text(rotated, encoding="utf-8")
        assert binding.replace_started.wait(2)

        tracked = threading.Event()
        errors: list[BaseException] = []

        def track() -> None:
            try:
                lifecycle.track_current()
            except BaseException as exc:
                errors.append(exc)
            finally:
                tracked.set()

        thread = threading.Thread(target=track)
        thread.start()
        completed = tracked.wait(0.5)
        binding.replace_allowed.set()
        thread.join(timeout=2)
        assert completed
        assert not errors
        _wait_for_value(binding, rotated)
    finally:
        binding.replace_allowed.set()
        lifecycle.close()


def test_partial_file_is_not_published() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = MemoryBinding(initial)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    try:
        lifecycle.path.write_text('{"tokens":', encoding="utf-8")
        time.sleep(0.25)
        assert binding.value == initial
        assert binding.replace_calls == 0
        lifecycle.path.write_text(rotated, encoding="utf-8")
        _wait_for_value(binding, rotated)
    finally:
        lifecycle.close()


def test_partial_file_fails_masking_without_poisoning_lifecycle() -> None:
    binding = MemoryBinding(_auth("refresh-r0", "old-access"))
    registry = SecretRegistry()
    lifecycle, _ = _lifecycle(binding, registry)
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)

    lifecycle.path.write_text('{"tokens":', encoding="utf-8")
    with pytest.raises(CredentialSyncError, match="read safely"):
        lifecycle.track_current()

    rotated = _auth("refresh-r1", "new-rotated-access")
    lifecycle.path.write_text(rotated, encoding="utf-8")
    lifecycle.track_current()

    assert registry.mask_secrets_in_output("new-rotated-access") == "<secret-hidden>"
    lifecycle.close()


def test_unchanged_file_does_not_write() -> None:
    binding = MemoryBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    lifecycle.flush()
    lifecycle.close()
    assert binding.replace_calls == 0


def test_ambiguous_committed_write_converges() -> None:
    rotated = _auth("refresh-r1")
    binding = AmbiguousBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    try:
        lifecycle.path.write_text(rotated, encoding="utf-8")
        lifecycle.flush()
        assert binding.value == rotated
        assert binding.replace_calls == 1
    finally:
        lifecycle.close()


def test_conflict_is_sticky_until_close_scrubs_runtime_directory() -> None:
    binding = MemoryBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    binding.generation += 1
    lifecycle.path.write_text(_auth("refresh-r1"), encoding="utf-8")
    with pytest.raises(CredentialConflict):
        lifecycle.flush()
    with pytest.raises(CredentialConflict):
        lifecycle.track_current()
    lifecycle.close()
    lifecycle.close()
    assert not runtime_dir.exists()


def test_deleted_credential_error_is_sticky() -> None:
    binding = MissingBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    runtime = cast(Any, lifecycle)
    runtime._stop.set()
    assert runtime._monitor is not None
    runtime._monitor.join(timeout=1)
    lifecycle.path.write_text(_auth("refresh-r1"), encoding="utf-8")

    with pytest.raises(CredentialNeedsReauthentication):
        lifecycle.flush()
    with pytest.raises(CredentialNeedsReauthentication):
        lifecycle.track_current()
    lifecycle.close()
    lifecycle.close()
    assert not runtime_dir.exists()


def test_revoked_authorization_is_terminal_and_scrubbed_on_close() -> None:
    binding = RevokedBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(_auth("refresh-r1"), encoding="utf-8")

    with pytest.raises(CredentialAuthorizationRejected, match="revoked"):
        lifecycle.flush()
    with pytest.raises(CredentialAuthorizationRejected, match="revoked"):
        lifecycle.track_current()

    lifecycle.close()
    assert not runtime_dir.exists()


def test_unstable_read_does_not_poison_lifecycle() -> None:
    rotated = _auth("refresh-r1")
    binding = MemoryBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime = cast(Any, lifecycle)
    runtime._stop.set()
    assert runtime._monitor is not None
    runtime._monitor.join(timeout=1)
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with (
        patch.object(runtime, "_read_stable", return_value=None),
        pytest.raises(CredentialSyncError, match="read safely"),
    ):
        lifecycle.flush()

    lifecycle.flush()
    assert binding.value == rotated
    lifecycle.close()


@pytest.mark.parametrize("replacement", [None, '{"logged_out": true}', b"\xff"])
def test_close_consumes_deleted_or_invalid_local_credential(replacement) -> None:
    lifecycle, _ = _lifecycle(
        MemoryBinding(_auth("refresh-r0")),
        SecretRegistry(),
    )
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    if replacement is None:
        lifecycle.path.unlink()
    elif isinstance(replacement, bytes):
        lifecycle.path.write_bytes(replacement)
    else:
        lifecycle.path.write_text(replacement, encoding="utf-8")

    lifecycle.close()
    lifecycle.close()

    assert not runtime_dir.exists()


def test_close_retains_valid_file_after_unstable_read() -> None:
    lifecycle, _ = _lifecycle(
        MemoryBinding(_auth("refresh-r0")),
        SecretRegistry(),
    )
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    runtime = cast(Any, lifecycle)

    with (
        patch.object(runtime, "_read_stable", return_value=None),
        pytest.raises(CredentialSyncError, match="read safely"),
    ):
        lifecycle.close()

    assert runtime_dir.exists()
    lifecycle.close()
    assert not runtime_dir.exists()


def test_failed_close_keeps_rotated_file_for_retry() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = RecoveringBinding(initial, failures=3)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    runtime = cast(Any, lifecycle)
    runtime._stop.set()
    assert runtime._monitor is not None
    runtime._monitor.join(timeout=1)
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with pytest.raises(CredentialSyncError, match="unavailable"):
        lifecycle.close()

    assert lifecycle.path is not None
    assert lifecycle.path.read_text(encoding="utf-8") == rotated
    assert runtime_dir.exists()

    lifecycle.close()
    assert binding.value == rotated
    assert not runtime_dir.exists()


def test_delayed_close_retry_renews_before_writeback() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = ExpiringRecoveringBinding(initial, replace_failures=3)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with pytest.raises(CredentialSyncError, match="source unavailable"):
        lifecycle.close()

    binding.authorization_valid = False
    binding.due = True
    lifecycle.close()

    assert binding.maintain_calls == 1
    assert binding.value == rotated
    assert not runtime_dir.exists()


def test_retryable_writeback_failure_recovers_on_live_flush() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = RecoveringBinding(initial, failures=3)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with pytest.raises(CredentialSyncError, match="unavailable"):
        lifecycle.flush()
    with pytest.raises(CredentialSyncError, match="unavailable"):
        lifecycle.track_current()

    lifecycle.flush()
    assert binding.value == rotated
    lifecycle.close()


def test_monitor_recovers_retryable_writeback_failure(
    monkeypatch,
) -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = RecoveringBinding(initial, failures=3)
    monkeypatch.setattr(
        "openhands.sdk.agent.acp_file_credentials._SOURCE_RETRY_DELAYS",
        (0,),
    )
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None

    lifecycle.path.write_text(rotated, encoding="utf-8")
    _wait_for_value(binding, rotated)

    lifecycle.close()


def test_conflict_after_ambiguous_commit_converges_when_values_match() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = CommitThenUnavailableBinding(initial)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(rotated, encoding="utf-8")
    binding.load_failures = 2

    with pytest.raises(CredentialSyncError, match="conflict could not be resolved"):
        lifecycle.flush()

    assert binding.value == rotated
    lifecycle.flush()
    assert binding.replace_attempts == 3
    lifecycle.close()


def test_flush_maintains_binding_only_when_due() -> None:
    binding = MaintenanceBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    _stop_monitor(lifecycle)

    lifecycle.flush()
    assert binding.maintain_calls == 0

    binding.due = True
    lifecycle.flush()
    assert binding.maintain_calls == 1
    lifecycle.close()


def test_monitor_maintains_due_binding() -> None:
    binding = MaintenanceBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    binding.due = True
    deadline = time.monotonic() + 2
    while binding.maintain_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.02)

    assert binding.maintain_calls == 1
    lifecycle.close()


def test_monitor_waits_for_renewal_before_rotated_writeback() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = MaintenanceBinding(initial, failures=1)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    binding.due = True

    deadline = time.monotonic() + 2
    while binding.maintain_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert binding.maintain_calls == 1

    lifecycle.path.write_text(rotated, encoding="utf-8")
    time.sleep(0.25)
    assert binding.value == initial
    assert binding.replace_calls == 0

    binding.due = True
    _wait_for_value(binding, rotated)
    assert binding.maintain_calls == 2
    assert binding.replace_calls == 1
    lifecycle.close()


def test_maintenance_failure_is_nonsticky_and_recovers() -> None:
    binding = MaintenanceBinding(_auth("refresh-r0"), failures=1)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    _stop_monitor(lifecycle)
    binding.due = True

    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.flush()
    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.flush()
    assert binding.maintain_calls == 1

    binding.due = True
    lifecycle.flush()
    assert binding.maintain_calls == 2
    lifecycle.close()


def test_failed_close_retries_due_maintenance() -> None:
    binding = MaintenanceBinding(_auth("refresh-r0"), failures=1)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    binding.due = True

    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.flush()

    assert runtime_dir.exists()
    assert binding.maintain_calls == 1
    lifecycle.close()
    assert binding.maintain_calls == 2
    assert not runtime_dir.exists()


def test_failed_close_renewal_retains_runtime_for_retry() -> None:
    binding = MaintenanceBinding(_auth("refresh-r0"), failures=2)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    binding.due = True

    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.flush()
    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.close()

    assert runtime_dir.exists()
    assert binding.maintain_calls == 2

    lifecycle.close()
    assert binding.maintain_calls == 3
    assert not runtime_dir.exists()


def test_close_renews_before_writing_rotated_credential() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = MaintenanceBinding(initial, failures=1)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    binding.due = True

    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.flush()

    lifecycle.path.write_text(rotated, encoding="utf-8")
    lifecycle.close()

    assert binding.maintain_calls == 2
    assert binding.value == rotated
    assert binding.replace_calls == 1
    assert not runtime_dir.exists()


def test_reauthorization_clears_latched_renewal_rejection() -> None:
    binding = ReauthorizableMaintenanceBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    _stop_monitor(lifecycle)
    binding.due = True
    binding.reject_renewal = True

    with pytest.raises(CredentialRenewalRejected, match="renewal rejected"):
        lifecycle.flush()
    with pytest.raises(CredentialRenewalRejected, match="renewal rejected"):
        lifecycle.track_current()

    binding.reauthorize()
    lifecycle.flush()
    lifecycle.close()


def test_close_syncs_rotation_after_renewal_protocol_rejection() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = ReauthorizableMaintenanceBinding(initial)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    binding.due = True
    binding.reject_renewal = True
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with pytest.raises(CredentialRenewalRejected, match="renewal rejected"):
        lifecycle.flush()

    assert binding.value == initial
    lifecycle.close()

    assert binding.value == rotated
    assert binding.replace_calls == 1
    assert binding.maintain_calls == 1
    assert not runtime_dir.exists()


def test_close_observes_inflight_monitor_rejection_before_final_sync(
    monkeypatch,
) -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = BlockingRenewalRejectedBinding(initial)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    binding.due = True
    assert binding.maintain_started.wait(2)
    lifecycle.path.write_text(rotated, encoding="utf-8")
    monkeypatch.setattr(
        "openhands.sdk.agent.acp_file_credentials._MONITOR_JOIN_TIMEOUT_SECONDS",
        0.01,
    )
    errors: list[BaseException] = []

    def close() -> None:
        try:
            lifecycle.close()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=close)
    thread.start()
    time.sleep(0.05)
    assert thread.is_alive()
    binding.maintain_allowed.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert not errors
    assert binding.value == rotated
    assert not runtime_dir.exists()


def test_reauthorization_clears_old_renewal_retry() -> None:
    binding = ReauthorizableMaintenanceBinding(_auth("refresh-r0"), failures=1)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    _stop_monitor(lifecycle)
    binding.due = True

    with pytest.raises(CredentialSyncError, match="could not be renewed"):
        lifecycle.flush()
    assert binding.maintain_calls == 1

    binding.reauthorize()
    lifecycle.close()

    assert binding.maintain_calls == 1


def test_renewed_revision_does_not_clear_its_own_write_rejection() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = RenewThenRejectWriteBinding(initial)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
    binding.due = True
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with pytest.raises(CredentialAuthorizationRejected, match="write rejected"):
        lifecycle.flush()
    with pytest.raises(CredentialAuthorizationRejected, match="write rejected"):
        lifecycle.flush()

    assert binding.maintain_calls == 1
    assert binding.replace_attempts == 1

    binding.reauthorize()
    lifecycle.flush()
    assert binding.value == rotated
    lifecycle.close()


def test_clean_close_skips_due_maintenance() -> None:
    binding = MaintenanceBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    _stop_monitor(lifecycle)
    binding.due = True

    lifecycle.close()

    assert binding.maintain_calls == 0


def test_clean_close_renews_due_authorization_before_changed_writeback() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = ExpiringRecoveringBinding(initial)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(rotated, encoding="utf-8")
    binding.authorization_valid = False
    binding.due = True

    lifecycle.close()

    assert binding.maintain_calls == 1
    assert binding.value == rotated
    assert not runtime_dir.exists()


def test_retry_close_bypasses_persistent_renewal_rejection() -> None:
    initial = _auth("refresh-r0")
    rotated = _auth("refresh-r1")
    binding = PersistentRenewalRejectedBinding(initial, replace_failures=3)
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(rotated, encoding="utf-8")
    binding.due = True

    with pytest.raises(CredentialSyncError, match="source unavailable"):
        lifecycle.close()

    assert runtime_dir.exists()
    assert binding.maintain_calls == 1
    assert binding.replace_attempts == 3

    lifecycle.close()

    assert binding.maintain_calls == 2
    assert binding.replace_attempts == 4
    assert binding.value == rotated
    assert not runtime_dir.exists()


def test_runtime_state_does_not_serialize_binding_values() -> None:
    secret = _auth("never-serialize")
    binding = MemoryBinding(secret)
    lifecycle, env = _lifecycle(binding, SecretRegistry())
    try:
        assert secret not in json.dumps(env)
        assert secret not in json.dumps(lifecycle.__dict__, default=str)
    finally:
        lifecycle.close()
