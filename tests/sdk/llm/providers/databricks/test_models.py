"""Tests for Databricks AI Gateway routing primitives.

Covers:
  * ``ProviderFamily`` enum shape
  * ``detect_family`` — name-pattern routing (fast path, no HTTP)
  * ``pick_family_from_api_types`` — metadata routing (authoritative)
  * ``AIGatewayPaths.url`` — URL construction per family

These primitives are the hot path of the whole connector; they must never
regress. The live E2E tests already exercise them end-to-end; this file locks
in the contract at the unit level so refactors can't silently flip routing.
"""

from __future__ import annotations

import pytest

from openhands.sdk.llm.providers.databricks.models import (
    AIGatewayPaths,
    ProviderFamily,
    detect_family,
    pick_family_from_api_types,
)


# ---------------------------------------------------------------------------
# ProviderFamily enum shape
# ---------------------------------------------------------------------------

def test_provider_family_enum_values() -> None:
    """Exactly four families must be exposed (OpenAI Chat / Responses, Anthropic, Gemini)."""
    assert {f.value for f in ProviderFamily} == {
        "openai", "openai_responses", "anthropic", "gemini",
    }


def test_provider_family_openai_is_default_fallback() -> None:
    """``OPENAI`` is the universal fallback — must be present and import-stable."""
    assert ProviderFamily.OPENAI.value == "openai"


# ---------------------------------------------------------------------------
# detect_family — name-pattern routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        # Anthropic — substring match
        ("databricks-claude-sonnet-4-5",        ProviderFamily.ANTHROPIC),
        ("databricks/databricks-claude-opus-4", ProviderFamily.ANTHROPIC),
        ("claude-haiku",                         ProviderFamily.ANTHROPIC),
        ("my-custom-claude-proxy",              ProviderFamily.ANTHROPIC),

        # Gemini — substring match
        ("databricks-gemini-2-5-flash",         ProviderFamily.GEMINI),
        ("databricks/databricks-gemini-pro",    ProviderFamily.GEMINI),
        ("gemini-1-5-pro",                       ProviderFamily.GEMINI),

        # GPT-5 series → Responses API (bare and prefixed, all variants)
        ("gpt-5",                                    ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-mini",                               ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-nano",                               ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-1",                                  ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-1-codex-max",                        ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-1-codex-mini",                       ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-2-codex",                            ProviderFamily.OPENAI_RESPONSES),
        ("gpt-5-3-codex",                            ProviderFamily.OPENAI_RESPONSES),
        ("databricks-gpt-5-4",                       ProviderFamily.OPENAI_RESPONSES),
        ("databricks/databricks-gpt-5-4-mini",       ProviderFamily.OPENAI_RESPONSES),
        # Future numbered GPT generations inherit Responses API automatically
        ("gpt-6",                                    ProviderFamily.OPENAI_RESPONSES),
        ("gpt-6-mini",                               ProviderFamily.OPENAI_RESPONSES),
        ("databricks/databricks-gpt-7-turbo",        ProviderFamily.OPENAI_RESPONSES),

        # gpt-oss and everything else stays on MLflow Chat Completions
        ("gpt-oss-120b",                             ProviderFamily.OPENAI),
        ("databricks-gpt-oss-120b",                  ProviderFamily.OPENAI),
        ("databricks-meta-llama-3-3-70b-instruct",   ProviderFamily.OPENAI),

        # Case-insensitive
        ("DATABRICKS-CLAUDE-SONNET-4-5",         ProviderFamily.ANTHROPIC),
        ("Databricks/Gemini-Flash",              ProviderFamily.GEMINI),
    ],
)
def test_detect_family_name_patterns(model: str, expected: ProviderFamily) -> None:
    assert detect_family(model) == expected, (
        f"routing regression: detect_family({model!r}) != {expected}"
    )


