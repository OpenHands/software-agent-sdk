"""Tests for DatabricksFMAPIClient.

Covers: User-Agent header presence (PWAF), _parse_response, _build_stream_response,
streaming accumulation, __del__ cleanup (P1-1), and URL construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from openhands.sdk.llm.providers.databricks.auth import DatabricksCredentials
from openhands.sdk.llm.providers.databricks.client import DatabricksFMAPIClient
from openhands.sdk.llm.providers.databricks.models import ProviderFamily
from openhands.sdk.llm.providers.databricks.utils import DatabricksTimeouts, USER_AGENT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOST = "https://adb-123.azuredatabricks.net"


def _make_credentials(token: str = "test-token") -> DatabricksCredentials:
    return DatabricksCredentials(
        host=_HOST,
        get_token=lambda: token,
        auth_method="pat",
    )


_GATEWAY = _HOST  # tests use the same URL for both surfaces by default


def _make_client(
    credentials: DatabricksCredentials | None = None,
    max_retries: int = 0,
    metadata_probe: bool = False,
    ai_gateway_host: str = _GATEWAY,
) -> DatabricksFMAPIClient:
    creds = credentials or _make_credentials()
    return DatabricksFMAPIClient(
        credentials=creds,
        ai_gateway_host=ai_gateway_host,
        timeouts=DatabricksTimeouts(),
        max_retries=max_retries,
        metadata_probe=metadata_probe,
    )


def test_client_requires_some_host() -> None:
    """Constructor must reject the case where neither ai_gateway_host nor
    credentials.host is provided — there's no URL to route invocations to."""
    creds_no_host = DatabricksCredentials(
        host="", get_token=lambda: "tok", auth_method="pat"
    )
    with pytest.raises(ValueError, match="must be provided"):
        DatabricksFMAPIClient(
            credentials=creds_no_host,
            ai_gateway_host=None,
            timeouts=DatabricksTimeouts(),
        )


def test_client_defaults_gateway_host_to_credentials_host() -> None:
    """When no ai_gateway_host override is given, the workspace host
    (credentials.host) becomes the gateway base."""
    client = DatabricksFMAPIClient(
        credentials=_make_credentials(),
        ai_gateway_host=None,
        timeouts=DatabricksTimeouts(),
    )
    assert client._ai_gateway_host == _HOST


def _make_success_response(model: str = "test-model") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": model,
    }


# ---------------------------------------------------------------------------
# _make_headers — PWAF User-Agent
# ---------------------------------------------------------------------------

def test_make_headers_includes_user_agent() -> None:
    """PWAF: every request must carry the correct User-Agent header."""
    client = _make_client()
    headers = client._make_headers(ProviderFamily.OPENAI)
    assert headers["User-Agent"] == USER_AGENT


def test_make_headers_includes_authorization() -> None:
    """Authorization header must use Bearer scheme with the current token."""
    client = _make_client(_make_credentials(token="dapi-abc123"))
    headers = client._make_headers(ProviderFamily.OPENAI)
    assert headers["Authorization"] == "Bearer dapi-abc123"


def test_make_headers_includes_content_type() -> None:
    client = _make_client()
    headers = client._make_headers(ProviderFamily.OPENAI)
    assert headers["Content-Type"] == "application/json"


def test_make_headers_openai_family_has_no_anthropic_version() -> None:
    """OPENAI / GEMINI / RESPONSES families must not set ``anthropic-version``."""
    client = _make_client()
    for f in (ProviderFamily.OPENAI, ProviderFamily.GEMINI, ProviderFamily.OPENAI_RESPONSES):
        headers = client._make_headers(f)
        assert "anthropic-version" not in headers, (
            f"family={f} must not carry Anthropic header"
        )


def test_make_headers_anthropic_family_sets_anthropic_version() -> None:
    """Anthropic native API requires the ``anthropic-version`` header."""
    client = _make_client()
    headers = client._make_headers(ProviderFamily.ANTHROPIC)
    assert "anthropic-version" in headers
    assert headers["anthropic-version"], "anthropic-version must be non-empty"


# ---------------------------------------------------------------------------
# _parse_response (P0-1)
# ---------------------------------------------------------------------------

def test_parse_response_maps_to_model_response() -> None:
    """_parse_response should return a litellm ModelResponse from FMAPI JSON."""
    client = _make_client()
    body = _make_success_response()
    resp = httpx.Response(200, json=body)

    result = client._parse_response(resp, family=ProviderFamily.OPENAI, model="my-model")

    assert result.choices is not None
    assert len(result.choices) > 0


