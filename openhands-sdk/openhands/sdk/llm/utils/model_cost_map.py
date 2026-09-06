"""Which model database this process actually loaded.

LiteLLM resolves its model database at import time and, by default, fetches
``model_prices_and_context_window.json`` from the tip of ``BerriAI/litellm@main``.
Pinning ``litellm`` in ``uv.lock`` pins its *code*, not that data, and on a
fetch failure it silently falls back to the older copy bundled in the wheel,
which answers capability and context-window questions differently.

Nothing surfaces which of those a process read, so two hosts behaving
differently cannot be told apart and a fallback leaves no trace. This records
it.

Diagnostics, not a control: it does not make the data trustworthy or
deterministic. See #4880 for why neither pinning nor signing achieves that,
and what does.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache

from litellm import model_cost

from openhands.sdk.logger import get_logger


logger = get_logger(__name__)


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
