from __future__ import annotations

from collections.abc import Callable, Generator
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from litellm.exceptions import (
    APIConnectionError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout as LiteLLMTimeout,
)
from pydantic import BaseModel, Field, PrivateAttr

from openhands.sdk.llm.exceptions import LLMNoResponseError
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.llm.llm_response import LLMResponse
    from openhands.sdk.llm.utils.metrics import Metrics

logger = get_logger(__name__)

# Exceptions that trigger fallback to alternate LLMs (after retries exhausted).
_LLM_FALLBACK_EXCEPTIONS: Final[tuple[type[Exception], ...]] = (
    APIConnectionError,
    RateLimitError,
    ServiceUnavailableError,
    LiteLLMTimeout,
    InternalServerError,
    LLMNoResponseError,
)


class FallbackStrategy(BaseModel):
    """Encapsulates fallback behavior for LLM calls.

    When the primary LLM fails with a transient error (after retries),
    this strategy tries alternate LLMs loaded from LLMProfileStore profiles.
    Fallback is per-call: each new request starts with the primary model.
    """

    fallback_llms: list[str] = Field(
        description="Ordered list of LLM profile names to try on transient failure."
    )
    profile_store_dir: str | Path | None = Field(
        default=None,
        description="Path to directory containing profiles. "
        "If not specified, defaults to `.openhands/profiles`.",
    )

    # Private: lazily resolved LLM instances
    _resolved: list[Any] | None = PrivateAttr(default=None)

    def should_fallback(self, error: Exception) -> bool:
        """Whether this error type is eligible for fallback."""
        return isinstance(error, _LLM_FALLBACK_EXCEPTIONS)

    def try_fallback(
        self,
        primary_model: str,
        primary_error: Exception,
        primary_metrics: Metrics,
        call_fn: Callable[[Any], LLMResponse],
    ) -> LLMResponse | None:
        """Try fallback LLMs in order. Merges metrics into primary on success.

        Args:
            primary_model: The primary model name (for logging).
            primary_error: The error from the primary model.
            primary_metrics: The primary LLM's Metrics to merge fallback costs into.
            call_fn: A callable that takes an LLM instance and returns an LLMResponse.

        Returns:
            LLMResponse from the first successful fallback, or None if all fail.
        """
        total = len(self.fallback_llms)
        tried = 0
        for i, fb in enumerate(self._iter_fallbacks()):
            tried += 1
            remaining = total - i - 1
            logger.warning(
                f"[Fallback Strategy]Primary LLM ({primary_model}) failed with "
                f"{type(primary_error).__name__}, "
                f"trying fallback {i + 1}/{total} ({fb.model}); "
                f"{remaining} fallback(s) remaining"
            )
            try:
                # Disable nested fallbacks to prevent recursive chains
                saved_strategy = fb.fallback_strategy
                fb.fallback_strategy = None
                metrics_before = fb.metrics.deep_copy()
                try:
                    result = call_fn(fb)
                finally:
                    fb.fallback_strategy = saved_strategy
                # Merge fallback metrics (cost + tokens) into primary
                metrics_diff = fb.metrics.diff(metrics_before)
                primary_metrics.merge(metrics_diff)
                logger.info(f"[Fallback Strategy] Fallback LLM ({fb.model}) succeeded")
                return result
            except Exception as fb_error:
                logger.warning(
                    "[Fallback Strategy]"
                    f"Fallback {i + 1} ({fb.model}) failed: "
                    f"{type(fb_error).__name__}: {fb_error}"
                )
                continue

        if tried > 0:
            logger.error(
                "[Fallback Strategy] All fallback LLMs failed; re-raising primary error"
            )
        return None

    @cached_property
    def _profile_store(self) -> LLMProfileStore:
        return LLMProfileStore(self.profile_store_dir)

    def _iter_fallbacks(self) -> Generator[Any, None, None]:
        """Yield fallback LLM instances, resolving lazily from profiles.

        Profiles are loaded one at a time and appended to ``_resolved``
        progressively.  On subsequent calls the already-cached instances
        are yielded first, then resolution continues for any remaining
        profiles that were not yet loaded.
        """
        if self._resolved is None:
            self._resolved = []

        # Yield already-cached instances
        yield from self._resolved

        # Continue resolving profiles that haven't been loaded yet
        remaining_names = self.fallback_llms[len(self._resolved) :]
        for name in remaining_names:
            try:
                fb = self._profile_store.load(name)
                self._resolved.append(fb)
                yield fb
            except (FileNotFoundError, ValueError) as exc:
                logger.error(
                    "[Fallback Strategy] Failed to load "
                    f"fallback profile '{name}': {exc}"
                )
