"""Routing contract for provider connections and OpenRouter endpoints (#6862).

A recognized connection ``provider`` decides routing for a linked profile, and
an ``openrouter.ai`` base URL routes OpenRouter-native ids through OpenRouter.
The HTTP tests dispatch real SDK requests to a local OpenAI-compatible stand-in
so the wire path and model id are checked without credentials.
"""

import json
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, Message, TextContent
from openhands.sdk.llm.exceptions import LLMBadRequestError
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.llm.options.common import is_openrouter
from openhands.sdk.llm.provider_connection_store import (
    ProviderConnection,
    ProviderConnectionModelConflict,
    ProviderConnectionNotFound,
    ProviderConnectionStore,
)
from openhands.sdk.llm.utils.openhands_provider import (
    is_openrouter_base_url,
    litellm_call_kwargs,
)


INKLING = "thinkingmachines/inkling-small:free"
API_KEY = "sk-or-test-secret"


def _connection(**overrides: Any) -> ProviderConnection:
    now = int(time.time())
    data: dict[str, Any] = {
        "id": "conn-or",
        "display_name": "OpenRouter",
        "provider": "openrouter",
        "api_key": API_KEY,
        "base_url": None,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return ProviderConnection(**data)


def _stores(tmp_path: Path, **conn: Any) -> LLMProfileStore:
    provider = ProviderConnectionStore(base_dir=tmp_path / "conns")
    provider.create(_connection(**conn))
    return LLMProfileStore(base_dir=tmp_path / "profiles", provider_store=provider)


# ── ProviderConnection.route_model ──────────────────────────────────────────


@pytest.mark.parametrize(
    "model,expected",
    [
        (INKLING, f"openrouter/{INKLING}"),
        # A vendor namespace is part of the OpenRouter id, not a direct route.
        ("anthropic/claude-sonnet-4", "openrouter/anthropic/claude-sonnet-4"),
        ("openai/gpt-5.5", "openrouter/openai/gpt-5.5"),
        (
            "openrouter/anthropic/claude-sonnet-4",
            "openrouter/anthropic/claude-sonnet-4",
        ),
        ("openrouter/auto", "openrouter/auto"),
    ],
)
def test_openrouter_connection_qualifies_native_ids(model, expected):
    connection = _connection()
    routed = connection.route_model(model)
    assert routed == expected
    assert connection.route_model(routed) == routed


@pytest.mark.parametrize("label", ["OpenRouter", " openrouter "])
def test_openrouter_label_is_case_and_space_insensitive(label):
    assert _connection(provider=label).route_model(INKLING) == f"openrouter/{INKLING}"


@pytest.mark.parametrize(
    "label", ["custom", "anthropic", "openai", "my legacy router", "openrouter-ish"]
)
def test_other_labels_keep_the_stored_model(label):
    connection = _connection(provider=label)
    for model in (INKLING, "anthropic/claude-sonnet-4", "gpt-4o", "openhands/x"):
        assert connection.route_model(model) == model


@pytest.mark.parametrize(
    "model", ["openhands/claude-sonnet-5", "litellm_proxy/gpt-5", "digitalocean/llama"]
)
def test_openrouter_connection_rejects_other_sdk_routes(model):
    with pytest.raises(ProviderConnectionModelConflict) as caught:
        _connection().route_model(model)
    message = str(caught.value)
    assert model in message
    assert "conn-or" in message
    assert "openrouter" in message
    assert API_KEY not in message
    assert issubclass(ProviderConnectionModelConflict, ValueError)


# ── OpenRouter base URL inference ───────────────────────────────────────────


@pytest.mark.parametrize(
    "base_url,expected",
    [
        ("https://openrouter.ai/api/v1", True),
        ("https://openrouter.ai", True),
        (" https://openrouter.ai/api/v1/ ", True),
        ("https://eu.openrouter.ai/api/v1", True),
        ("https://OpenRouter.AI/api/v1", True),
        ("https://openrouter.ai.example.com/v1", False),
        ("https://notopenrouter.ai/v1", False),
        ("https://proxy.example.com/openrouter.ai/v1", False),
        ("http://127.0.0.1:8080/v1", False),
        ("http://[::1", False),
        ("not a url", False),
        ("", False),
        (None, False),
    ],
)
def test_is_openrouter_base_url(base_url, expected):
    assert is_openrouter_base_url(base_url) is expected


@pytest.mark.parametrize(
    "model,base_url,expected",
    [
        (INKLING, "https://openrouter.ai/api/v1", f"openrouter/{INKLING}"),
        (
            "anthropic/claude-sonnet-4",
            "https://openrouter.ai/api/v1",
            "openrouter/anthropic/claude-sonnet-4",
        ),
        (
            f"openrouter/{INKLING}",
            "https://openrouter.ai/api/v1",
            f"openrouter/{INKLING}",
        ),
        ("litellm_proxy/gpt-5", "https://openrouter.ai/api/v1", "litellm_proxy/gpt-5"),
        (INKLING, "https://openrouter.ai.example.com/v1", INKLING),
        ("anthropic/claude-sonnet-4", None, "anthropic/claude-sonnet-4"),
        ("qwen/qwen3-coder", "http://localhost:11434/v1", "qwen/qwen3-coder"),
    ],
)
def test_litellm_call_kwargs_infers_openrouter_from_host(model, base_url, expected):
    assert litellm_call_kwargs(model, base_url) == {
        "model": expected,
        "api_base": base_url,
    }


def test_inline_openrouter_base_url_routes_without_rewriting_config():
    llm = LLM(model=INKLING, base_url="https://openrouter.ai/api/v1", api_key=API_KEY)
    assert llm.model == INKLING
    assert llm._provider_info is not None
    assert llm._provider_info.name == "openrouter"
    assert llm._provider_info.model == INKLING
    assert is_openrouter(llm)

    lookalike = LLM(model=INKLING, base_url="https://openrouter.ai.example.com/v1")
    assert lookalike._provider_info is not None
    assert lookalike._provider_info.name is None
    assert not is_openrouter(lookalike)


# ── Profile resolution ──────────────────────────────────────────────────────


def _write_legacy_profile(store: LLMProfileStore, name: str, model: str) -> Path:
    """Write a linked profile as an earlier release saved it (unprefixed)."""
    store.base_dir.mkdir(parents=True, exist_ok=True)
    path = store.base_dir / f"{name}.json"
    payload = LLM(model=model, provider_connection_id="conn-or").model_dump(mode="json")
    path.write_text(json.dumps(payload))
    return path


def test_saved_unprefixed_profile_routes_through_openrouter(tmp_path):
    profiles = _stores(tmp_path)
    path = _write_legacy_profile(profiles, "inkling", INKLING)
    before = path.read_bytes()

    stored = profiles.load("inkling", resolve_provider=False)
    assert stored._provider_info is not None
    assert stored._provider_info.name is None  # the original failure

    llm = profiles.load("inkling")
    assert llm.model == f"openrouter/{INKLING}"
    assert llm._provider_info is not None
    assert llm._provider_info.name == "openrouter"
    assert llm._provider_info.model == INKLING
    assert llm.base_url is None
    assert isinstance(llm.api_key, SecretStr)
    assert llm.api_key.get_secret_value() == API_KEY
    assert is_openrouter(llm)
    # Resolution happens at use; the stored profile is not rewritten.
    assert path.read_bytes() == before


def test_resolution_is_idempotent_across_load_and_switch(tmp_path):
    profiles = _stores(tmp_path)
    _write_legacy_profile(profiles, "inkling", INKLING)

    loaded = profiles.load("inkling")
    again = profiles.resolve_provider_connection(loaded)
    draft = profiles.resolve_provider_connection(
        LLM(model=INKLING, provider_connection_id="conn-or")
    )

    assert loaded.model == again.model == draft.model == f"openrouter/{INKLING}"


def test_resolution_refreshes_provider_derived_state(tmp_path):
    profiles = _stores(tmp_path)
    draft = LLM(model="anthropic/claude-sonnet-4", provider_connection_id="conn-or")
    assert draft._provider_info is not None
    assert draft._provider_info.name == "anthropic"

    resolved = profiles.resolve_provider_connection(draft)

    assert resolved._provider_info is not None
    assert resolved._provider_info.name == "openrouter"
    assert resolved._provider_info.model == "anthropic/claude-sonnet-4"
    assert (
        resolved.model_info
        == LLM(model="openrouter/anthropic/claude-sonnet-4").model_info
    )


def test_custom_connection_keeps_existing_inference(tmp_path):
    profiles = _stores(tmp_path, provider="custom", base_url="http://127.0.0.1:1/v1")
    draft = LLM(model="openai/gpt-5.5", provider_connection_id="conn-or")

    resolved = profiles.resolve_provider_connection(draft)

    assert resolved.model == "openai/gpt-5.5"
    assert resolved._provider_info is not None
    assert resolved._provider_info.name == "openai"


def test_conflicting_model_fails_load_and_draft_resolution_alike(tmp_path):
    profiles = _stores(tmp_path)
    _write_legacy_profile(profiles, "conflict", "openhands/claude-sonnet-5")

    with pytest.raises(ProviderConnectionModelConflict):
        profiles.load("conflict")
    with pytest.raises(ProviderConnectionModelConflict):
        profiles.resolve_provider_connection(
            LLM(model="openhands/claude-sonnet-5", provider_connection_id="conn-or")
        )
    # Display paths still read the profile as stored.
    stored = profiles.load("conflict", resolve_provider=False)
    assert stored.model == "openhands/claude-sonnet-5"


def test_missing_connection_error_is_unchanged(tmp_path):
    profiles = _stores(tmp_path)
    draft = LLM(model=INKLING, provider_connection_id="ghost")
    with pytest.raises(ProviderConnectionNotFound, match="ghost"):
        profiles.resolve_provider_connection(draft)


# ── Request dispatch through a local OpenRouter stand-in ────────────────────


_CHAT_REPLY = {
    "id": "chat-1",
    "object": "chat.completion",
    "created": 1,
    "model": INKLING,
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "pong"},
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}

