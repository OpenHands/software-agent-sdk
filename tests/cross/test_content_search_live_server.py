"""Live-server tests for the opt-in content-search feature.

Runs a real uvicorn agent-server (the ``live_server_env`` harness also used
by ``test_remote_conversation_live_server.py``) so the flag gating and the
rename-reindex path are exercised over actual HTTP, complementing the
TestClient unit suite in ``tests/search/``.

Covers the two acceptance criteria from software-agent-sdk#6767 that the
unit suite does not:
  - live-server flag-off/flag-on behavior (service mounted or absent), and
  - a PATCHed title being found by the next search (rename reindex).
"""

import os
import time
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from openhands.agent_server.config import load_config
from openhands.sdk import LLM, Agent
from tests.cross.conftest import live_server_env
from tests.search.conftest import write_conversation


TEST_API_KEY = "test-key"


def _client(host: str) -> httpx.Client:
    return httpx.Client(
        base_url=host, trust_env=False, headers={"X-Session-API-Key": TEST_API_KEY}
    )


def _search(client: httpx.Client, query: str) -> dict:
    resp = client.get("/api/conversations/search-content", params={"query": query})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_content_search_flag_gating(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """The search route must exist only when the opt-in flag is set.

    The server is started twice from the same layout: first with the flag off
    (default), then on. ``live_server_env`` wipes and re-creates the
    conversations dir at startup, so conversations are seeded only after the
    server is up (the service syncs incrementally per request, so files
    created while it runs are picked up).
    Off: no route, no OpenAPI entry, and the 404/422 fallthrough matches any
    unknown /conversations/ path. On: title and message hits come back over
    real HTTP.
    """

    def seed():
        write_conversation(
            tmp_path / "conversations",
            "Garage door sensor",
            [
                ("user", "The garage door sensor keeps triggering false alarms."),
                ("agent", "False alarms happen below five degrees at night."),
            ],
        )

    for enabled, expect_route in ((False, False), (True, True)):
        with live_server_env(
            tmp_path,
            monkeypatch,
            session_api_keys=[TEST_API_KEY],
            config_overrides={"content_search_enabled": enabled},
        ) as env:
            seed()
            with _client(env["host"]) as client:
                schema = client.get("/openapi.json").json()
                assert (
                    "/api/conversations/search-content" in schema["paths"]
                ) is expect_route

                unknown = client.get("/api/conversations/definitely-not-a-route")
                if not expect_route:
                    resp = client.get(
                        "/api/conversations/search-content", params={"query": "garage"}
                    )
                    assert resp.status_code == unknown.status_code
                    a, b = resp.json()["detail"][0], unknown.json()["detail"][0]
                    # Live routing: both 422 UUID-parse errors differing only
                    # in the echoed path input — same type, same loc.
                    assert a["type"] == b["type"]
                    assert a["loc"] == b["loc"]
                else:
                    body = _search(client, "garage")
                    assert body["total"] >= 1
                    assert {h["source"] for h in body["results"]} >= {"title"}
                    hit = next(h for h in body["results"] if h["source"] == "title")
                    assert hit["title"] == "Garage door sensor"
                    assert hit["snippet"]
                    body = _search(client, "false alarms")
                    assert body["total"] >= 1
                    assert any(h["event_id"] for h in body["results"])


def test_content_search_flag_env_var(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """``OH_CONTENT_SEARCH_ENABLED`` must drive the flag through the same
    env-parser merge production deployments use (load_config), so a
    deployment never needs to hand-edit a JSON config file to enable it."""
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text("{}")
    monkeypatch.setenv("OPENHANDS_AGENT_SERVER_CONFIG_PATH", str(cfg_path))

    monkeypatch.setenv("OH_CONTENT_SEARCH_ENABLED", "1")
    assert load_config().content_search_enabled is True

    # Env overrides JSON: enabling via env beats a disabled config file.
    cfg_path.write_text('{"content_search_enabled": false}')
    monkeypatch.setenv("OH_CONTENT_SEARCH_ENABLED", "true")
    assert load_config().content_search_enabled is True

    monkeypatch.delenv("OH_CONTENT_SEARCH_ENABLED")
    assert load_config().content_search_enabled is False


def test_rename_reindexes_title(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """PATCHing a title must be visible to the next search (rename reindex).

    Renames persist through ``event_service.save_meta``, which rewrites
    meta.json in place. Backfill's incremental sync keys on the
    conversation *directory* mtime, and an in-place file rewrite does not
    necessarily bump the directory's mtime — so this test pins the
    rename-reindex criterion end-to-end: if the index goes stale on rename,
    this fails. mtime granularity is handled by ``os.utime`` only as a
    timing hygiene measure for same-second runs, not to mask a skip.
    """
    with live_server_env(
        tmp_path,
        monkeypatch,
        session_api_keys=[TEST_API_KEY],
        config_overrides={"content_search_enabled": True},
    ) as env:
        with _client(env["host"]) as client:
            agent = Agent(
                llm=LLM(model="gpt-4o-mini", api_key=SecretStr("test")),
                tools=[],
            )
            resp = client.post(
                "/api/conversations",
                json={
                    "agent": agent.model_dump(
                        mode="json", context={"expose_secrets": True}
                    ),
                    "workspace": {"working_dir": str(env["workspace_path"])},
                },
            )
            assert resp.status_code == 201, resp.text
            conv_id = resp.json()["id"]
            # On-disk conversation dirs use the undashed hex form of the id.
            meta_dir = Path(
                env["app"].state.config.conversations_path
            ) / conv_id.replace("-", "")

            # The real conversation has no title until set, and no events
            # beyond launch. Give it a baseline title that can be renamed.
            baseline = "Baseline title before rename"
            resp = client.patch(
                f"/api/conversations/{conv_id}", json={"title": baseline}
            )
            assert resp.status_code == 200, resp.text
            # mtime hygiene: ensure the PATCH write is observably newer than
            # any prior index sync, so a failure here is a logic bug, not
            # filesystem timing.
            deadline = time.time() + 10
            while not (meta_dir / "meta.json").is_file() and time.time() < deadline:
                time.sleep(0.05)
            time.sleep(0.01)
            os.utime(meta_dir / "meta.json", None)

            assert _search(client, baseline)["total"] == 1

            # The rename itself.
            renamed = "Renamed after PATCH"
            resp = client.patch(
                f"/api/conversations/{conv_id}", json={"title": renamed}
            )
            assert resp.status_code == 200, resp.text
            os.utime(meta_dir / "meta.json", None)

            body = _search(client, renamed)
            assert body["total"] == 1
            assert body["results"][0]["title"] == renamed
            assert _search(client, baseline)["total"] == 0