def test_detect_family_gpt_oss_must_not_route_to_responses() -> None:
    """Regression guard: gpt-oss-* has no Responses API — keep on MLflow Chat.

    The ``re.match(r"gpt-\\d", name)`` rule excludes ``gpt-oss-*`` by
    construction: ``gpt-oss`` starts with ``gpt-o``, not ``gpt-<digit>``.
    This test pins that boundary so a future regex change doesn't silently
    break gpt-oss routing.
    """
    assert detect_family("gpt-oss-120b") is ProviderFamily.OPENAI
    assert detect_family("databricks-gpt-oss-20b") is ProviderFamily.OPENAI
    assert detect_family("databricks/databricks-gpt-oss-120b") is ProviderFamily.OPENAI


# ---------------------------------------------------------------------------
# pick_family_from_api_types — metadata routing
# ---------------------------------------------------------------------------

def test_pick_family_prefers_anthropic_over_openai_chat() -> None:
    """If an endpoint exposes both ``anthropic/v1/messages`` and the mlflow chat
    shim, we must pick the native Anthropic route."""
    family = pick_family_from_api_types(
        ["anthropic/v1/messages", "mlflow/v1/chat/completions"],
    )
    assert family is ProviderFamily.ANTHROPIC


def test_pick_family_prefers_gemini_over_openai_chat() -> None:
    family = pick_family_from_api_types(
        ["gemini/v1/generateContent", "mlflow/v1/chat/completions"],
    )
    assert family is ProviderFamily.GEMINI


def test_pick_family_prefers_responses_over_openai_chat() -> None:
    family = pick_family_from_api_types(
        ["openai/v1/responses", "mlflow/v1/chat/completions"],
    )
    assert family is ProviderFamily.OPENAI_RESPONSES


def test_pick_family_priority_order_anthropic_wins() -> None:
    """When multiple specific api_types are present, priority order
    decides — Anthropic wins over Gemini wins over Responses (documented)."""
    family = pick_family_from_api_types(
        ["gemini/v1/generateContent",
         "anthropic/v1/messages",
         "openai/v1/responses"],
    )
    assert family is ProviderFamily.ANTHROPIC


def test_pick_family_defaults_to_openai_when_no_native_hint() -> None:
    """mlflow-only api_types → fall back to universal OpenAI Chat."""
    family = pick_family_from_api_types(["mlflow/v1/chat/completions"])
    assert family is ProviderFamily.OPENAI


def test_pick_family_empty_or_none_returns_openai() -> None:
    """Empty api_types + no external provider → OPENAI (universal default)."""
    assert pick_family_from_api_types(None) is ProviderFamily.OPENAI
    assert pick_family_from_api_types([]) is ProviderFamily.OPENAI
    assert pick_family_from_api_types([], external_provider=None) is ProviderFamily.OPENAI


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("anthropic",         ProviderFamily.ANTHROPIC),
        ("ANTHROPIC",         ProviderFamily.ANTHROPIC),
        ("bedrock-anthropic", ProviderFamily.ANTHROPIC),
        ("google",            ProviderFamily.GEMINI),
        ("gemini",            ProviderFamily.GEMINI),
        ("openai",            ProviderFamily.OPENAI),
        ("azure-openai",      ProviderFamily.OPENAI),
        # Unknown providers → safe default
        ("cohere",            ProviderFamily.OPENAI),
        ("",                  ProviderFamily.OPENAI),
    ],
)
def test_pick_family_external_provider_routing(
    provider: str, expected: ProviderFamily,
) -> None:
    """External-model endpoints route via ``external_model.provider``."""
    family = pick_family_from_api_types([], external_provider=provider or None)
    assert family == expected, (
        f"external provider {provider!r} should route to {expected}"
    )


def test_pick_family_native_api_type_wins_over_external_provider() -> None:
    """When both signals are present, the native ``api_types`` must win."""
    family = pick_family_from_api_types(
        ["anthropic/v1/messages"],
        external_provider="openai",  # contradictory, should be ignored
    )
    assert family is ProviderFamily.ANTHROPIC


# ---------------------------------------------------------------------------
# AIGatewayPaths.url — URL construction per family
# ---------------------------------------------------------------------------

_HOST = "https://adb-123.azuredatabricks.net"


_DEDICATED_GW = "https://9999999999999999.ai-gateway.cloud.databricks.com"