_RESPONSES_REPLY = {
    "id": "resp-1",
    "object": "response",
    "created_at": 1,
    "model": INKLING,
    "status": "completed",
    "parallel_tool_calls": False,
    "tool_choice": "auto",
    "tools": [],
    "output": [
        {
            "type": "message",
            "id": "msg-1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "pong", "annotations": []}],
        }
    ],
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
}


@pytest.fixture
def openrouter_standin() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """An OpenAI-compatible server standing in for OpenRouter on localhost."""
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": body,
                }
            )
            reply = (
                _RESPONSES_REPLY if self.path.endswith("/responses") else _CHAT_REPLY
            )
            payload = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


_PING = [
    Message(role="system", content=[TextContent(text="Reply with one token.")]),
    Message(role="user", content=[TextContent(text="ping")]),
]


@pytest.mark.parametrize("model", [INKLING, "anthropic/claude-sonnet-4"])
@pytest.mark.parametrize("use_async", [False, True], ids=["sync", "async"])
@pytest.mark.asyncio
async def test_linked_profile_dispatches_native_id_to_connection_endpoint(
    tmp_path, openrouter_standin, model, use_async
):
    base_url, requests = openrouter_standin
    profiles = _stores(tmp_path, base_url=base_url)
    _write_legacy_profile(profiles, "linked", model)

    llm = profiles.load("linked").model_copy(update={"num_retries": 0})
    response = await llm.agenerate(_PING) if use_async else llm.generate(_PING)

    assert response.message.content == [TextContent(text="pong")]
    assert len(requests) == 1
    assert requests[0]["path"] == "/api/v1/chat/completions"
    assert requests[0]["body"]["model"] == model
    assert requests[0]["authorization"] == f"Bearer {API_KEY}"


