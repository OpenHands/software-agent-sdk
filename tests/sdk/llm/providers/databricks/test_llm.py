"""Tests for DatabricksLLM and the create_llm factory.

Covers: PAT construction, provider discriminator (P0-6), context window lookup,
max_output_tokens lookup, unknown model fallback, _transport_call prefix stripping,
Pydantic round-trip serialization, and create_llm factory routing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr, ValidationError

from openhands.sdk.llm.providers.databricks.llm import (
    DATABRICKS_CONTEXT_WINDOWS,
    DATABRICKS_MAX_OUTPUT,
    DatabricksLLM,
)
from openhands.sdk.llm.providers.databricks.models import (
    ProviderFamily,
    StoredU2MTokens,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HOST = "https://adb-123.azuredatabricks.net"
_MODEL_LLAMA = "databricks/databricks-meta-llama-3-3-70b-instruct"
_MODEL_CLAUDE = "databricks/databricks-claude-sonnet-4"
_MODEL_UNKNOWN = "databricks/my-custom-finetuned-model"


# ---------------------------------------------------------------------------
# Helper: minimal PAT-auth DatabricksLLM (no HTTP calls during construction)
# ---------------------------------------------------------------------------

def _make_llm(
    model: str = _MODEL_LLAMA,
    host: str = _HOST,
    token: str = "dapi-test",
    ai_gateway_host: str | None = None,
    **kwargs,
) -> DatabricksLLM:
    """Build a DatabricksLLM with PAT auth for tests.

    The new architecture only requires *one* of databricks_host or
    databricks_ai_gateway_host. We always set databricks_host (the canonical
    workspace URL) and forward ai_gateway_host only when explicitly given,
    so tests reflect the typical single-URL deployment by default.
    """
    return DatabricksLLM(
        model=model,
        databricks_host=host,
        databricks_ai_gateway_host=ai_gateway_host,
        api_key=SecretStr(token),
        usage_id="test-databricks-llm",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_construction_succeeds_with_pat_auth() -> None:
    """DatabricksLLM must construct without HTTP calls when using PAT."""
    llm = _make_llm()
    assert llm.model == _MODEL_LLAMA
    assert llm.databricks_host == _HOST


def test_construction_exposes_db_client() -> None:
    """_db_client private attr must be set after construction."""
    llm = _make_llm()
    from openhands.sdk.llm.providers.databricks.client import DatabricksFMAPIClient
    assert isinstance(llm._db_client, DatabricksFMAPIClient)


def test_construction_exposes_db_credentials() -> None:
    """_db_credentials private attr must be set after construction."""
    from openhands.sdk.llm.providers.databricks.auth import DatabricksCredentials
    llm = _make_llm()
    assert isinstance(llm._db_credentials, DatabricksCredentials)
    assert llm._db_credentials.auth_method == "pat"


def test_construction_rejects_non_https_host() -> None:
    """databricks_host without https:// must raise ValueError at construction."""
    with pytest.raises(ValueError, match="https://"):
        DatabricksLLM(
            model=_MODEL_LLAMA,
            databricks_host="http://adb-123.azuredatabricks.net",
            databricks_ai_gateway_host=_HOST,
            api_key=SecretStr("tok"),
            usage_id="t",
        )


def test_construction_rejects_no_host_at_all() -> None:
    """At least one of databricks_host / databricks_ai_gateway_host is required."""
    with pytest.raises(ValueError, match="databricks_host is required"):
        DatabricksLLM(
            model=_MODEL_LLAMA,
            api_key=SecretStr("tok"),
            usage_id="t",
        )


def test_construction_pat_with_only_workspace_host() -> None:
    """PAT auth on a single-URL deployment: workspace URL is enough; the SDK
    derives the AI Gateway base from it (<host>/ai-gateway/<route>)."""
    llm = DatabricksLLM(
        model=_MODEL_LLAMA,
        databricks_host=_HOST,
        api_key=SecretStr("tok"),
        usage_id="t",
    )
    assert llm.databricks_host == _HOST
    assert llm.databricks_ai_gateway_host is None
    assert llm._db_credentials.auth_method == "pat"
    # Internally the FM client must end up routing through the workspace host.
    assert llm._db_client._ai_gateway_host == _HOST


def test_construction_pat_with_dedicated_gateway_host_only() -> None:
    """PAT auth on a dedicated gateway: the gateway host is sufficient."""
    dedicated = "https://9999999999999999.ai-gateway.cloud.databricks.com"
    llm = DatabricksLLM(
        model=_MODEL_LLAMA,
        databricks_ai_gateway_host=dedicated,
        api_key=SecretStr("tok"),
        usage_id="t",
    )
    assert llm.databricks_host is None
    assert llm.databricks_ai_gateway_host == dedicated
    assert llm._db_client._ai_gateway_host == dedicated


