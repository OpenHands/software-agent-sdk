"""Provider-aware runtime metadata resolution for :class:`~openhands.sdk.llm.LLM`.

The static model catalog (LiteLLM model metadata) describes a model's nominal
limits, which is not always the limit of the route a configured provider
actually serves at runtime. OpenRouter is the canonical example: the catalog
advertises `deepseek/deepseek-v4-flash-0731` with a 1M-token context, but an
endpoint such as CoreWeave only supports 262k tokens.

This module resolves route-aware limits lazily, caches them with a TTL, and
falls back to the existing metadata on any error. It is a narrow compatibility
layer for metadata LiteLLM does not yet expose, and should be removable (or
delegating) once LiteLLM provides equivalent provider-endpoint metadata.

See: https://github.com/OpenHands/software-agent-sdk/issues/4421
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM

logger = get_logger(__name__)

# How long a successfully-resolved runtime update stays fresh.
RUNTIME_METADATA_TTL_SECONDS = 3600
# How long to suppress rediscovery after a failed/negative resolution so a
# flaky provider or a closed network hole is not hammered on every step.
RUNTIME_METADATA_NEGATIVE_TTL_SECONDS = 300
# Bounded upstream probe; metadata discovery must never block a completion.
RUNTIME_METADATA_TIMEOUT_SECONDS = 10.0


class ModelRuntimeMetadata(BaseModel):
    """Route-aware limits resolved for a single ``LLM`` configuration.

    Includes provenance so callers can reason about whether the value is the
    exact limit of a pinned route, a conservative bound across eligible routes,
    or a fallback to model-level metadata.
    """

    max_input_tokens: int | None = None
    source: str
    candidate_providers: list[str] = Field(default_factory=list)
    selected_provider: str | None = None
    confidence: Literal["exact", "safe_lower_bound", "model_level", "unknown"] = (
        "unknown"
    )


# An async provider adapter: returns route-aware metadata or None when the
# provider is unsupported/unresolvable (so the caller falls back to model-level
# metadata).
ProviderResolver = Callable[["LLM"], Coroutine[Any, Any, ModelRuntimeMetadata | None]]


def _provider_config(llm: LLM) -> dict[str, Any]:
    """Return the routing chunk of ``litellm_extra_body``, if any."""
    extra_body = llm.litellm_extra_body or {}
    provider = extra_body.get("provider")
    return provider if isinstance(provider, dict) else {}


def cache_key(llm: LLM) -> tuple[str, str, str]:
    """A stable key describing the configured route.

    Two ``LLM`` instances that resolve to the same provider/mode and routing
    configuration share a cache entry; a different routing preference does not.
    """
    model = llm.model or ""
    base_url = llm.base_url or ""
    provider = _provider_config(llm)
    try:
        provider_json = json.dumps(provider, sort_keys=True, default=str)
    except TypeError:  # defensively; routing config is JSON-serializable
        provider_json = repr(sorted(provider.items()))
    return (model, base_url, provider_json)


# ---------------------------------------------------------------------------
# Cache / negative-cache helpers for instance state stored on the LLM
# ---------------------------------------------------------------------------


def cached_metadata(
    metadata: ModelRuntimeMetadata | None,
    fetched_at: float | None,
) -> ModelRuntimeMetadata | None:
    """Return a still-fresh cached metadata, else None (without network I/O)."""
    if metadata is None or fetched_at is None:
        return None
    if time.monotonic() - fetched_at < RUNTIME_METADATA_TTL_SECONDS:
        return metadata
    return None


def in_negative_cache(negative_until: float | None) -> bool:
    if negative_until is None:
        return False
    return time.monotonic() < negative_until


def store_result(
    metadata: ModelRuntimeMetadata | None,
    now: float | None = None,
) -> tuple[float | None, float | None, ModelRuntimeMetadata | None]:
    """Compute new (fetched_at, negative_until, metadata) state after a probe.

    Returns the state fields so the caller can persist them on the instance.
    """
    now = now if now is not None else time.monotonic()
    if metadata is not None:
        return (now, None, metadata)
    return (None, now + RUNTIME_METADATA_NEGATIVE_TTL_SECONDS, None)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _openrouter_resolver() -> ProviderResolver:
    # Imported lazily so this module stays importable without httpx and so the
    # adapter's dependencies do not load unless an OpenRouter model is used.
    from openhands.sdk.llm.utils.providers.openrouter import aresolve_openrouter

    return aresolve_openrouter


def detect_provider(llm: LLM) -> ProviderResolver | None:
    model = llm.model or ""
    base_url = llm.base_url or ""
    if model.startswith("openrouter/") or "openrouter.ai" in base_url:
        return _openrouter_resolver()
    return None


async def _safe_resolve(
    resolver: ProviderResolver, llm: LLM
) -> ModelRuntimeMetadata | None:
    """Run a resolver, mapping any unexpected error to ``None``.

    Transport/parsing failures must never abort the real LLM request; the
    caller falls back to model-level metadata instead. Cancellation always
    propagates so the caller can honour ``await`` cancellation.
    """
    try:
        return await resolver(llm)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - any failure falls back upstream
        logger.warning(
            "runtime metadata resolution failed for %s (provider: %s); falling "
            "back to model-level metadata: %s",
            llm.model,
            llm.base_url,
            e,
            exc_info=True,
        )
        return None


_inflight: dict[
    tuple[asyncio.AbstractEventLoop, tuple[str, str, str]],
    asyncio.Task[ModelRuntimeMetadata | None],
] = {}


async def aresolve_provider_metadata(
    llm: LLM,
) -> ModelRuntimeMetadata | None:
    """Resolve provider-aware runtime metadata, deduplicating concurrent calls.

    Returns ``None`` for unsupported providers or an unresolvable route so the
    caller falls back to static model metadata.

    In-flight tasks are keyed by *event loop* in addition to the route so that
    two threads each running their own loop (a supported way to drive a shared
    ``LLM``) never await a task attached to another loop.
    """
    resolver = detect_provider(llm)
    if resolver is None:
        return None

    key = cache_key(llm)
    inflight_key = (asyncio.get_running_loop(), key)
    pending = _inflight.get(inflight_key)
    if pending is not None and not pending.done():
        # A concurrent resolution for the same route & event loop is already in
        # flight; await it rather than issuing a duplicate upstream request.
        return await pending

    task = asyncio.create_task(_safe_resolve(resolver, llm))
    _inflight[inflight_key] = task
    try:
        return await task
    finally:
        if _inflight.get(inflight_key) is task:
            del _inflight[inflight_key]


def resolve_provider_metadata_sync(llm: LLM) -> ModelRuntimeMetadata | None:
    """Synchronous variant of :func:`aresolve_provider_metadata`.

    Runs the async adapter in a private event loop. Callers on a running event
    loop (e.g. the agent-server async path) should use the async variant; if one
    is detected, this returns ``None`` so the caller falls back to model-level
    metadata instead of raising ``RuntimeError``.
    """
    resolver = detect_provider(llm)
    if resolver is None:
        return None

    # asyncio.run() cannot be entered from a thread that already owns a running
    # event loop. Prefer a graceful fallback over crashing the caller.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        logger.warning(
            "resolve_provider_metadata_sync() called from a running event loop; "
            "use aresolve_runtime_metadata() instead. Falling back to model metadata."
        )
        return None

    async def _run() -> ModelRuntimeMetadata | None:
        return await _safe_resolve(resolver, llm)

    return asyncio.run(_run())
