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

from openhands.sdk.llm.utils.model_info import (
    _get_model_info_from_litellm_proxy,
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
    # Both functions are lru_cache'd; clear between tests so cache_key reuse
    # across tests does not mask behavior.
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
