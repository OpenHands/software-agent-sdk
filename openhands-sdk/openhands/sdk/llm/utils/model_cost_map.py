"""Provenance and sanity checks for LiteLLM's model database.

LiteLLM resolves its model database at import time and, by default, fetches
``model_prices_and_context_window.json`` from the tip of ``BerriAI/litellm@main``.
Pinning ``litellm`` in ``uv.lock`` pins its *code*, not that data, so the same
build reads different pricing, context windows and capability flags on
different days — and on a fetch failure it silently falls back to the older
copy bundled in the wheel, which answers those questions differently again.

Pinning the data is not available as a fix: the map describes provider APIs
that change weekly, so freezing it makes new models invisible to the SDK (no
context window, no capability flags). See #4880.

What is available is knowing what we loaded and noticing when it is absurd:

- :func:`model_cost_map_provenance` fingerprints the map actually in use, so a
  silent fallback or an unexpected change is diagnosable rather than invisible.
- :func:`describe_metadata_anomalies` bounds-checks the handful of fields the
  SDK consumes, which catches gross tampering and upstream mistakes alike —
  #4877 was the latter, and would have been reported by it.

Detection only, deliberately: these paths decide truncation and what the SDK
puts on the wire, so a false positive that refused a legitimate new model
would be worse than the drift it guards against. Enforcement can follow once
the signal has been observed in the wild.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from litellm import model_cost

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


# Numeric limits the SDK reads off model metadata. A value outside these bounds
# is not a model we do not know about yet — it is a value that would break
# truncation arithmetic.
_TOKEN_LIMIT_KEYS = ("max_input_tokens", "max_output_tokens", "max_tokens")

# Comfortably above any real context window (the largest advertised today is
# ~10M) and far below anything that would silently disable condensation.
_MAX_PLAUSIBLE_TOKENS = 100_000_000

# Capability flags the SDK branches on. LiteLLM's own schema declares these
# boolean; anything else means we are reading a document we do not understand.
_CAPABILITY_KEYS = (
    "supports_adaptive_thinking",
    "supports_prompt_cache",
    "supports_prompt_cache_retention",
    "supports_prompt_caching",
    "supports_reasoning",
    "supports_reasoning_effort",
    "supports_responses_api",
    "supports_sampling_params",
    "supports_stop_words",
    "supports_vision",
)


@dataclass(frozen=True)
class ModelCostMapProvenance:
    """Which model database this process actually loaded."""

    source: str
    """``"remote"`` or ``"local"`` — the latter meaning the copy bundled in the
    installed wheel, which LiteLLM also falls back to when a fetch fails."""

    url: str | None
    """Where it was fetched from, or ``None`` when the bundled copy was used."""

    is_env_forced: bool
    """Whether ``LITELLM_LOCAL_MODEL_COST_MAP`` selected the bundled copy."""

    fallback_reason: str | None
    """Why the remote fetch was abandoned, when it was."""

    model_count: int

    fingerprint: str
    """SHA-256 over the canonicalised map. Two processes reporting different
    fingerprints loaded different data, whatever their versions agree on."""


@lru_cache(maxsize=1)
def model_cost_map_provenance() -> ModelCostMapProvenance:
    """Fingerprint the model database in use. Computed once per process."""
    source, url, is_env_forced, fallback_reason = "unknown", None, False, None
    try:
        from litellm.litellm_core_utils.get_model_cost_map import (
            get_model_cost_map_source_info,
        )

        info = get_model_cost_map_source_info()
        source = str(info.get("source", "unknown"))
        url = info.get("url")
        is_env_forced = bool(info.get("is_env_forced", False))
        fallback_reason = info.get("fallback_reason")
    except Exception:  # pragma: no cover - LiteLLM internal, absent on older pins
        logger.debug("LiteLLM did not report a model-cost-map source", exc_info=True)

    canonical = json.dumps(model_cost, sort_keys=True, default=str).encode()
    return ModelCostMapProvenance(
        source=source,
        url=url,
        is_env_forced=is_env_forced,
        fallback_reason=fallback_reason,
        model_count=len(model_cost),
        fingerprint=hashlib.sha256(canonical).hexdigest(),
    )


@lru_cache(maxsize=1)
def log_model_cost_map_provenance() -> None:
    """Record the provenance once, so a silent fallback leaves a trace."""
    provenance = model_cost_map_provenance()
    logger.info(
        "LiteLLM model database: source=%s, url=%s, models=%d, sha256=%s",
        provenance.source,
        provenance.url,
        provenance.model_count,
        provenance.fingerprint[:16],
    )
    if provenance.fallback_reason:
        # The bundled copy is materially smaller than the published one, so
        # this is a capability change, not just a stale price list.
        logger.warning(
            "LiteLLM fell back to the model database bundled in the installed "
            "wheel (%s); capability and context-window answers may differ from "
            "the published data",
            provenance.fallback_reason,
        )


def describe_metadata_anomalies(
    model_info: Mapping[str, Any] | None,
) -> list[str]:
    """Bounds-check the metadata fields the SDK consumes.

    Returns a description per implausible value. Absent keys are not anomalies
    — most models declare only some of these.
    """
    if not model_info:
        return []

    anomalies: list[str] = []
    for key in _TOKEN_LIMIT_KEYS:
        value = model_info.get(key)
        if value is None:
            continue
        # Floats are ordinary here (xai/grok-4-fast-* publish 2000000.0), and
        # zero is meaningful rather than missing: moderation and embedding
        # models declare no output budget. Neither is worth a warning.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            anomalies.append(f"{key}={value!r} is not a number")
        elif not math.isfinite(value):
            anomalies.append(f"{key}={value!r} is not finite")
        elif value < 0:
            anomalies.append(f"{key}={value!r} is negative")
        elif value > _MAX_PLAUSIBLE_TOKENS:
            anomalies.append(f"{key}={value!r} exceeds {_MAX_PLAUSIBLE_TOKENS}")

    for key in _CAPABILITY_KEYS:
        value = model_info.get(key)
        if value is not None and not isinstance(value, bool):
            anomalies.append(f"{key}={value!r} is not a boolean")

    provider = model_info.get("litellm_provider")
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        anomalies.append(f"litellm_provider={provider!r} is not a provider name")

    return anomalies


@lru_cache(maxsize=4096)
def _warn_once(model: str | None, anomalies: tuple[str, ...]) -> None:
    provenance = model_cost_map_provenance()
    logger.warning(
        "Implausible model metadata for %r: %s. Loaded from source=%s url=%s "
        "sha256=%s. Using it as-is; see OpenHands/software-agent-sdk#4880",
        model,
        "; ".join(anomalies),
        provenance.source,
        provenance.url,
        provenance.fingerprint[:16],
    )


def warn_on_metadata_anomalies(
    model: str | None, model_info: Mapping[str, Any] | None
) -> None:
    """Report implausible metadata once per (model, anomaly set)."""
    anomalies = describe_metadata_anomalies(model_info)
    if anomalies:
        _warn_once(model, tuple(anomalies))