def test_linked_profile_dispatches_responses_api(tmp_path, openrouter_standin):
    base_url, requests = openrouter_standin
    profiles = _stores(tmp_path, base_url=base_url)
    profiles.save(
        "responses",
        LLM(model=INKLING, provider_connection_id="conn-or", api_mode="responses"),
    )

    llm = profiles.load("responses").model_copy(update={"num_retries": 0})
    response = llm.generate(_PING)

    assert response.message.content == [TextContent(text="pong")]
    assert len(requests) == 1
    assert requests[0]["path"] == "/api/v1/responses"
    assert requests[0]["body"]["model"] == INKLING
    assert requests[0]["authorization"] == f"Bearer {API_KEY}"


def test_custom_label_keeps_failing_provider_inference(tmp_path, openrouter_standin):
    """Legacy labels keep today's routing: the unqualified id still has no route.

    This is the original #6862 failure, now confined to connections that do not
    say they are OpenRouter.
    """
    base_url, requests = openrouter_standin
    profiles = _stores(tmp_path, provider="custom", base_url=base_url)
    _write_legacy_profile(profiles, "custom", INKLING)

    llm = profiles.load("custom").model_copy(update={"num_retries": 0})
    with pytest.raises(LLMBadRequestError, match="LLM Provider NOT provided") as caught:
        llm.generate(_PING)

    assert requests == []
    assert API_KEY not in str(caught.value)