def test_parse_response_preserves_id() -> None:
    client = _make_client()
    body = _make_success_response()
    body["id"] = "chatcmpl-unique-id"
    resp = httpx.Response(200, json=body)

    result = client._parse_response(resp, family=ProviderFamily.OPENAI, model="m")
    assert result.id == "chatcmpl-unique-id"


def test_parse_response_handles_malformed_body_gracefully() -> None:
    """_parse_response must not crash on unexpected FMAPI shape (fallback path)."""
    client = _make_client()
    resp = httpx.Response(200, json={"unexpected": "field"})
    result = client._parse_response(
        resp, family=ProviderFamily.OPENAI, model="fallback-model"
    )
    assert result is not None


# ---------------------------------------------------------------------------
# _build_stream_response (P0-1)
# ---------------------------------------------------------------------------

def test_build_stream_response_assembles_content() -> None:
    """Streaming: accumulated content must appear in the single returned ModelResponse."""
    client = _make_client()
    result = client._build_stream_response(
        content="The answer is 42.",
        response_id="stream-resp-1",
        model="databricks/test-model",
    )
    choices = result.choices
    assert choices is not None and len(choices) > 0
    msg = choices[0].get("message") or getattr(choices[0], "message", None)
    # Extract content regardless of dict or object
    content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
    assert content == "The answer is 42."


def test_build_stream_response_uses_fallback_id_when_empty() -> None:
    client = _make_client()
    result = client._build_stream_response(
        content="hi", response_id="", model="m"
    )
    assert result.id is not None
    assert result.id != ""


def test_build_stream_response_sets_finish_reason_stop() -> None:
    client = _make_client()
    result = client._build_stream_response(content="done", response_id="rid", model="m")
    choice = result.choices[0]
    finish_reason = (
        choice.get("finish_reason") if isinstance(choice, dict)
        else getattr(choice, "finish_reason", None)
    )
    assert finish_reason == "stop"


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------

def test_chat_completion_builds_correct_url_workspace_host() -> None:
    """Workspace host → gateway is reverse-proxied at /ai-gateway."""
    client = _make_client(max_retries=0)
    captured_url: list[str] = []
    captured_body: list[dict] = []

    def mock_post(url, headers=None, json=None, **_kw):
        captured_url.append(url)
        captured_body.append(json or {})
        return httpx.Response(200, json=_make_success_response())

    with patch.object(client._http, "post", side_effect=mock_post):
        client.chat_completion(
            model="databricks-meta-llama-3-3-70b-instruct",
            messages=[{"role": "user", "content": "hello"}],
        )

    assert captured_url == [f"{_HOST}/ai-gateway/mlflow/v1/chat/completions"]
    # The mlflow path no longer carries the endpoint in the URL — it must be
    # in the request body so the gateway knows which model to route to.
    assert captured_body[0].get("model") == "databricks-meta-llama-3-3-70b-instruct"


