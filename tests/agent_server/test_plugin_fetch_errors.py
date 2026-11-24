"""REST regressions for explicit plugin fetch failures.

The production path is the FastAPI app and a local ``file://`` git repository.
These tests do not mock plugin fetching.
"""

import json
import subprocess
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from openhands.agent_server.api import create_app
from openhands.agent_server.config import Config
from openhands.agent_server.conversation_service import INVALID_PLUGIN_SOURCE_MESSAGE
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.plugin.fetch import (
    PLUGIN_FETCH_CLIENT_MESSAGE,
    PluginFetchError,
)
from tests.command_utils import touch_command


_SECRET = "s3cr3t-token"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _write_plugin(plugin_dir: Path, marker: Path) -> None:
    manifest = plugin_dir / ".plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        json.dumps({"name": "marker-plugin", "version": "1.0.0"})
    )
    skill = plugin_dir / "skills" / "marker-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: marker-skill\ndescription: Marks that the plugin loaded\n---\n\n"
        "Marker skill body.\n"
    )
    hooks = plugin_dir / "hooks"
    hooks.mkdir()
    command = touch_command(marker)
    (hooks / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": command}]}
                    ]
                }
            }
        )
    )


def _publish_bare(plugin_dir: Path, bare: Path) -> None:
    _git(plugin_dir, "init", "-b", "main")
    _git(plugin_dir, "add", ".")
    _git(
        plugin_dir,
        "-c",
        "user.name=OpenHands Test",
        "-c",
        "user.email=openhands@example.com",
        "commit",
        "-m",
        "plugin",
    )
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(plugin_dir, "remote", "add", "origin", str(bare))
    _git(plugin_dir, "push", "origin", "HEAD")


def _agent(marketplaces: list[dict[str, object]] | None = None) -> dict[str, object]:
    agent: dict[str, object] = {
        "kind": "Agent",
        "llm": {"model": "openai/test", "usage_id": "test"},
        "tools": [],
    }
    if marketplaces is not None:
        agent["agent_context"] = {"registered_marketplaces": marketplaces}
    return agent


def _payload(
    workspace: Path,
    *,
    plugins: list[dict[str, str]] | None = None,
    marketplaces: list[dict[str, object]] | None = None,
    initial_message: str | None = None,
) -> dict[str, object]:
    body: dict[str, object] = {
        "workspace": {"working_dir": str(workspace)},
        "agent": _agent(marketplaces),
        "autotitle": False,
    }
    if plugins is not None:
        body["plugins"] = plugins
    if initial_message is not None:
        body["initial_message"] = {
            "role": "user",
            "content": [{"type": "text", "text": initial_message}],
        }
    return body


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "openhands.sdk.plugin.fetch.DEFAULT_CACHE_DIR",
        tmp_path / "plugin-cache",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = Config(
        session_api_keys=[],
        conversations_path=tmp_path / "conversations",
    )
    with TestClient(create_app(config), raise_server_exceptions=False) as test_client:
        yield test_client, workspace


def _count(client: TestClient) -> int:
    response = client.get("/api/conversations/count")
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), int)
    return response.json()


