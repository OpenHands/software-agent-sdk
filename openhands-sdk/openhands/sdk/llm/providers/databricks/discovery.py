"""Databricks AI Gateway model discovery.

Queries GET /api/2.0/serving-endpoints and returns the chat-capable endpoints
exposed through the AI Gateway. Three endpoint classes are surfaced:

* ``FOUNDATION_MODEL_API`` — workspace-hosted Llama / Claude / Gemini /
  GPT-5 pay-per-token models (native AI Gateway).
* ``EXTERNAL_MODEL``      — customer-configured external model endpoints
  proxied through the gateway (still routed to provider-native APIs).
* ``CUSTOM_MODEL`` and ``endpoint_type=None`` endpoints are intentionally
  excluded — those are agent / custom-deployment endpoints whose payload
  shape is not guaranteed to be OpenAI-Chat-compatible.

Results are TTL-cached for 5 minutes in ``list_models_from_env``. The structured
``list_chat_endpoints`` call always hits the network because metadata is cheap
and callers may want fresh data.

PWAF: User-Agent header is included on every discovery call (required on ALL
Databricks HTTP).

Cache race condition: on cache miss multiple threads may call
``list_foundation_models()`` concurrently (thundering herd). Last writer wins —
no data corruption, just redundant API calls. We prefer this over holding the
lock during the HTTP call, which would serialize all model-picker refreshes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass

import httpx

from openhands.sdk.llm.providers.databricks.auth import DatabricksCredentials
from openhands.sdk.llm.providers.databricks.models import (
    ProviderFamily,
    detect_family,
)
from openhands.sdk.llm.providers.databricks.utils import USER_AGENT, normalize_host

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHED_MODELS: list[str] = []
_CACHE_EXPIRES_AT: float = 0.0
_CACHE_TTL_S: int = 300   # 5 minutes

# Endpoint types that expose AI-Gateway-shaped chat payloads. Everything else
# (CUSTOM_MODEL, None, agent/* tasks) is excluded by default — callers that
# really want them can pass a custom filter to list_chat_endpoints.
_GATEWAY_ENDPOINT_TYPES = frozenset({"FOUNDATION_MODEL_API", "EXTERNAL_MODEL"})


@dataclass(frozen=True)
class DiscoveredEndpoint:
    """Structured view of a serving endpoint from the list call.

    Only fields the list response reliably returns are captured here.
    Authoritative routing metadata (``foundation_model.api_types``,
    ``external_model.provider``) only comes from the per-endpoint describe
    call and is resolved lazily by ``DatabricksFMAPIClient._probe_metadata``.
    """

    name: str                           # e.g. "databricks-claude-sonnet-4-5"
    qualified_name: str                 # e.g. "databricks/databricks-claude-sonnet-4-5"
    endpoint_type: str | None           # FOUNDATION_MODEL_API | EXTERNAL_MODEL | None
    task: str                           # "llm/v1/chat"
    ready: bool
    creator: str | None = None


def list_chat_endpoints(
    credentials: DatabricksCredentials,
    *,
    include_not_ready: bool = False,
    allowed_endpoint_types: frozenset[str] = _GATEWAY_ENDPOINT_TYPES,
) -> list[DiscoveredEndpoint]:
    """Return all AI-Gateway chat endpoints visible in this workspace.

    Filters:
      * ``task == "llm/v1/chat"``
      * ``endpoint_type`` in ``allowed_endpoint_types`` (default:
        FOUNDATION_MODEL_API + EXTERNAL_MODEL)
      * ``state.ready == "READY"`` unless ``include_not_ready`` is True

    PWAF: ``User-Agent`` header required on ALL Databricks HTTP calls.
    """
    resp = httpx.get(
        f"{credentials.host}/api/2.0/serving-endpoints",
        headers={
            "Authorization": f"Bearer {credentials.get_token()}",
            "User-Agent": USER_AGENT,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    endpoints = resp.json().get("endpoints", [])

    out: list[DiscoveredEndpoint] = []
    for ep in endpoints:
        task = ep.get("task")
        if task != "llm/v1/chat":
            continue
        et = ep.get("endpoint_type")
        if et not in allowed_endpoint_types:
            continue
        ready = ep.get("state", {}).get("ready") == "READY"
        if not ready and not include_not_ready:
            continue
        name = ep.get("name")
        if not name:
            continue
        out.append(
            DiscoveredEndpoint(
                name=name,
                qualified_name=f"databricks/{name}",
                endpoint_type=et,
                task=task,
                ready=ready,
                creator=ep.get("creator"),
            )
        )
    return out


def list_foundation_models(credentials: DatabricksCredentials) -> list[str]:
    """Return qualified names of gateway chat endpoints (back-compat API).

    Historical name — kept for back-compat. Delegates to ``list_chat_endpoints``
    and returns only the ``databricks/<name>`` strings. Includes both
    ``FOUNDATION_MODEL_API`` and ``EXTERNAL_MODEL`` endpoints, READY only.
    """
    return [e.qualified_name for e in list_chat_endpoints(credentials)]


# ---------------------------------------------------------------------------
# Two-tier model picker: curated (static) + discovered (dynamic)
# ---------------------------------------------------------------------------
#
# Static/global lists go stale fast and paper over per-workspace availability.
# Dynamic discovery solves that but isn't available until the user has entered
# host + credentials. We surface both:
#
#   - tier 1: CURATED_DATABRICKS_MODELS — a small, hand-picked, family-balanced
#     set (Claude, GPT, Gemini) that known-good works against FMAPI on any
#     standard workspace. Used as the picker default before auth.
#   - tier 2: list_chat_endpoints(creds) — the actual endpoints this workspace
#     exposes (FOUNDATION_MODEL_API + EXTERNAL_MODEL), fetched live. Merged on
#     top of the curated set so customer-configured gpt-5 / gemini / claude
#     endpoints also show up.
#
# ``get_picker_entries`` is the single call UIs should use. It dedups by
# qualified name, preserves the curated "recommended" flag when the same
# endpoint is also discovered, and sorts recommended-first then by family/name.


@dataclass(frozen=True)
class ModelPickerEntry:
    """UI-facing model picker row — merged view across curated + discovered.

    Fields:
      qualified_name  — "databricks/<name>" (use as the model id for create_llm)
      name            — bare endpoint name
      family          — predicted provider family (OPENAI / ANTHROPIC / ...)
      source          — "curated" | "discovered" | "curated+discovered"
      endpoint_type   — FOUNDATION_MODEL_API | EXTERNAL_MODEL | None (curated-only)
      ready           — True if discovered and READY; True for curated (optimistic)
      recommended     — True for the curated "one per family" default picks
    """

    qualified_name: str
    name: str
    family: ProviderFamily
    source: str
    endpoint_type: str | None = None
    ready: bool = True
    recommended: bool = False


def _curated_entry(
    name: str, family: ProviderFamily, *, recommended: bool = False
) -> ModelPickerEntry:
    return ModelPickerEntry(
        qualified_name=f"databricks/{name}",
        name=name,
        family=family,
        source="curated",
        endpoint_type="FOUNDATION_MODEL_API",
        ready=True,
        recommended=recommended,
    )


# Curated tier-1 set — Claude / GPT / Gemini only. One "recommended" per family
# (fast + capable), plus a couple of siblings. Intentionally excludes Llama
# and legacy endpoints — those surface automatically via discovery if the
# workspace has them enabled.
#
# Last sync with Databricks FMAPI docs: May 2026.
# Source: https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/supported-models
CURATED_DATABRICKS_MODELS: tuple[ModelPickerEntry, ...] = (
    # ------------------------------------------------------------------ #
    # Anthropic — Claude (native Anthropic Messages API)
    # All live-tested PASS except opus-4-7 (temporarily rate-limited).
    # ------------------------------------------------------------------ #
    _curated_entry(
        "databricks-claude-sonnet-4-6", ProviderFamily.ANTHROPIC, recommended=True
    ),
    _curated_entry("databricks-claude-sonnet-4-5", ProviderFamily.ANTHROPIC),
    _curated_entry("databricks-claude-haiku-4-5", ProviderFamily.ANTHROPIC),
    _curated_entry("databricks-claude-opus-4-7", ProviderFamily.ANTHROPIC),
    _curated_entry("databricks-claude-opus-4-6", ProviderFamily.ANTHROPIC),
    _curated_entry("databricks-claude-opus-4-5", ProviderFamily.ANTHROPIC),
    _curated_entry("databricks-claude-opus-4-1", ProviderFamily.ANTHROPIC),
    # ------------------------------------------------------------------ #
    # OpenAI — GPT-5 series (Responses API) and gpt-oss (OpenAI Chat)
    # All live-tested PASS. gpt-5-5 / gpt-5-5-pro may be temporarily
    # rate-limited (403) on some workspaces.
    # ------------------------------------------------------------------ #
    _curated_entry(
        "databricks-gpt-5-mini", ProviderFamily.OPENAI_RESPONSES, recommended=True
    ),
    _curated_entry("databricks-gpt-5-5-pro", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-5", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-4", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-4-mini", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-4-nano", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-3-codex", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-2-codex", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-2", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-1", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5-nano", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-5", ProviderFamily.OPENAI_RESPONSES),
    _curated_entry("databricks-gpt-oss-120b", ProviderFamily.OPENAI),
    # ------------------------------------------------------------------ #
    # Google — Gemini (native generateContent)
    # ------------------------------------------------------------------ #
    # Live-tested PASS: gemini-3-5-flash, gemini-3-1-flash-lite,
    #   gemini-2-5-flash, gemini-2-5-pro
    # gemini-3-flash / gemini-3-pro: NOT available in typical workspaces —
    #   they require cross-geo routing on global endpoints.  They surface via
    #   live workspace discovery when the endpoint is actually available.
    # gemma-3-12b: excluded — 8,192-token context window is below the 16k
    #   minimum required by OpenHands.
    _curated_entry(
        "databricks-gemini-3-5-flash", ProviderFamily.GEMINI, recommended=True
    ),
    _curated_entry("databricks-gemini-3-1-flash-lite", ProviderFamily.GEMINI),
    _curated_entry("databricks-gemini-2-5-flash", ProviderFamily.GEMINI),
    _curated_entry("databricks-gemini-2-5-pro", ProviderFamily.GEMINI),
)


def get_picker_entries(
    credentials: DatabricksCredentials | None = None,
    *,
    include_curated: bool = True,
    include_discovered: bool = True,
    include_not_ready: bool = False,
) -> list[ModelPickerEntry]:
    """Merged view of curated + discovered Databricks models for picker UIs.

    Dedup rule: if a qualified name is in both tiers, the curated entry wins
    on ``recommended`` / ``family`` (our opinion) but picks up the live
    ``endpoint_type`` and ``ready`` fields from discovery, and its ``source``
    becomes ``"curated+discovered"`` so UIs can show a "verified + available"
    badge. Order: recommended-first, then by family, then by name.

    Network: calls ``list_chat_endpoints`` **only** if ``credentials`` is
    provided and ``include_discovered`` is True. Without creds this is a pure
    compute over the static curated set and safe to call from sync UI code.

    Errors during discovery are logged and swallowed — the curated tier is
    always returned even if the workspace is unreachable. That keeps the
    picker usable offline / during outages.
    """
    merged: dict[str, ModelPickerEntry] = {}

    if include_curated:
        for e in CURATED_DATABRICKS_MODELS:
            merged[e.qualified_name] = e

    if include_discovered and credentials is not None:
        try:
            discovered = list_chat_endpoints(
                credentials, include_not_ready=include_not_ready
            )
        except Exception as exc:
            logger.warning(
                "databricks_discovery_failed_in_picker", extra={"error": str(exc)}
            )
            discovered = []

        for d in discovered:
            existing = merged.get(d.qualified_name)
            if existing is not None:
                # Curated entry already present — upgrade with live signals,
                # keep our opinion on family + recommended.
                merged[d.qualified_name] = ModelPickerEntry(
                    qualified_name=existing.qualified_name,
                    name=existing.name,
                    family=existing.family,
                    source="curated+discovered",
                    endpoint_type=d.endpoint_type,
                    ready=d.ready,
                    recommended=existing.recommended,
                )
            else:
                merged[d.qualified_name] = ModelPickerEntry(
                    qualified_name=d.qualified_name,
                    name=d.name,
                    family=detect_family(d.name),
                    source="discovered",
                    endpoint_type=d.endpoint_type,
                    ready=d.ready,
                    recommended=False,
                )

    return sorted(
        merged.values(),
        key=lambda e: (not e.recommended, e.family.value, e.name),
    )


def list_models_from_env() -> list[str]:
    """Convenience wrapper that reads env vars and returns TTL-cached model list.

    Reads:
      DATABRICKS_HOST   — required
      DATABRICKS_TOKEN or DATABRICKS_ACCESS_TOKEN — required

    Returns [] silently if env vars are not set or on any API error.
    Results are cached for ``_CACHE_TTL_S`` seconds.  Cache writes are protected
    by ``_CACHE_LOCK``; reads use a lock-free fast path (last-writer-wins on miss).
    """
    global _CACHED_MODELS, _CACHE_EXPIRES_AT

    if time.time() < _CACHE_EXPIRES_AT:
        return _CACHED_MODELS

    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    token = os.environ.get("DATABRICKS_TOKEN") or os.environ.get(
        "DATABRICKS_ACCESS_TOKEN"
    )
    if not host or not token:
        return []

    credentials = DatabricksCredentials(
        host=normalize_host(host),
        get_token=lambda: token,   # type: ignore[return-value]
        auth_method="env",
    )
    try:
        models = list_foundation_models(credentials)
        with _CACHE_LOCK:
            _CACHED_MODELS = models
            _CACHE_EXPIRES_AT = time.time() + _CACHE_TTL_S
        return models
    except Exception as exc:
        logger.warning("databricks_discovery_failed", extra={"error": str(exc)})
        return []