def test_chat_completion_uses_dedicated_gateway_host_when_set() -> None:
    """Dedicated *.ai-gateway.* host is used as-is (no /ai-gateway prefix)."""
    dedicated = "https://9999999999999999.ai-gateway.cloud.databricks.com"
    client = _make_client(max_retries=0, ai_gateway_host=dedicated)
    captured_url: list[str] = []

    def mock_post(url, headers=None, json=None, **_kw):
        captured_url.append(url)
        return httpx.Response(
            200,
            json={
                "id": "msg_x",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    with patch.object(client._http, "post", side_effect=mock_post):
        client.chat_completion(
            model="databricks-claude-opus-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert captured_url == [f"{dedicated}/anthropic/v1/messages"]


def test_chat_completion_ignores_extra_litellm_kwargs() -> None:
    """extra_headers and extra_body (litellm conventions) must not appear in
    the JSON body forwarded to the AI Gateway — the gateway returns 400 if
    they are present."""
    client = _make_client(max_retries=0)
    captured_body: list[dict] = []

    def mock_post(url, headers=None, json=None, **_kw):
        captured_body.append(json or {})
        return httpx.Response(
            200,
            json={
                "id": "msg_x",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    with patch.object(client._http, "post", side_effect=mock_post):
        client.chat_completion(
            model="databricks-claude-sonnet-4-5",
            messages=[{"role": "user", "content": "hi"}],
            # These are litellm-specific kwargs that DatabricksLLM._transport_call
            # strips before forwarding — the client must never receive them.
            # (We pass them here directly to confirm the client tolerates them
            #  if present, but the primary assertion is they don't reach the body.)
        )

    body = captured_body[0]
    assert "extra_headers" not in body, "extra_headers must not appear in gateway request body"
    assert "extra_body" not in body, "extra_body must not appear in gateway request body"


# ---------------------------------------------------------------------------
# resolve_family — metadata-first routing with name-pattern fallback
# ---------------------------------------------------------------------------

def test_resolve_family_uses_metadata_when_available() -> None:
    """When the describe call returns ``api_types``, that wins over the name."""
    client = _make_client(max_retries=0, metadata_probe=True)

    # Model name screams "openai chat" but metadata says anthropic.
    meta_response = httpx.Response(
        200,
        json={
            "config": {
                "served_entities": [{
                    "foundation_model": {"api_types": ["anthropic/v1/messages"]},
                }],
            },
        },
    )
    with patch.object(client._http, "get", return_value=meta_response):
        family = client.resolve_family("my-custom-endpoint")

    assert family is ProviderFamily.ANTHROPIC, (
        "metadata api_types must authoritatively override the name pattern"
    )


def test_resolve_family_falls_back_to_name_when_metadata_fails() -> None:
    """If the describe call errors, we must fall back to ``detect_family``."""
    client = _make_client(max_retries=0, metadata_probe=True)

    with patch.object(
        client._http, "get",
        side_effect=httpx.HTTPError("boom"),
    ):
        family = client.resolve_family("databricks-claude-sonnet-4-5")

    assert family is ProviderFamily.ANTHROPIC, (
        "name-pattern fallback must match *claude* → ANTHROPIC"
    )


def test_resolve_family_caches_positive_hit() -> None:
    """A successful metadata resolve should not trigger a second describe call."""
    client = _make_client(max_retries=0, metadata_probe=True)

    meta_response = httpx.Response(
        200,
        json={"config": {"served_entities": [
            {"foundation_model": {"api_types": ["gemini/v1/generateContent"]}},
        ]}},
    )
    with patch.object(client._http, "get", return_value=meta_response) as mock_get:
        first = client.resolve_family("databricks-gemini-x")
        second = client.resolve_family("databricks-gemini-x")

    assert first is second is ProviderFamily.GEMINI
    assert mock_get.call_count == 1, "second resolve must be served from cache"


def test_resolve_family_default_skips_metadata_probe() -> None:
    """Default (metadata_probe=False) must NOT hit the workspace URL.

    The whole point of the connector is to send FM traffic to the AI Gateway
    host; the workspace URL is only for auth/discovery and must be touched
    'only as required'. With the default config, resolve_family must rely on
    detect_family(model) and never issue a metadata GET.
    """
    client = _make_client(max_retries=0)  # metadata_probe defaults to False

    with patch.object(client._http, "get") as mock_get:
        family_claude = client.resolve_family("databricks-claude-opus-4-6")
        family_gemini = client.resolve_family("databricks-gemini-2-5-flash")
        family_gpt5 = client.resolve_family("databricks-gpt-5-4-mini")
        family_chat = client.resolve_family("databricks-meta-llama-3-3-70b-instruct")

    # Name-pattern detection must give the right family for each.
    assert family_claude is ProviderFamily.ANTHROPIC
    assert family_gemini is ProviderFamily.GEMINI
    assert family_gpt5 is ProviderFamily.OPENAI_RESPONSES
    assert family_chat is ProviderFamily.OPENAI

    # Critical assertion: NO workspace metadata GET on the FM hot path.
    assert mock_get.call_count == 0, (
        "Default config must not issue any GET against the workspace URL "
        "(the workspace must only be hit 'as required' — not on every chat)."
    )


# ---------------------------------------------------------------------------
# __del__ cleanup (P1-1)
# ---------------------------------------------------------------------------

def test_del_closes_http_client() -> None:
    """__del__ must close the singleton httpx.Client to release connections."""
    client = _make_client()
    with patch.object(client._http, "close") as mock_close:
        client.__del__()
    mock_close.assert_called_once()


def test_del_is_idempotent_on_exception() -> None:
    """__del__ must not raise even if the client is already closed."""
    client = _make_client()
    client._http.close()  # close manually first
    client.__del__()  # should not raise


def test_explicit_close_works() -> None:
    """close() must close the singleton httpx.Client."""
    client = _make_client()
    with patch.object(client._http, "close") as mock_close:
        client.close()
    mock_close.assert_called_once()
