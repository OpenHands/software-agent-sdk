"""Profile-store lock waits must not block the agent-server event loop."""

import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server import (
    agent_profiles_router,
    meta_profiles_router,
    profiles_router,
    provider_connections_router,
)
from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.meta_profile_store import MetaProfileStore
from openhands.sdk.llm.provider_connection_store import ProviderConnectionStore
from openhands.sdk.profiles import AgentProfileStore


@pytest.mark.parametrize(
    ("store_kind", "method", "path", "payload", "lock_name", "success_status"),
    [
        (
            "llm",
            "POST",
            "/api/profiles/qa-busy",
            {"llm": {"model": "openai/qa-busy"}},
            ".profiles.lock",
            201,
        ),
        (
            "agent",
            "GET",
            "/api/agent-profiles/missing",
            None,
            ".agent-profiles.lock",
            404,
        ),
        (
            "meta",
            "POST",
            "/api/meta-profiles/qa-busy",
            {
                "classifier_model": "minimax",
                "classes": [{"description": "UI", "model": "deepseek"}],
            },
            ".meta-profiles.lock",
            201,
        ),
        (
            "provider",
            "GET",
            "/api/llm/provider-connections",
            None,
            ".provider-connections.lock",
            200,
        ),
    ],
)
def test_profile_store_lock_wait_keeps_liveness_responsive(
    store_kind, method, path, payload, lock_name, success_status, tmp_path, monkeypatch
):
    persistence_dir = tmp_path / ".openhands"
    monkeypatch.setenv("OH_PERSISTENCE_DIR", str(persistence_dir))
    stores = {
        "llm": LLMProfileStore(base_dir=persistence_dir / "profiles"),
        "agent": AgentProfileStore(base_dir=persistence_dir / "agent-profiles"),
        "meta": MetaProfileStore(base_dir=persistence_dir / "meta-profiles"),
        "provider": ProviderConnectionStore(
            base_dir=persistence_dir / "provider-connections"
        ),
    }
    store = stores[store_kind]

    if store_kind == "llm":
        monkeypatch.setattr(profiles_router, "get_llm_profile_store", lambda: store)
    elif store_kind == "agent":
        monkeypatch.setattr(
            agent_profiles_router, "get_agent_profile_store", lambda: store
        )
    elif store_kind == "meta":
        monkeypatch.setattr(
            meta_profiles_router, "_get_meta_profile_store", lambda: store
        )
    else:
        monkeypatch.setattr(
            provider_connections_router,
            "get_provider_connections_store",
            lambda config=None: store,
        )

    lock_wait_started = threading.Event()
    both_lock_waits_started = threading.Event()
    lock_wait_count = 0
    lock_wait_count_lock = threading.Lock()
    acquire = store._acquire_lock

    @contextmanager
    def acquire_with_short_timeout():
        nonlocal lock_wait_count
        with lock_wait_count_lock:
            lock_wait_count += 1
            if lock_wait_count == 2:
                both_lock_waits_started.set()
        lock_wait_started.set()
        with acquire(timeout=2.5):
            yield

    monkeypatch.setattr(store, "_acquire_lock", acquire_with_short_timeout)

    app = create_app(
        Config(static_files_path=None, session_api_keys=[], secret_key=None)
    )
    lock_path = store.base_dir / lock_name

    def snapshot_persisted_files() -> dict[Path, bytes]:
        return {
            file_path.relative_to(persistence_dir): file_path.read_bytes()
            for file_path in persistence_dir.rglob("*")
            if file_path.is_file() and file_path != lock_path
        }

    @contextmanager
    def hold_lock_in_process(suffix: str) -> Iterator[None]:
        ready_path = tmp_path / f"{store_kind}-{suffix}.ready"
        holder_code = """
import sys
import time
from pathlib import Path
from filelock import FileLock

lock_path = Path(sys.argv[1])
ready_path = Path(sys.argv[2])
with FileLock(lock_path):
    ready_path.touch()
    while True:
        time.sleep(1)
"""
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                holder_code,
                str(lock_path),
                str(ready_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5
            while not ready_path.exists():
                if holder.poll() is not None:
                    raise AssertionError("Lock holder exited before acquiring")
                if time.monotonic() >= deadline:
                    raise AssertionError("Lock holder did not acquire lock")
                time.sleep(0.01)
            yield
        finally:
            if holder.poll() is None:
                holder.terminate()
            try:
                holder.wait(timeout=5)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait()

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        with hold_lock_in_process("busy"):
            files_before = snapshot_persisted_files()
            waiting_requests = [
                executor.submit(client.request, method, path, json=payload)
                for _ in range(2)
            ]
            assert both_lock_waits_started.wait(timeout=2)

            for probe in ("/alive", "/health", "/ready"):
                started_at = time.monotonic()
                response = client.get(probe)
                assert response.status_code == 200
                assert time.monotonic() - started_at < 2

            assert [
                response.result(timeout=3).status_code for response in waiting_requests
            ] == [503, 503]
            assert snapshot_persisted_files() == files_before

        lock_wait_started.clear()
        with hold_lock_in_process("released"):
            released_request = executor.submit(
                client.request, method, path, json=payload
            )
            assert lock_wait_started.wait(timeout=2)

        assert released_request.result(timeout=2).status_code == success_status
