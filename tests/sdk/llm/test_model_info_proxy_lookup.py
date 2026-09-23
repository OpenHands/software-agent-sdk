"""Tests for the LiteLLM proxy /v1/model/info lookup.

Focused on the matcher that picks the right entry out of the proxy's
response. The proxy accepts requests addressed by either the public alias
(`model_name`) or the underlying provider id (`litellm_params.model`), and
the SDK's model_info lookup must do the same — otherwise `model_info`
overrides set on the proxy (e.g. `supports_vision: true` for models LiteLLM
does not yet know upstream) silently fail to reach clients.

See issue: LiteLLM proxy model_info lookup misses when proxy uses short
aliases (claude-opus-4-8 vision still off).
"""

from unittest.mock import patch

import litellm
from litellm import model_cost

from openhands.sdk.llm.utils.model_info import (
    _get_model_info_from_litellm_proxy,
    _merge_raw_model_metadata,
    _register_proxy_alias_pricing,
    get_litellm_model_info,
)


_PROXY_RESPONSE = {
    "data": [
        # Aliased entry: short public name, provider-prefixed underlying id,
        # plus a model_info override (the case that motivated this fix).
        {
            "model_name": "claude-opus-4-8",
            "litellm_params": {"model": "anthropic/claude-opus-4-8"},
            "model_info": {"supports_vision": True},
        },
        # Plain entry: alias matches provider id verbatim.
        {
            "model_name": "openrouter/some-model",
            "litellm_params": {"model": "openrouter/some-model"},
            "model_info": {"supports_vision": False},
        },
    ]
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _patched_httpx_get(*_a, **_kw):
    return _FakeResponse(_PROXY_RESPONSE)


def setup_function(_):
    # `_get_model_info_from_litellm_proxy` is lru_cache'd; clear between tests
    # so cache_key reuse across tests does not mask behavior.
    _get_model_info_from_litellm_proxy.cache_clear()


def test_lookup_matches_by_model_name_alias():
    """Existing behavior: address by the proxy's public alias."""
    with patch("openhands.sdk.llm.utils.model_info.httpx.get", _patched_httpx_get):
        info = _get_model_info_from_litellm_proxy(
            secret_api_key="k",
            base_url="https://proxy.example",
            model="litellm_proxy/claude-opus-4-8",
            cache_key=1,
        )
    assert info == {"supports_vision": True}


def test_lookup_matches_by_litellm_params_model():
    """New behavior: address by the underlying provider id (`anthropic/...`).

    This is the case that broke `claude-opus-4-8` vision detection: the
    proxy exposes the model as the alias `claude-opus-4-8` but the SDK is
    configured with `litellm_proxy/anthropic/claude-opus-4-8`, so the
    pre-fix matcher (which only looked at `model_name`) missed.
    """
    with patch("openhands.sdk.llm.utils.model_info.httpx.get", _patched_httpx_get):
        info = _get_model_info_from_litellm_proxy(
            secret_api_key="k",
            base_url="https://proxy.example",
            model="litellm_proxy/anthropic/claude-opus-4-8",
            cache_key=2,
        )
    assert info == {"supports_vision": True}


def test_lookup_returns_none_for_unknown_model():
    with patch("openhands.sdk.llm.utils.model_info.httpx.get", _patched_httpx_get):
        info = _get_model_info_from_litellm_proxy(
            secret_api_key="k",
            base_url="https://proxy.example",
            model="litellm_proxy/anthropic/not-a-real-model",
            cache_key=3,
        )
    assert info is None


def test_get_litellm_model_info_uses_proxy_match_for_provider_prefixed_id():
    """End-to-end: `get_litellm_model_info` returns the proxy override when
    the SDK is configured with the provider-prefixed id even though the
    proxy advertises a shorter alias."""
    with patch("openhands.sdk.llm.utils.model_info.httpx.get", _patched_httpx_get):
        info = get_litellm_model_info(
            secret_api_key="k",
            base_url="https://proxy.example",
            model="litellm_proxy/anthropic/claude-opus-4-8",
        )
    assert info is not None
    assert info.get("supports_vision") is True


def test_get_litellm_model_info_uses_proxy_for_openhands_provider_model():
    with patch("openhands.sdk.llm.utils.model_info.httpx.get", _patched_httpx_get):
        info = get_litellm_model_info(
            secret_api_key="k",
            base_url=None,
            model="openhands/claude-opus-4-8",
        )
    assert info is not None
    assert info.get("supports_vision") is True


def test_raw_registry_capabilities_survive_typed_model_info_projection():
    raw = {
        "future-model": {
            "supports_adaptive_thinking": True,
            "supports_sampling_params": False,
        }
    }
    with patch.dict("openhands.sdk.llm.utils.model_info.model_cost", raw, clear=True):
        info = _merge_raw_model_metadata(
            {"key": "future-model", "supports_reasoning": True}
        )

    assert info["supports_reasoning"] is True
    assert info["supports_adaptive_thinking"] is True
    assert info["supports_sampling_params"] is False


# --- alias pricing registration (issue #4816) ---

# Each test uses a distinct custom alias so litellm's internal caches (which
# remember a previously-registered alias as priceable even after it is popped
# out of `model_cost`) cannot pollute a later test's priceability check.
_UNDERLYING = "claude-sonnet-4-5-20250929"


def _pop(alias):
    model_cost.pop(alias, None)


def test_register_proxy_alias_makes_alias_priceable():
    """Registering a custom alias flips cost_per_token from raising to a value."""
    alias = "prod/claude-sonnet-4-5-bedrock-a"
    _pop(alias)
    # Sanity: the alias is not priceable before registration.
    try:
        litellm.cost_per_token(model=alias, prompt_tokens=1, completion_tokens=1)
        pre_raises = False
    except Exception:
        pre_raises = True
    assert pre_raises

    underlying = model_cost[_UNDERLYING]
    _register_proxy_alias_pricing(
        alias=alias,
        underlying_model_info=underlying,
        proxy_model_info=None,
    )
    cost = (0.0, 0.0)
    try:
        cost = litellm.cost_per_token(
            model=alias, prompt_tokens=100, completion_tokens=50
        )
        post_raises = False
    except Exception:
        post_raises = True

    assert not post_raises
    # (prompt_cost, completion_cost); both must be non-zero.
    assert cost[0] > 0
    assert cost[1] > 0
    _pop(alias)


def test_register_proxy_alias_skips_already_priceable_models():
    """A provider-prefixed real model is priceable natively; do not clobber."""
    alias = "anthropic/claude-sonnet-4-5-20250929"
    before = dict(model_cost.get(alias, {}))
    _register_proxy_alias_pricing(
        alias=alias,
        underlying_model_info=model_cost[_UNDERLYING],
        proxy_model_info={"input_cost_per_token": 999},
    )
    # Entry unchanged: registration was skipped.
    assert model_cost.get(alias, {}) == before


def test_register_proxy_proxy_overrides_win():
    """Proxy-side pricing overrides take precedence over the underlying model."""
    alias = "prod/claude-sonnet-4-5-bedrock-b"
    _pop(alias)
    underlying = model_cost[_UNDERLYING]
    override = {"input_cost_per_token": 1.0, "output_cost_per_token": 2.0}
    _register_proxy_alias_pricing(
        alias=alias,
        underlying_model_info=underlying,
        proxy_model_info=override,
    )
    entry = model_cost[alias]
    assert entry["input_cost_per_token"] == 1.0
    assert entry["output_cost_per_token"] == 2.0
    # Underlying cache/max/provider fields still carried over.
    assert entry["litellm_provider"] == underlying["litellm_provider"]
    _pop(alias)


def test_register_proxy_alias_bedrock_converse_provider_normalized():
    """A bedrock_converse provider label must be normalized to bedrock.

    ``get_llm_provider`` resolves ``bedrock_converse`` only for model ids already
    present in litellm's builtin bedrock_converse set; an unknown alias id with that
    label raises, leaving the span priced $0. Registering with ``bedrock`` makes
    ``cost_per_token`` resolve without an explicit ``custom_llm_provider`` (#4836).
    """
    alias = "prod/claude-sonnet-4-5-bedrock-e"
    _pop(alias)
    # Sanity: the alias is not priceable before registration.

    try:
        litellm.cost_per_token(model=alias, prompt_tokens=1, completion_tokens=1)
        pre_raises = False
    except Exception:
        pre_raises = True
    assert pre_raises

    # The proxy advertises ``bedrock_converse`` (the Anthropic-on-Bedrock route),
    # which would otherwise override the underlying provider in the merged entry.

    underlying = model_cost["us.anthropic.claude-sonnet-4-5-20250929-v1:0"]
    assert underlying["litellm_provider"] == "bedrock_converse"
    _register_proxy_alias_pricing(
        alias=alias,
        underlying_model_info=underlying,
        proxy_model_info={"litellm_provider": "bedrock_converse"},
    )
    assert model_cost[alias]["litellm_provider"] == "bedrock"
    cost = (0.0, 0.0)
    try:
        cost = litellm.cost_per_token(
            model=alias, prompt_tokens=100, completion_tokens=50
        )
        post_raises = False
    except Exception:
        post_raises = True
    assert not post_raises
    assert cost[0] > 0
    assert cost[1] > 0
    _pop(alias)


def test_register_proxy_alias_no_pricing_logs_and_skips():
    """When no pricing can be derived, the alias stays unregistered (no $0 entry)."""
    alias = "prod/claude-sonnet-4-5-bedrock-c"
    _pop(alias)
    _register_proxy_alias_pricing(
        alias=alias,
        underlying_model_info=None,
        proxy_model_info={"supports_vision": True},
    )
    assert alias not in model_cost


def test_get_model_info_from_proxy_registers_alias_pricing():
    """End-to-end: fetching proxy model info registers the alias into model_cost."""
    alias = "prod/claude-sonnet-4-5-bedrock-d"
    _pop(alias)
    proxy_response = {
        "data": [
            {
                "model_name": alias,
                "litellm_params": {"model": _UNDERLYING},
                "model_info": {"supports_vision": True},
            }
        ]
    }
    with patch(
        "openhands.sdk.llm.utils.model_info.httpx.get",
        lambda *_a, **_kw: _FakeResponse(proxy_response),
    ):
        _get_model_info_from_litellm_proxy.cache_clear()
        _get_model_info_from_litellm_proxy(
            secret_api_key="k",
            base_url="https://proxy.example",
            model=f"litellm_proxy/{alias}",
            cache_key=42,
        )
    assert alias in model_cost
    try:
        cost = litellm.cost_per_token(
            model=alias, prompt_tokens=100, completion_tokens=50
        )
    except Exception:
        cost = None
    assert cost is not None and cost[0] > 0
    _pop(alias)