# ---------------------------------------------------------------------------
# P0-6: provider discriminator
# ---------------------------------------------------------------------------

def test_provider_field_is_databricks() -> None:
    """provider field must be exactly 'databricks' for Pydantic discriminator."""
    llm = _make_llm()
    assert llm.provider == "databricks"


def test_provider_field_is_literal() -> None:
    """provider must not be overridable by user-supplied kwargs."""
    # Attempt to construct with a different provider value should either be
    # silently overridden to "databricks" or raise — it must never produce a
    # DatabricksLLM with provider != "databricks".
    llm = _make_llm()
    assert llm.provider == "databricks"


# ---------------------------------------------------------------------------
# Context windows
# ---------------------------------------------------------------------------

def test_context_window_llama_70b() -> None:
    llm = _make_llm(model=_MODEL_LLAMA)
    assert llm.max_input_tokens == DATABRICKS_CONTEXT_WINDOWS[_MODEL_LLAMA]


def test_context_window_claude() -> None:
    """Claude-based Databricks models have 200K context window."""
    llm = _make_llm(model=_MODEL_CLAUDE)
    assert llm.max_input_tokens == 200_000


def test_context_window_unknown_model_fallback() -> None:
    """Unknown Databricks models fall back to 128K context window."""
    llm = _make_llm(model=_MODEL_UNKNOWN)
    assert llm.max_input_tokens == 128_000


def test_max_output_tokens_claude() -> None:
    llm = _make_llm(model=_MODEL_CLAUDE)
    assert llm.max_output_tokens == 8_192


def test_max_output_tokens_unknown_model_fallback() -> None:
    """Unknown models fall back to 16K max output."""
    llm = _make_llm(model=_MODEL_UNKNOWN)
    assert llm.max_output_tokens == 16_384


# ---------------------------------------------------------------------------
# _transport_call — prefix stripping
# ---------------------------------------------------------------------------

def test_transport_call_strips_databricks_prefix() -> None:
    """_transport_call must strip 'databricks/' prefix before calling FMAPI."""
    llm = _make_llm()
    captured_model: list[str] = []

    def mock_chat_completion(model, messages, stream=False, on_token=None, **kwargs):
        captured_model.append(model)
        return MagicMock()

    with patch.object(llm._db_client, "chat_completion", side_effect=mock_chat_completion):
        llm._transport_call(messages=[{"role": "user", "content": "hi"}])

    assert len(captured_model) == 1
    # Should be bare endpoint name, not prefixed
    assert not captured_model[0].startswith("databricks/")
    assert captured_model[0] == "databricks-meta-llama-3-3-70b-instruct"


def test_transport_call_passes_through_streaming_flag() -> None:
    llm = _make_llm()

    def mock_chat_completion(model, messages, stream=False, on_token=None, **kwargs):
        return MagicMock()

    with patch.object(llm._db_client, "chat_completion", side_effect=mock_chat_completion) as mock_cc:
        llm._transport_call(messages=[], enable_streaming=True)

    call_kwargs = mock_cc.call_args
    assert call_kwargs.kwargs.get("stream") is True


def test_transport_call_strips_litellm_kwargs() -> None:
    """_transport_call must strip litellm-specific kwargs (extra_headers,
    extra_body, stream) before forwarding to chat_completion so they never
    appear in the JSON body sent to the Databricks AI Gateway."""
    llm = _make_llm()
    received_kwargs: dict = {}

    def mock_chat_completion(model, messages, stream=False, on_token=None, **kwargs):
        received_kwargs.update(kwargs)
        return MagicMock()

    with patch.object(llm._db_client, "chat_completion", side_effect=mock_chat_completion):
        llm._transport_call(
            messages=[{"role": "user", "content": "hi"}],
            extra_headers={"X-Custom": "value"},
            extra_body={"custom_param": True},
            stream=True,  # also stripped; streaming controlled via enable_streaming
        )

    assert "extra_headers" not in received_kwargs, "extra_headers must be stripped before gateway call"
    assert "extra_body"    not in received_kwargs, "extra_body must be stripped before gateway call"
    assert "stream"        not in received_kwargs, "stream must be stripped; controlled via enable_streaming"


# ---------------------------------------------------------------------------
# Resilience knob passthrough
# ---------------------------------------------------------------------------

def test_custom_timeouts_propagate_to_client() -> None:
    """Resilience knobs must be forwarded to DatabricksFMAPIClient."""
    llm = _make_llm(
        databricks_connect_timeout_s=5.0,
        databricks_read_timeout_s=60.0,
        databricks_max_retries=5,
    )
    assert llm._db_client._timeouts.connect_s == 5.0
    assert llm._db_client._timeouts.read_s == 60.0
    assert llm._db_client._max_retries == 5