def test_aigateway_url_openai_chat_workspace_host() -> None:
    """Workspace host: gateway is reverse-proxied at /ai-gateway."""
    url = AIGatewayPaths().url(_HOST, ProviderFamily.OPENAI, "databricks-llama-3-3")
    assert url == f"{_HOST}/ai-gateway/mlflow/v1/chat/completions"
    # Endpoint name is carried in the body, not the URL.
    assert "llama" not in url


def test_aigateway_url_openai_chat_dedicated_gateway_host() -> None:
    """Dedicated *.ai-gateway.* host is the gateway base; no /ai-gateway prefix."""
    url = AIGatewayPaths().url(
        _DEDICATED_GW, ProviderFamily.OPENAI, "databricks-llama-3-3",
    )
    assert url == f"{_DEDICATED_GW}/mlflow/v1/chat/completions"


def test_aigateway_url_anthropic_workspace_host() -> None:
    """Anthropic native route — endpoint-agnostic, name goes in the body."""
    url = AIGatewayPaths().url(
        _HOST, ProviderFamily.ANTHROPIC, "databricks-claude-sonnet-4-5",
    )
    assert url == f"{_HOST}/ai-gateway/anthropic/v1/messages"
    assert "claude" not in url


def test_aigateway_url_anthropic_dedicated_gateway_host() -> None:
    url = AIGatewayPaths().url(
        _DEDICATED_GW, ProviderFamily.ANTHROPIC, "databricks-claude-opus-4-6",
    )
    assert url == f"{_DEDICATED_GW}/anthropic/v1/messages"


def test_aigateway_url_gemini_interpolates_endpoint() -> None:
    url = AIGatewayPaths().url(
        _HOST, ProviderFamily.GEMINI, "databricks-gemini-2-5-flash",
    )
    assert url == (
        f"{_HOST}/ai-gateway/gemini/v1beta/models/"
        f"databricks-gemini-2-5-flash:generateContent"
    )


def test_aigateway_url_openai_responses_workspace_host() -> None:
    url = AIGatewayPaths().url(
        _HOST, ProviderFamily.OPENAI_RESPONSES, "databricks-gpt-5-4",
    )
    assert url == f"{_HOST}/ai-gateway/openai/v1/responses"
    assert "gpt-5" not in url


def test_aigateway_url_trailing_slash_is_normalized() -> None:
    """Trailing slashes on host must not produce double-slash URLs."""
    url = AIGatewayPaths().url(_HOST + "/", ProviderFamily.OPENAI, "foo")
    assert "//ai-gateway" not in url
    assert url == f"{_HOST}/ai-gateway/mlflow/v1/chat/completions"


def test_aigateway_url_explicit_ai_gateway_path_is_idempotent() -> None:
    """If the user already added '/ai-gateway' to the host, don't double it."""
    host_with_prefix = f"{_HOST}/ai-gateway"
    url = AIGatewayPaths().url(host_with_prefix, ProviderFamily.OPENAI, "x")
    assert url == f"{_HOST}/ai-gateway/mlflow/v1/chat/completions"


def test_aigateway_paths_overrideable() -> None:
    """Custom path templates (e.g. private deployment) round-trip."""
    paths = AIGatewayPaths(openai="/custom/{endpoint}/chat")
    url = paths.url(_HOST, ProviderFamily.OPENAI, "x")
    assert url == f"{_HOST}/ai-gateway/custom/x/chat"


def test_normalize_base_dedicated_gateway() -> None:
    """*.ai-gateway.* hosts are returned as-is."""
    assert AIGatewayPaths.normalize_base(_DEDICATED_GW) == _DEDICATED_GW
    assert AIGatewayPaths.normalize_base(_DEDICATED_GW + "/") == _DEDICATED_GW


def test_normalize_base_workspace_appends_prefix() -> None:
    """Workspace URLs gain the /ai-gateway prefix."""
    assert (
        AIGatewayPaths.normalize_base(_HOST) == f"{_HOST}/ai-gateway"
    )
    assert (
        AIGatewayPaths.normalize_base(f"{_HOST}/ai-gateway") == f"{_HOST}/ai-gateway"
    )
