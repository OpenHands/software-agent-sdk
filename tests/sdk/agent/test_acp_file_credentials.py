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
    def __init__(self, value: str) -> None:
        super().__init__(value)
        self.authorization_revision = 0
        self.rejected = True

    async def replace(self, expected_version: str, value: str) -> str:
        if self.rejected:
            raise CredentialAuthorizationRejected("rejected")
        return await super().replace(expected_version, value)

    def reauthorize(self) -> None:
        self.rejected = False
        self.authorization_revision += 1


class FailingBinding(MemoryBinding):
    async def replace(self, expected_version: str, value: str) -> str:
        raise CredentialSyncError("unavailable")


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


def test_mask_tracking_does_not_sleep_or_write() -> None:
    initial = _auth("refresh-r0", "access-r0")
    rotated = _auth("refresh-r1", "access-r1")
    binding = MemoryBinding(initial)
    registry = SecretRegistry()
    lifecycle, _ = _lifecycle(binding, registry)
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
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


def test_unstable_read_does_not_poison_lifecycle() -> None:
    rotated = _auth("refresh-r1")
    binding = MemoryBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
    runtime = cast(Any, lifecycle)
    lifecycle.path.write_text(rotated, encoding="utf-8")

    with (
        patch.object(runtime, "_read_stable", return_value=None),
        pytest.raises(CredentialSyncError, match="read safely"),
    ):
        lifecycle.flush()

    lifecycle.flush()
    assert binding.value == rotated
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
    lifecycle.path.write_text(rotated, encoding="utf-8")
    lifecycle.flush()
    assert binding.value == rotated
    assert binding.replace_calls == 1
    lifecycle.close()


def test_exhausted_writeback_failure_is_sticky() -> None:
    binding = FailingBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    runtime_dir = lifecycle.path.parent
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(_auth("refresh-r1"), encoding="utf-8")

    with pytest.raises(CredentialSyncError, match="unavailable"):
        lifecycle.flush()
    with pytest.raises(CredentialSyncError, match="unavailable"):
        lifecycle.flush()
    with pytest.raises(CredentialSyncError, match="unavailable"):
        lifecycle.close()

    assert runtime_dir.exists()
    lifecycle.discard()
    assert not runtime_dir.exists()


def test_conflict_is_sticky() -> None:
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
    with pytest.raises(CredentialConflict):
        lifecycle.close()

    assert runtime_dir.exists()
    lifecycle.discard()


def test_deleted_credential_error_is_sticky() -> None:
    binding = MissingBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(_auth("refresh-r1"), encoding="utf-8")

    with pytest.raises(CredentialNeedsReauthentication):
        lifecycle.flush()
    with pytest.raises(CredentialNeedsReauthentication):
        lifecycle.track_current()
    with pytest.raises(CredentialNeedsReauthentication):
        lifecycle.close()

    lifecycle.discard()


def test_reauthorization_clears_authorization_rejection() -> None:
    binding = RevokedBinding(_auth("refresh-r0"))
    lifecycle, _ = _lifecycle(binding, SecretRegistry())
    assert lifecycle.path is not None
    _stop_monitor(lifecycle)
    lifecycle.path.write_text(_auth("refresh-r1"), encoding="utf-8")

    with pytest.raises(CredentialAuthorizationRejected):
        lifecycle.flush()
    binding.reauthorize()
    lifecycle.flush()

    assert binding.value == _auth("refresh-r1")
    lifecycle.close()


def test_runtime_state_does_not_serialize_binding_values() -> None:
    secret = _auth("never-serialize")
    binding = MemoryBinding(secret)
    lifecycle, env = _lifecycle(binding, SecretRegistry())
    try:
        assert secret not in json.dumps(env)
        assert secret not in json.dumps(lifecycle.__dict__, default=str)
    finally:
        lifecycle.close()
