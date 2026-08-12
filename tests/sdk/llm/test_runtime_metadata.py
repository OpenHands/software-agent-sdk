"""Tests for provider-aware runtime metadata resolution.

Covers the OpenRouter adapter (route-aware context limits), the LLM facade
methods, the precedence rules, TTL/negative caching, concurrent de-duplication,
and that ``effective_max_input_tokens`` stays side-effect free.

See: https://github.com/OpenHands/software-agent-sdk/issues/4421
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from openhands.sdk.llm import LLM, Message, ModelRuntimeMetadata, TextContent
from openhands.sdk.llm.utils import runtime_metadata as rm
from openhands.sdk.llm.utils.providers import openrouter as orm


PAYLOAD = {
    "data": {
        "id": "deepseek/deepseek-v4-flash-0731",
        "endpoints": [
            {
                "provider_name": "CoreWeave",
                "context_length": 262144,
                "max_completion_tokens": 262144,
            },
            {
                "provider_name": "Baseten",
                "context_length": 1048576,
                "max_completion_tokens": 1048576,
            },
            {
                "provider_name": "DeepInfra",
                "context_length": 1048576,
                "max_completion_tokens": 65536,
            },
        ],
    }
}


def _openrouter_llm(**overrides) -> LLM:
    return LLM(
        model=overrides.pop("model", "openrouter/deepseek/deepseek-v4-flash-0731"),
        litellm_extra_body=overrides.pop(
            "litellm_extra_body",
            {"provider": {"order": ["CoreWeave", "Baseten"], "allow_fallbacks": False}},
        ),
        **overrides,
    )


def _handler(payload=PAYLOAD, *, status: int = 200, body: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/endpoints" in request.url.path
        if status != 200:
            return httpx.Response(status, text=body or "boom")
        return httpx.Response(200, json=payload)

    return handler


def _async_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sync_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _patch_async_factory(handler):
    """Return a AsyncClient factory bound to a MockTransport(handler).

    The original class is captured before any patch is installed so the
    factory does not recurse into a patched ``httpx.AsyncClient``.
    """
    original = orm.httpx.AsyncClient

    def factory(*args, **kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    return factory


# ---------------------------------------------------------------------------
# Parsing / routing unit tests
# ---------------------------------------------------------------------------


def test_parse_pinned_order_returns_exact():
    md = orm.parse_openrouter_payload(
        PAYLOAD, {"order": ["CoreWeave", "Baseten"], "allow_fallbacks": False}
    )
    assert md is not None
    assert md.confidence == "exact"
    assert md.max_input_tokens == 262144
    assert md.selected_provider == "CoreWeave"
    assert md.candidate_providers == ["CoreWeave"]


def test_parse_allow_fallbacks_returns_safe_lower_bound():
    md = orm.parse_openrouter_payload(
        PAYLOAD, {"order": ["CoreWeave", "Baseten"], "allow_fallbacks": True}
    )
    assert md is not None
    assert md.confidence == "safe_lower_bound"
    assert md.max_input_tokens == 262144
    # With fallbacks enabled the runtime provider is not known ahead of time, so
    # the safe lower bound must not claim a specific provider.
    assert md.selected_provider is None


def test_parse_no_routing_uses_all_endpoints():
    md = orm.parse_openrouter_payload(PAYLOAD, {})
    assert md is not None
    assert md.confidence == "safe_lower_bound"
    assert md.max_input_tokens == 262144
    assert md.selected_provider is None
    assert set(md.candidate_providers) == {"CoreWeave", "Baseten", "DeepInfra"}


def test_parse_provider_names_normalized_case_insensitively():
    payload = {
        "data": {
            "endpoints": [
                {"provider_name": "coreweave", "context_length": 262144},
                {"provider_name": "baseten", "context_length": 1048576},
            ]
        }
    }
    md = orm.parse_openrouter_payload(
        payload, {"order": ["COREWEAVE"], "allow_fallbacks": False}
    )
    assert md is not None
    assert md.confidence == "exact"
    assert md.max_input_tokens == 262144


def test_parse_unmatched_order_falls_back_to_none():
    # An explicit order referencing providers absent from the catalog is not
    # safely interpretable; model-level metadata should be used instead.
    assert orm.parse_openrouter_payload(PAYLOAD, {"order": ["Nope"]}) is None
    assert orm.parse_openrouter_payload(PAYLOAD, {"only": ["Nope"]}) is None


def test_parse_ignore_removes_routes():
    md = orm.parse_openrouter_payload(
        PAYLOAD,
        {"order": ["CoreWeave"], "ignore": ["Baseten"], "allow_fallbacks": True},
    )
    # CoreWeave retained; Baseten ignored; DeepInfra added back as fallback but
    # the safe lower bound is still CoreWeave's 262144.
    assert md is not None
    assert md.max_input_tokens == 262144


def test_parse_same_context_across_routes_is_exact():
    payload = {
        "data": {
            "endpoints": [
                {"provider_name": "A", "context_length": 1000},
                {"provider_name": "B", "context_length": 1000},
            ]
        }
    }
    md = orm.parse_openrouter_payload(payload, {})
    assert md is not None
    assert md.confidence == "exact"
    assert md.max_input_tokens == 1000


def test_parse_malformed_payload_returns_none():
    assert orm.parse_openrouter_payload({}, {}) is None
    assert orm.parse_openrouter_payload({"data": {}}, {}) is None
    assert orm.parse_openrouter_payload({"data": []}, {}) is None
    # Endpoints with no usable context_length.
    assert (
        orm.parse_openrouter_payload(
            {"data": {"endpoints": [{"provider_name": "A"}]}}, {}
        )
        is None
    )


def test_parse_list_shaped_data_supported():
    payload = {"data": [{"endpoints": PAYLOAD["data"]["endpoints"]}]}
    md = orm.parse_openrouter_payload(payload, {})
    assert md is not None
    assert md.max_input_tokens == 262144


def test_openrouter_model_id_normalization():
    llm = LLM(model="openrouter/deepseek/deepseek-v4-flash-0731")
    assert orm._openrouter_model_id(llm) == "deepseek/deepseek-v4-flash-0731"

    llm2 = LLM(
        model="deepseek/deepseek-v4-flash-0731", base_url="https://openrouter.ai/api/v1"
    )
    assert orm._openrouter_model_id(llm2) == "deepseek/deepseek-v4-flash-0731"

    # Non-OpenRouter config yields no id.
    llm3 = LLM(model="openai/gpt-4o")
    assert orm._openrouter_model_id(llm3) is None


# ---------------------------------------------------------------------------
# LLM facade / caching / precedence tests
# ---------------------------------------------------------------------------


def test_effective_unchanged_before_resolution():
    llm = _openrouter_llm()
    assert llm.resolved_runtime_metadata is None
    # Property performs no network I/O.
    assert llm.effective_max_input_tokens is None


def test_explicit_max_input_tokens_wins():
    llm = _openrouter_llm(max_input_tokens=131072)
    handler = _handler()
    client = _sync_client(handler)
    md = orm.resolve_openrouter_sync(llm, http_client=client)
    assert md is not None and md.max_input_tokens == 262144
    # Explicit config still outranks discovered metadata.
    assert llm.effective_max_input_tokens == 131072


def test_async_resolution_updates_effective():
    llm = _openrouter_llm()

    async def go():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(orm.httpx, "AsyncClient", _patch_async_factory(_handler()))
            return await llm.aresolve_runtime_metadata()

    md = asyncio.run(go())
    assert md is not None
    assert md.confidence == "exact"
    assert llm.effective_max_input_tokens == 262144


def test_acompletion_resolves_runtime_metadata(monkeypatch):
    """The async completion path must wire in runtime-metadata resolution.

    Without a production caller of ``aresolve_runtime_metadata`` the cache is
    never populated and ``effective_max_input_tokens`` never reflects the
    runtime route (issue #4421).
    """
    from litellm.types.utils import (
        Choices,
        Message as LiteLLMMessage,
        ModelResponse,
        Usage,
    )

    llm = LLM(model="gpt-4o", api_key="test", usage_id="test-llm")
    resolved = {"calls": 0}
    orig_resolve = llm.aresolve_runtime_metadata

    async def recording(self, *, force: bool = False):
        resolved["calls"] += 1
        return await orig_resolve(force=force)

    monkeypatch.setattr(LLM, "aresolve_runtime_metadata", recording)

    async def fake_acompletion(*args, **kwargs):
        return ModelResponse(
            id="test",
            choices=[
                Choices(
                    finish_reason="stop",
                    message=LiteLLMMessage(content="ok", role="assistant"),
                )
            ],
            created=0,
            model="gpt-4o",
            object="chat.completion",
            usage=Usage(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )

    monkeypatch.setattr("openhands.sdk.llm.llm.litellm_acompletion", fake_acompletion)

    async def go():
        return await llm.acompletion(
            messages=[Message(role="user", content=[TextContent(text="hi")])]
        )

    assert asyncio.run(go()) is not None
    assert resolved["calls"] >= 1


def test_sync_and_async_agree():
    handler = _handler()
    llm_sync = _openrouter_llm()
    md_sync = orm.resolve_openrouter_sync(llm_sync, http_client=_sync_client(handler))
    md_async = asyncio.run(_aresolve(llm_sync, handler))
    assert md_sync == md_async


def _aresolve(llm, handler):
    async def go():
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(orm.httpx, "AsyncClient", _patch_async_factory(handler))
            return await llm.aresolve_runtime_metadata()

    return go()


def test_failure_negative_caches_then_refreshes(monkeypatch):
    llm = _openrouter_llm()
    calls = {"n": 0}

    def flaky_handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=PAYLOAD)

    async def go():
        monkeypatch.setattr(
            orm.httpx, "AsyncClient", _patch_async_factory(flaky_handler)
        )
        first = await llm.aresolve_runtime_metadata()
        # Within the negative-cache window no second request is issued.
        second = await llm.aresolve_runtime_metadata()
        return first, second, calls["n"]

    first, second, count = asyncio.run(go())
    assert first is None
    assert second is None
    assert count == 1


def test_forced_resolution_bypasses_cache(monkeypatch):
    llm = _openrouter_llm()
    calls = {"n": 0}

    def counting_handler(request):
        calls["n"] += 1
        return httpx.Response(200, json=PAYLOAD)

    async def go():
        monkeypatch.setattr(
            orm.httpx, "AsyncClient", _patch_async_factory(counting_handler)
        )
        await llm.aresolve_runtime_metadata()  # caches
        await llm.aresolve_runtime_metadata()  # served from cache
        await llm.aresolve_runtime_metadata(force=True)  # re-fetches
        return calls["n"]

    assert asyncio.run(go()) == 2


def test_concurrent_resolution_deduplicates(monkeypatch):
    llm = _openrouter_llm()
    calls = {"n": 0}

    async def slow_handler(request):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=PAYLOAD)

    monkeypatch.setattr(orm.httpx, "AsyncClient", _patch_async_factory(slow_handler))
    rm._inflight.clear()

    async def go():
        results = await asyncio.gather(
            llm.aresolve_runtime_metadata(),
            llm.aresolve_runtime_metadata(),
            llm.aresolve_runtime_metadata(),
        )
        return results

    results = asyncio.run(go())
    assert all(r is not None and r.max_input_tokens == 262144 for r in results)
    assert calls["n"] == 1
    rm._inflight.clear()


def test_sync_resolver_inside_running_loop_falls_back():
    """The sync resolver must not raise asyncio.run() inside a running loop."""
    llm = _openrouter_llm()

    async def go():
        # A running loop is present here, so the sync resolver must return None
        # without attempting to enter a nested event loop or issuing a request.
        return rm.resolve_provider_metadata_sync(llm)

    assert asyncio.run(go()) is None


def test_http_error_timeout_and_unsupported_fall_back(monkeypatch):
    # HTTP 500 -> None
    llm_err = _openrouter_llm()
    md = orm.resolve_openrouter_sync(
        llm_err, http_client=_sync_client(_handler(status=500))
    )
    assert md is None

    # Non-OpenRouter model -> detect_provider returns None, no network.
    llm_other = LLM(model="openai/gpt-4o")
    assert rm.detect_provider(llm_other) is None

    async def go():
        monkeypatch.setattr(
            orm.httpx, "AsyncClient", _patch_async_factory(_handler(status=500))
        )
        return await llm_err.aresolve_runtime_metadata()

    assert asyncio.run(go()) is None


def test_cached_metadata_ttl():
    md = ModelRuntimeMetadata(max_input_tokens=262144, source="test")
    now = rm.time.monotonic()
    assert rm.cached_metadata(md, now) is md
    # Expired -> None
    assert rm.cached_metadata(md, now - rm.RUNTIME_METADATA_TTL_SECONDS - 1) is None
    assert rm.cached_metadata(None, now) is None
    assert rm.cached_metadata(md, None) is None


def test_serialization_excludes_runtime_cache():
    llm = _openrouter_llm()
    llm._runtime_metadata = ModelRuntimeMetadata(max_input_tokens=262144, source="test")
    llm._runtime_metadata_fetched_at = 1.0
    dumped = llm.model_dump()
    assert "runtime_metadata" not in dumped
    assert "_runtime_metadata" not in dumped


def test_cache_key_depends_on_routing():
    llm_a = _openrouter_llm(litellm_extra_body={"provider": {"order": ["CoreWeave"]}})
    llm_b = _openrouter_llm(litellm_extra_body={"provider": {"order": ["Baseten"]}})
    assert rm.cache_key(llm_a) != rm.cache_key(llm_b)