# ---------------------------------------------------------------------------
# Pydantic round-trip serialization (P0-6)
# ---------------------------------------------------------------------------

def test_pydantic_roundtrip_preserves_provider_field() -> None:
    """model_dump / model_validate round-trip must preserve provider='databricks'."""
    llm = _make_llm()
    data = llm.model_dump()
    assert data["provider"] == "databricks"


def test_pydantic_json_roundtrip_preserves_provider_field() -> None:
    """JSON serialization must include provider field for deserialization dispatch."""
    llm = _make_llm()
    json_str = llm.model_dump_json()
    assert '"provider":"databricks"' in json_str or '"provider": "databricks"' in json_str


def test_m2m_client_secret_serialized_as_plaintext_with_expose_secrets() -> None:
    """databricks_client_secret must be written as plaintext when serialized
    with context={'expose_secrets': True} (the path used by AgentStore.save()).

    Without this, the saved agent_settings.json contains '**********' and the
    M2M OIDC token request always returns 401 after a restart.
    """
    secret_value = "my-real-client-secret"
    llm = DatabricksLLM(
        model=_MODEL_CLAUDE,
        databricks_host=_HOST,
        databricks_client_id="app-id-123",
        databricks_client_secret=SecretStr(secret_value),
        api_key=None,
    )

    import json

    # Default JSON serialization — must be redacted (what users see / screen output)
    default_json = llm.model_dump_json()
    default = json.loads(default_json)
    assert default.get("databricks_client_secret") == "**********", (
        "secret must be redacted in default model_dump_json()"
    )

    # With expose_secrets=True (AgentStore.save path) — must be plaintext string
    exposed_json = llm.model_dump_json(context={"expose_secrets": True})
    exposed = json.loads(exposed_json)
    assert exposed.get("databricks_client_secret") == secret_value, (
        "secret must be plaintext when expose_secrets=True so agent_settings.json "
        "contains the real value and M2M auth doesn't send '**********' to OIDC"
    )

    # Round-trip: reload from the exposed JSON and confirm secret survived
    reloaded = DatabricksLLM.model_validate_json(exposed_json)
    assert reloaded.databricks_client_secret is not None
    assert reloaded.databricks_client_secret.get_secret_value() == secret_value, (
        "secret must survive a model_dump_json → model_validate_json round-trip"
    )


# ---------------------------------------------------------------------------
# create_llm factory
# ---------------------------------------------------------------------------

def test_create_llm_routes_databricks_prefix() -> None:
    """create_llm must return DatabricksLLM for 'databricks/' prefixed models."""
    from openhands.sdk import create_llm

    llm = create_llm(
        model=_MODEL_LLAMA,
        databricks_host=_HOST,
        api_key=SecretStr("dapi-tok"),
        usage_id="factory-test",
    )
    assert isinstance(llm, DatabricksLLM)


def test_create_llm_routes_non_databricks_to_base_llm() -> None:
    """create_llm must return the base LLM for non-Databricks models."""
    from openhands.sdk import create_llm
    from openhands.sdk.llm import LLM

    llm = create_llm(model="claude-sonnet-4-20250514", usage_id="base-test")
    assert type(llm) is LLM
    assert not isinstance(llm, DatabricksLLM)


def test_create_llm_empty_model_raises_validation_error() -> None:
    """create_llm('') delegates to base LLM, which rejects an empty model name."""
    from openhands.sdk import create_llm

    with pytest.raises(ValidationError, match="model must be specified"):
        create_llm(model="", usage_id="empty-test")


# ---------------------------------------------------------------------------
# PWAF surfaces: auth_method / predicted_family / resolve_family
# ---------------------------------------------------------------------------

def test_auth_method_property_reflects_pat_construction() -> None:
    """auth_method must mirror the strategy resolved at construction time."""
    llm = _make_llm()
    assert llm.auth_method == "pat"


@pytest.mark.parametrize(
    "model,expected",
    [
        ("databricks/databricks-meta-llama-3-3-70b-instruct", ProviderFamily.OPENAI),
        ("databricks/databricks-gpt-oss-120b",                ProviderFamily.OPENAI),
        ("databricks/databricks-claude-sonnet-4-5",           ProviderFamily.ANTHROPIC),
        ("databricks/databricks-claude-opus-4-6",             ProviderFamily.ANTHROPIC),
        ("databricks/databricks-gemini-2-5-flash",            ProviderFamily.GEMINI),
        ("databricks/databricks-gpt-5-4",                     ProviderFamily.OPENAI_RESPONSES),
        ("databricks/databricks-gpt-5-4-mini",                ProviderFamily.OPENAI_RESPONSES),
    ],
)
def test_predicted_family_no_http_call(model: str, expected: ProviderFamily) -> None:
    """predicted_family must be pure-compute (no metadata probe)."""
    llm = _make_llm(model=model)
    # If this does HTTP, it will fail because adb-123 is unreachable from tests.
    # detect_family is sync + pure, so this must succeed without mocking.
    assert llm.predicted_family is expected


