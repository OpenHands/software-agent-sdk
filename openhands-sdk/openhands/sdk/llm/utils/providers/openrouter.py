"""OpenRouter runtime metadata adapter.

Queries OpenRouter's per-endpoint catalog to determine the context/output
limits of the routes an ``LLM`` is actually configured to use, rather than the
model-level catalog value (which can overstate an endpoint's context).

See: https://github.com/OpenHands/software-agent-sdk/issues/4421
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from openhands.sdk.llm.utils.runtime_metadata import (
    RUNTIME_METADATA_TIMEOUT_SECONDS,
    ModelRuntimeMetadata,
)
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLM

logger = get_logger(__name__)

OPENROUTER_ENDPOINTS_BASE = "https://openrouter.ai/api/v1/models"


def _openrouter_model_id(llm: LLM) -> str | None:
    """Return the OpenRouter model id for the endpoints API, or None.

    Prefers the ``openrouter/<id>`` prefix form. When the base URL identifies
    OpenRouter and the model already looks like an OpenRouter id
    (``provider/model``), it is used as-is.
    """
    model = llm.model or ""
    base_url = llm.base_url or ""
    if model.startswith("openrouter/"):
        return model[len("openrouter/") :]
    if "openrouter.ai" in base_url and "/" in model:
        return model
    return None


def _normalize(name: Any) -> str:
    if not isinstance(name, str):
        return ""
    return name.strip().casefold()


def _provider_routing(llm: LLM) -> dict[str, Any]:
    extra_body = llm.litellm_extra_body or {}
    provider = extra_body.get("provider")
    return provider if isinstance(provider, dict) else {}


def _eligible_endpoints(
    endpoints: list[dict[str, Any]], routing: dict[str, Any]
) -> list[dict[str, Any]]:
    """Filter and rank the endpoint list to the routes the routing config permits.

    Matches OpenRouter's provider-selection semantics: ``only`` and ``ignore``
    are *eligibility* filters applied first, and ``order`` ranks the survivors.
    ``only`` overrides ``order`` (order is ignored when ``only`` is present).
    When ``allow_fallbacks`` is false and an ``order`` is given, routing pins
    to the first surviving ordered provider; otherwise ordered providers come
    first and the remaining survivors are appended as fallbacks.
    """
    only = [p for p in (routing.get("only") or [])]
    ignore = [p for p in (routing.get("ignore") or [])]
    order = [p for p in (routing.get("order") or [])]
    allow_fallbacks = routing.get("allow_fallbacks", True)

    only_set = {_normalize(p) for p in only}
    ignore_set = {_normalize(p) for p in ignore}

    # (1) Eligibility: keep routes allowed by `only` and not `ignore`. A route
    # listed in both is excluded.
    eligible = [
        e
        for e in endpoints
        if (not only_set or _normalize(e.get("provider_name")) in only_set)
        and _normalize(e.get("provider_name")) not in ignore_set
    ]
    if not eligible:
        return []

    # (2) `only` overrides `order`: with `only` set, order never applies.
    if only_set or not order:
        return eligible

    order_idx = {_normalize(name): i for i, name in enumerate(order)}
    present_order = [
        e for e in eligible if _normalize(e.get("provider_name")) in order_idx
    ]
    others = [
        e for e in eligible if _normalize(e.get("provider_name")) not in order_idx
    ]
    if not present_order:
        # An explicit order referencing none of the surviving providers is not
        # safely interpretable; leave it to the caller to fall back to model
        # metadata rather than guessing a pin.
        return []
    if allow_fallbacks:
        present_order.sort(key=lambda e: order_idx[_normalize(e.get("provider_name"))])
        return present_order + others
    # Pins to the first surviving ordered provider.
    present_order.sort(key=lambda e: order_idx[_normalize(e.get("provider_name"))])
    return present_order[:1]


def parse_openrouter_payload(
    payload: dict[str, Any],
    routing: dict[str, Any],
) -> ModelRuntimeMetadata | None:
    """Turn the OpenRouter endpoints response into runtime metadata.

    Returns ``None`` when routing cannot be interpreted safely, so the caller
    falls back to model-level metadata rather than guessing.
    """
    data = payload.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints:
        return None

    valid = [
        e
        for e in endpoints
        if isinstance(e, dict) and isinstance(e.get("context_length"), int)
    ]
    if not valid:
        return None

    eligible = _eligible_endpoints(valid, routing)
    if not eligible:
        # The config references routes not present in the catalog, or pins/skips
        # everything. Do not guess a limit.
        return None

    provider_names = [e.get("provider_name", "") for e in eligible]
    contexts = [e.get("context_length", 0) for e in eligible]

    if len(eligible) == 1:
        target = eligible[0]
        return ModelRuntimeMetadata(
            max_input_tokens=target.get("context_length"),
            source="openrouter_endpoints_api",
            candidate_providers=provider_names,
            selected_provider=target.get("provider_name"),
            confidence="exact",
        )

    # Multiple eligible routes: routing may pick any of them, so the runtime
    # provider is not known ahead of time and no single ``selected_provider``
    # should be implied. The input context is the minimum unless every route
    # agrees (in which case that value is exact).
    contexts_equal = len(set(contexts)) == 1
    return ModelRuntimeMetadata(
        max_input_tokens=contexts[0] if contexts_equal else min(contexts),
        source="openrouter_endpoints_api",
        candidate_providers=provider_names,
        selected_provider=None,
        confidence="exact" if contexts_equal else "safe_lower_bound",
    )


def _fetch_sync(url: str, client: httpx.Client | None) -> dict[str, Any] | None:
    own_client = client is None
    effective = client or httpx.Client(timeout=RUNTIME_METADATA_TIMEOUT_SECONDS)
    try:
        response = effective.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:  # noqa: BLE001 - any failure falls back upstream
        logger.debug(f"OpenRouter runtime metadata lookup failed: {e}", exc_info=True)
        return None
    finally:
        if own_client:
            effective.close()


async def _fetch_async(
    url: str, client: httpx.AsyncClient | None
) -> dict[str, Any] | None:
    own_client = client is None
    effective = client or httpx.AsyncClient(timeout=RUNTIME_METADATA_TIMEOUT_SECONDS)
    try:
        response = await effective.get(url)
        response.raise_for_status()
        return response.json()
    except Exception as e:  # noqa: BLE001 - any failure falls back upstream
        logger.debug(f"OpenRouter runtime metadata lookup failed: {e}", exc_info=True)
        return None
    finally:
        if own_client:
            await effective.aclose()


def resolve_openrouter_sync(
    llm: LLM, *, http_client: httpx.Client | None = None
) -> ModelRuntimeMetadata | None:
    model_id = _openrouter_model_id(llm)
    if not model_id:
        return None
    payload = _fetch_sync(
        f"{OPENROUTER_ENDPOINTS_BASE}/{model_id}/endpoints", http_client
    )
    if payload is None:
        return None
    return parse_openrouter_payload(payload, _provider_routing(llm))


async def aresolve_openrouter(
    llm: LLM, *, http_client: httpx.AsyncClient | None = None
) -> ModelRuntimeMetadata | None:
    model_id = _openrouter_model_id(llm)
    if not model_id:
        return None
    payload = await _fetch_async(
        f"{OPENROUTER_ENDPOINTS_BASE}/{model_id}/endpoints", http_client
    )
    if payload is None:
        return None
    return parse_openrouter_payload(payload, _provider_routing(llm))
