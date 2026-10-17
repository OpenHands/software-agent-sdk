"""Profile-store lock waits must not block the agent-server event loop."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

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
    lock = FileLock(store.base_dir / lock_name)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        with lock:
            files_before = {
                file_path.relative_to(persistence_dir): file_path.read_bytes()
                for file_path in persistence_dir.rglob("*")
                if file_path.is_file()
            }
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
            assert {
                file_path.relative_to(persistence_dir): file_path.read_bytes()
                for file_path in persistence_dir.rglob("*")
                if file_path.is_file()
            } == files_before

        lock_wait_started.clear()
        with lock:
            released_request = executor.submit(
                client.request, method, path, json=payload
            )
            assert lock_wait_started.wait(timeout=2)

        assert released_request.result(timeout=2).status_code == success_status