def _events(client: TestClient, conversation_id: str, kind: str) -> list[dict]:
    response = client.get(
        f"/api/conversations/{conversation_id}/events/search",
        params={"kind": kind, "limit": 100},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _user_texts(client: TestClient, conversation_id: str) -> list[str]:
    texts: list[str] = []
    for event in _events(client, conversation_id, "MessageEvent"):
        if event.get("source") != "user":
            continue
        texts.append(
            "".join(
                part.get("text", "")
                for part in event["llm_message"]["content"]
                if isinstance(part, dict)
            )
        )
    return texts


def _send(client: TestClient, conversation_id: str, text: str):
    return client.post(
        f"/api/conversations/{conversation_id}/events",
        json={
            "role": "user",
            "content": [{"type": "text", "text": text}],
            "run": False,
        },
    )


def _assert_plugin_fetch_error(response, *, secret: str | None = None) -> dict:
    assert response.status_code == 502, response.text
    body = response.json()
    assert "error_id" not in body
    detail = body["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "PluginFetchError"
    assert detail["message"] == PLUGIN_FETCH_CLIENT_MESSAGE
    assert "Failed to fetch" not in response.text
    if secret is not None:
        assert secret not in response.text
    return detail


def test_explicit_plugin_fetch_failure_is_a_structured_502(client, caplog) -> None:
    """A missing explicit plugin used to fall through to an unhandled 500."""
    test_client, workspace = client
    source = (
        "https://user:"
        + _SECRET
        + "@127.0.0.1:9/plugin.git?token="
        + _SECRET
        + "#"
        + _SECRET
    )
    created = test_client.post(
        "/api/conversations",
        json=_payload(workspace, plugins=[{"source": source}]),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    with caplog.at_level("ERROR"):
        response = _send(test_client, conversation_id, "hello")

    detail = _assert_plugin_fetch_error(response, secret=_SECRET)
    assert detail["conversation_id"] == conversation_id
    assert detail["source"] == "https://127.0.0.1:9/plugin.git"
    assert "Unhandled exception" not in caplog.text
    assert _user_texts(test_client, conversation_id) == []


def test_failed_fetch_retries_without_duplicating_the_message(
    client, tmp_path: Path
) -> None:
    test_client, workspace = client
    bare = tmp_path / "plugin.git"
    marker = tmp_path / "hook-ran"
    plugin_dir = tmp_path / "plugin-work"
    _write_plugin(plugin_dir, marker)
    source = bare.as_uri()

    created = test_client.post(
        "/api/conversations",
        json=_payload(workspace, plugins=[{"source": source}]),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    failed = _send(test_client, conversation_id, "hello once")
    _assert_plugin_fetch_error(failed)
    assert _user_texts(test_client, conversation_id) == []

    _publish_bare(plugin_dir, bare)
    retried = _send(test_client, conversation_id, "hello once")
    assert retried.status_code == 200, retried.text
    assert _user_texts(test_client, conversation_id) == ["hello once"]
    assert marker.is_file()


def test_invalid_plugin_source_is_422_before_persistence(client) -> None:
    test_client, workspace = client
    assert _count(test_client) == 0

    response = test_client.post(
        "/api/conversations",
        json=_payload(workspace, plugins=[{"source": "not a source " + _SECRET}]),
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == {
        "code": "InvalidPluginSource",
        "message": INVALID_PLUGIN_SOURCE_MESSAGE,
    }
    assert _SECRET not in response.text
    assert "not a source" not in response.text
    assert _count(test_client) == 0
    stored = workspace.parent / "conversations"
    assert not stored.exists() or not any(stored.iterdir())


def test_initial_message_fetch_failure_names_the_conversation(
    client, tmp_path: Path
) -> None:
    test_client, workspace = client
    source = (tmp_path / "missing-plugin.git").as_uri()
    response = test_client.post(
        "/api/conversations",
        json=_payload(
            workspace,
            plugins=[{"source": source}],
            initial_message="first words",
        ),
    )

    detail = _assert_plugin_fetch_error(response)
    conversation_id = detail["conversation_id"]
    assert response.headers.get("x-openhands-serverconversation-id") == conversation_id
    loaded = test_client.get(f"/api/conversations/{conversation_id}")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["id"] == conversation_id
    assert _user_texts(test_client, conversation_id) == []
    assert _count(test_client) == 1


def test_repeated_explicit_failure_keeps_one_marketplace_error(client) -> None:
    test_client, workspace = client
    created = test_client.post(
        "/api/conversations",
        json=_payload(
            workspace,
            plugins=[{"source": "https://127.0.0.1:9/explicit.git"}],
            marketplaces=[
                {
                    "name": "broken",
                    "source": "https://user:" + _SECRET + "@127.0.0.1:9/catalog.git",
                    "auto_load": True,
                }
            ],
        ),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    first = _send(test_client, conversation_id, "one")
    second = _send(test_client, conversation_id, "two")
    _assert_plugin_fetch_error(first)
    _assert_plugin_fetch_error(second)
    assert _SECRET not in first.text
    assert _SECRET not in second.text

    errors = _events(test_client, conversation_id, "ConversationErrorEvent")
    marketplace = [event for event in errors if event["code"] == "MarketplaceLoadError"]
    assert len(marketplace) == 1
    assert _SECRET not in marketplace[0]["detail"]
    assert "https://127.0.0.1:9/catalog.git" in marketplace[0]["detail"]
    assert "****@" not in marketplace[0]["detail"]
    assert _user_texts(test_client, conversation_id) == []


def test_bad_auto_load_without_explicit_plugin_still_accepts_the_message(
    client,
) -> None:
    test_client, workspace = client
    created = test_client.post(
        "/api/conversations",
        json=_payload(
            workspace,
            marketplaces=[
                {
                    "name": "broken",
                    "source": "https://127.0.0.1:9/catalog.git",
                    "auto_load": True,
                }
            ],
        ),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    response = _send(test_client, conversation_id, "still works")

    assert response.status_code == 200, response.text
    assert _user_texts(test_client, conversation_id) == ["still works"]
    codes = [
        event["code"]
        for event in _events(test_client, conversation_id, "ConversationErrorEvent")
    ]
    assert codes == ["MarketplaceLoadError"]


def test_plugin_fetch_error_detail_omits_synthetic_secrets() -> None:
    """The serialized 502 detail is built only from the fixed text and locator."""
    secret = "s3cr3t-token"
    exc = PluginFetchError(
        f"Failed to fetch plugin from https://user:{secret}@h/r.git?token={secret}",
        source=f"https://user:{secret}@h/r.git?token={secret}#{secret}",
        conversation_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    rendered = json.dumps(exc.detail)
    assert secret not in rendered
    assert "Failed to fetch" not in rendered
    assert exc.detail == {
        "code": "PluginFetchError",
        "message": PLUGIN_FETCH_CLIENT_MESSAGE,
        "source": "https://h/r.git",
        "conversation_id": "11111111-1111-1111-1111-111111111111",
    }
    assert secret not in str(exc)


def test_secret_template_is_accepted_and_filled_before_send(
    client, tmp_path: Path
) -> None:
    test_client, workspace = client
    bare = tmp_path / "plugin.git"
    marker = tmp_path / "hook-ran"
    plugin_dir = tmp_path / "plugin-work"
    _write_plugin(plugin_dir, marker)
    _publish_bare(plugin_dir, bare)

    created = test_client.post(
        "/api/conversations",
        json=_payload(workspace, plugins=[{"source": "${PLUGIN_SOURCE}"}]),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    secret = test_client.post(
        f"/api/conversations/{conversation_id}/secrets",
        json={"secrets": {"PLUGIN_SOURCE": bare.as_uri()}},
    )
    assert secret.status_code == 200, secret.text
    response = _send(test_client, conversation_id, "from template")
    assert response.status_code == 200, response.text
    assert _user_texts(test_client, conversation_id) == ["from template"]
    assert marker.is_file()


def test_unshapable_template_is_422_before_persistence(client) -> None:
    test_client, workspace = client
    response = test_client.post(
        "/api/conversations",
        json=_payload(workspace, plugins=[{"source": "not a source ${PLUGIN_SOURCE}"}]),
    )
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "InvalidPluginSource"
    assert "PLUGIN_SOURCE" not in response.text
    assert _count(test_client) == 0


def test_expanded_secret_is_absent_from_the_fetch_error(client) -> None:
    test_client, workspace = client
    secret = "s3cr3t/token"
    created = test_client.post(
        "/api/conversations",
        json=_payload(
            workspace,
            plugins=[
                {"source": "https://x-access-token:${TOKEN}@127.0.0.1:9/plugin.git"}
            ],
        ),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    posted = test_client.post(
        f"/api/conversations/{conversation_id}/secrets",
        json={"secrets": {"TOKEN": secret}},
    )
    assert posted.status_code == 200, posted.text

    response = _send(test_client, conversation_id, "hidden")
    detail = _assert_plugin_fetch_error(response, secret=secret)
    assert quote(secret, safe="") not in response.text
    assert detail["source"] == "https://127.0.0.1:9/plugin.git"
    assert "${TOKEN}" not in detail["source"]


def test_reuse_does_not_reject_a_stored_conversation_for_bad_plugins(client) -> None:
    test_client, workspace = client
    created = test_client.post("/api/conversations", json=_payload(workspace))
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    payload = _payload(workspace, plugins=[{"source": "not a source"}])
    payload["conversation_id"] = conversation_id
    reused = test_client.post("/api/conversations", json=payload)
    assert reused.status_code == 200, reused.text
    assert reused.json()["id"] == conversation_id
    assert _count(test_client) == 1


def test_distinct_auto_load_failures_are_reported_once_each(client) -> None:
    test_client, workspace = client
    created = test_client.post(
        "/api/conversations",
        json=_payload(
            workspace,
            marketplaces=[
                {
                    "name": "alpha",
                    "source": "https://127.0.0.1:9/alpha.git",
                    "auto_load": True,
                },
                {
                    "name": "beta",
                    "source": "https://127.0.0.1:9/beta.git",
                    "auto_load": True,
                },
            ],
        ),
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]

    first = _send(test_client, conversation_id, "still works")
    second = _send(test_client, conversation_id, "still works again")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    codes = [
        event["code"]
        for event in _events(test_client, conversation_id, "ConversationErrorEvent")
    ]
    assert codes == ["MarketplaceLoadError", "MarketplaceLoadError"]
    details = [
        event["detail"]
        for event in _events(test_client, conversation_id, "ConversationErrorEvent")
    ]
    assert any("alpha" in detail for detail in details)
    assert any("beta" in detail for detail in details)


def test_auto_load_dedupe_ignores_changing_error_text(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    conversation = LocalConversation(
        agent=Agent(
            llm=LLM(model="test/model", api_key=SecretStr("test-key")),
            tools=[],
        ),
        workspace=workspace,
        visualizer=None,
    )
    try:
        conversation._emit_auto_load_error(
            "MarketplaceLoadError",
            "first failure text",
            identity="marketplace:broken",
        )
        conversation._emit_auto_load_error(
            "MarketplaceLoadError",
            "second failure text that changed",
            identity="marketplace:broken",
        )
        conversation._emit_auto_load_error(
            "MarketplaceLoadError",
            "other marketplace",
            identity="marketplace:other",
        )
        errors = [
            event
            for event in conversation._state.events
            if isinstance(event, ConversationErrorEvent)
        ]
        assert [event.detail for event in errors] == [
            "first failure text",
            "other marketplace",
        ]
    finally:
        conversation.close()
