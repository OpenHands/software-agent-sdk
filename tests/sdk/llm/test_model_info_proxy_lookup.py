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

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

import litellm
import pytest
from litellm import model_cost
from pydantic import SecretStr

from openhands.sdk.llm import LLM
from openhands.sdk.llm.options.chat_options import select_chat_options
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


# --- proxy-advertised request params (issue #5499) ---


@pytest.fixture
def model_info_server():
    """Real ``/v1/model/info`` endpoint; ``state`` controls its behavior."""
    state = {"entries": [], "required_key": "proxy-key", "status": 200, "body": None}
    auth_headers = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            auth = self.headers.get("Authorization")
            auth_headers.append(auth)
            required = state["required_key"]
            if required and auth != f"Bearer {required}":
                status, payload = 401, json.dumps({"error": {"code": "401"}}).encode()
            elif state["body"] is not None:
                status, payload = state["status"], state["body"]
            else:
                status = state["status"]
                payload = json.dumps({"data": state["entries"]}).encode()
            self.send_response(status)
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
        yield f"http://127.0.0.1:{server.server_port}", state, auth_headers
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


# Unknown to LiteLLM's registry, so only proxy metadata can describe it.
_ALIAS = "acme-reasoner-7"
_UPSTREAM = "openai/acme-reasoner-7-upstream"
_ENTRY = {
    "model_name": _ALIAS,
    "litellm_params": {
        "model": _UPSTREAM,
        "allowed_openai_params": ["reasoning_effort"],
    },
    "model_info": {"max_input_tokens": 200_000, "supports_reasoning_effort": None},
}


def _deployment(allowed):
    params = {"model": _UPSTREAM}
    if allowed is not None:
        params["allowed_openai_params"] = allowed
    return {**_ENTRY, "litellm_params": params}


def _proxy_llm(base_url: str, api_key: str | None = "proxy-key") -> LLM:
    return LLM(
        model=f"litellm_proxy/{_ALIAS}",
        base_url=base_url,
        api_key=SecretStr(api_key) if api_key else None,
        reasoning_effort="low",
        usage_id="proxy-capabilities",
    )


def test_authenticated_proxy_advertised_param_reaches_request(model_info_server):
    base_url, state, auth_headers = model_info_server
    state["entries"] = [_ENTRY]

    llm = _proxy_llm(base_url)
    features = llm._model_features()
    request = select_chat_options(llm, {}, has_tools=True)

    assert auth_headers == ["Bearer proxy-key"]
    assert llm.model_info == {
        "max_input_tokens": 200_000,
        "supports_reasoning_effort": None,
        "allowed_openai_params": ["reasoning_effort"],
    }
    assert features.supports_reasoning_effort is True
    assert request["reasoning_effort"] == "low"
    assert features.supports_prompt_cache_key is False
    assert features.supports_prompt_cache is False
    assert features.supports_vision is False


def test_authless_proxy_advertised_params_are_consumed(model_info_server):
    base_url, state, auth_headers = model_info_server
    state["required_key"] = None
    state["entries"] = [_ENTRY]

    llm = _proxy_llm(base_url, api_key=None)

    assert auth_headers == [None]
    assert llm._model_features().supports_reasoning_effort is True


@pytest.mark.parametrize(
    "deployments,expected",
    [
        (
            [["reasoning_effort", "prompt_cache_key"], ["reasoning_effort"]],
            ["reasoning_effort"],
        ),
        ([["reasoning_effort"], None], None),
        ([["reasoning_effort"], "reasoning_effort"], None),
        ([["reasoning_effort", 7, None]], ["reasoning_effort"]),
    ],
)
def test_advertised_params_must_hold_for_every_matching_deployment(
    model_info_server, deployments, expected
):
    base_url, state, _ = model_info_server
    state["entries"] = [_deployment(allowed) for allowed in deployments]

    info = _get_model_info_from_litellm_proxy(
        secret_api_key="proxy-key",
        base_url=base_url,
        model=f"litellm_proxy/{_ALIAS}",
    )

    assert info is not None
    assert info.get("allowed_openai_params") == expected


@pytest.mark.parametrize(
    "api_key,status,body",
    [
        (None, 200, None),
        ("wrong-key", 200, None),
        ("proxy-key", 500, b"internal error"),
        ("proxy-key", 200, b"{not json"),
        ("proxy-key", 200, b'{"data": {"unexpected": "shape"}}'),
    ],
    ids=["missing-key", "401", "non-2xx", "invalid-json", "malformed-data"],
)
def test_unavailable_proxy_metadata_falls_back_safely(
    model_info_server, api_key, status, body
):
    base_url, state, _ = model_info_server
    state["entries"] = [_ENTRY]
    state["status"], state["body"] = status, body

    llm = _proxy_llm(base_url, api_key=api_key)

    assert llm.model_info is None
    assert llm._model_features().supports_reasoning_effort is False
    assert "reasoning_effort" not in select_chat_options(llm, {}, has_tools=True)


def test_proxy_metadata_is_cached_per_key(model_info_server):
    base_url, state, auth_headers = model_info_server
    state["entries"] = [_ENTRY]

    _proxy_llm(base_url)
    llm = _proxy_llm(base_url)

    assert len(auth_headers) == 1
    assert llm._model_features().supports_reasoning_effort is True