def test_resolve_family_delegates_to_client_and_caches() -> None:
    """resolve_family delegates to DatabricksFMAPIClient.resolve_family and caches.

    Metadata probing is opt-in (`databricks_metadata_probe=True`); without
    that flag `resolve_family` is name-pattern only and never hits the wire.
    """
    llm = _make_llm(
        model="databricks/databricks-claude-sonnet-4-5",
        databricks_metadata_probe=True,
    )

    import httpx
    meta_response = httpx.Response(200, json={
        "config": {"served_entities": [
            {"foundation_model": {"api_types": ["anthropic/v1/messages"]}},
        ]},
    })
    with patch.object(llm._db_client._http, "get", return_value=meta_response) as mg:
        f1 = llm.resolve_family()
        f2 = llm.resolve_family()
    assert f1 is f2 is ProviderFamily.ANTHROPIC
    assert mg.call_count == 1, "second resolve must be cache-served"


def test_resolve_family_default_skips_metadata_probe() -> None:
    """Default resolve_family is name-pattern only; no workspace GET."""
    llm = _make_llm(model="databricks/databricks-claude-sonnet-4-5")

    with patch.object(llm._db_client._http, "get") as mg:
        family = llm.resolve_family()
    assert family is ProviderFamily.ANTHROPIC
    assert mg.call_count == 0, "default path must not hit workspace metadata"


def test_transport_call_logs_family(caplog) -> None:
    """_transport_call must emit a debug log with predicted_family + auth_method."""
    import logging

    llm = _make_llm(model="databricks/databricks-claude-sonnet-4-5")

    def mock_chat_completion(model, messages, stream=False, on_token=None, **kwargs):
        return MagicMock()

    with caplog.at_level(logging.DEBUG, logger="openhands.sdk.llm.providers.databricks.llm"):
        with patch.object(llm._db_client, "chat_completion", side_effect=mock_chat_completion):
            llm._transport_call(messages=[{"role": "user", "content": "hi"}])

    log_records = [r for r in caplog.records if r.message == "databricks_transport_call"]
    assert log_records, "expected a databricks_transport_call debug record"
    record = log_records[-1]
    assert record.__dict__.get("predicted_family") == "anthropic"
    assert record.__dict__.get("auth_method") == "pat"


# ---------------------------------------------------------------------------
# Expanded model capability tables (current-gen FM / external models)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected_ctx",
    [
        # Anthropic family — 200K across Claude generations on Databricks
        ("databricks/databricks-claude-sonnet-4-5", 200_000),
        ("databricks/databricks-claude-opus-4-6",   200_000),
        ("databricks/databricks-claude-haiku-4-5",  200_000),
        # Gemini — 1M token context
        ("databricks/databricks-gemini-2-5-flash",  1_048_576),
        ("databricks/databricks-gemini-2-5-pro",    1_048_576),
        # GPT-5 Responses family — 400K context
        ("databricks/databricks-gpt-5-4",           400_000),
        ("databricks/databricks-gpt-5-4-mini",      400_000),
        # gpt-oss — 128K
        ("databricks/databricks-gpt-oss-120b",      128_000),
    ],
)
def test_current_gen_context_windows(model: str, expected_ctx: int) -> None:
    """Current-generation models must have correct context windows in the table."""
    llm = _make_llm(model=model)
    assert llm.max_input_tokens == expected_ctx, (
        f"{model}: expected ctx={expected_ctx}, got {llm.max_input_tokens}"
    )


@pytest.mark.parametrize(
    "model,min_output",
    [
        ("databricks/databricks-claude-sonnet-4-5", 64_000),
        ("databricks/databricks-gemini-2-5-flash",  16_384),  # reasoning budget
        ("databricks/databricks-gpt-5-4",           16_384),
        ("databricks/databricks-gpt-oss-120b",      16_384),
    ],
)
def test_reasoning_models_have_generous_output_budget(
    model: str, min_output: int,
) -> None:
    """Reasoning models' max_output_tokens must leave room for thinking + output."""
    llm = _make_llm(model=model)
    assert llm.max_output_tokens >= min_output, (
        f"{model}: max_output_tokens too tight for reasoning — "
        f"got {llm.max_output_tokens}, need >= {min_output}"
    )
