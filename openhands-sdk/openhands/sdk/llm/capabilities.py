"""Model capability detection and validation for LLM instances.

This module extracts capability-related logic from the LLM class to improve
maintainability and testability. It handles:
- Model information lookup from litellm
- Context window validation
- Vision support detection
- Prompt caching support detection
- Responses API support detection
"""

from __future__ import annotations

import os
import warnings
from typing import Any, Final

from litellm.utils import supports_vision
from pydantic import SecretStr

from openhands.sdk.llm.exceptions import LLMContextWindowTooSmallError
from openhands.sdk.llm.utils.model_features import get_features
from openhands.sdk.llm.utils.model_info import get_litellm_model_info
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

__all__ = ["LLMCapabilities"]

# Minimum context window size required for OpenHands to function properly.
# Based on typical usage: system prompt (~2k) + conversation history (~4k)
# + tool definitions (~2k) + working memory (~8k) = ~16k minimum.
MIN_CONTEXT_WINDOW_TOKENS: Final[int] = 16384

# Environment variable to override the minimum context window check
ENV_ALLOW_SHORT_CONTEXT_WINDOWS: Final[str] = "ALLOW_SHORT_CONTEXT_WINDOWS"

# Default max output tokens when model info only provides 'max_tokens' (ambiguous).
# Some providers use 'max_tokens' for the total context window, not output limit.
# This cap prevents requesting output that exceeds the context window.
# 16384 is a safe default that works for most models (GPT-4o: 16k, Claude: 8k).
DEFAULT_MAX_OUTPUT_TOKENS_CAP: Final[int] = 16384


class LLMCapabilities:
    """Detects and caches model capabilities.

    This class encapsulates capability detection for LLM models, including:
    - Vision support
    - Prompt caching support
    - Responses API support
    - Context window validation

    It is initialized with model configuration and caches model info from litellm.

    Example:
        >>> caps = LLMCapabilities(
        ...     model="claude-sonnet-4-20250514",
        ...     model_canonical_name=None,
        ...     base_url=None,
        ...     api_key=SecretStr("key"),
        ...     disable_vision=False,
        ...     caching_prompt=True,
        ...     max_input_tokens=None,
        ...     max_output_tokens=None,
        ... )
        >>> caps.vision_is_active()
        True
        >>> caps.is_caching_prompt_active()
        True
    """

    def __init__(
        self,
        model: str,
        model_canonical_name: str | None,
        base_url: str | None,
        api_key: SecretStr | str | None,
        disable_vision: bool | None,
        caching_prompt: bool,
        max_input_tokens: int | None,
        max_output_tokens: int | None,
    ) -> None:
        """Initialize capabilities detection.

        Args:
            model: The model name/identifier.
            model_canonical_name: Optional canonical model name for feature lookups.
            base_url: Optional custom base URL for API calls.
            api_key: API key for authentication (used for proxy model info lookup).
            disable_vision: Whether vision is explicitly disabled. None is treated
                as False (vision enabled if model supports it).
            caching_prompt: Whether prompt caching is enabled.
            max_input_tokens: User-specified max input tokens, or None for auto.
            max_output_tokens: User-specified max output tokens, or None for auto.
        """
        self._model = model
        self._model_canonical_name = model_canonical_name
        self._base_url = base_url
        self._api_key = api_key
        # Treat None as False (vision enabled if model supports it)
        self._disable_vision = disable_vision if disable_vision is not None else False
        self._caching_prompt = caching_prompt

        # These can be auto-detected from model info
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens

        # Internal cache for model info (typed as Any to match LLM class)
        self._model_info: Any = None

        # Initialize model info and capabilities
        self._init_model_info_and_caps()

    @property
    def model(self) -> str:
        """Return the model name."""
        return self._model

    @property
    def model_name_for_capabilities(self) -> str:
        """Return canonical name for capability lookups (e.g., vision support)."""
        return self._model_canonical_name or self._model

    @property
    def model_info(self) -> dict[str, Any] | None:
        """Return the cached model info dictionary."""
        return self._model_info

    def _init_model_info_and_caps(self) -> None:
        """Initialize model info and auto-detect token limits."""
        self._model_info = get_litellm_model_info(
            secret_api_key=self._api_key,
            base_url=self._base_url,
            model=self.model_name_for_capabilities,
        )

        # Context window (max_input_tokens)
        if (
            self.max_input_tokens is None
            and self._model_info is not None
            and isinstance(self._model_info.get("max_input_tokens"), int)
        ):
            self.max_input_tokens = self._model_info.get("max_input_tokens")

        # Validate context window size
        self._validate_context_window_size()

        # Auto-detect max_output_tokens
        if self.max_output_tokens is None:
            self._auto_detect_max_output_tokens()

    def _auto_detect_max_output_tokens(self) -> None:
        """Auto-detect max_output_tokens from model info."""
        if any(
            m in self._model
            for m in [
                "claude-3-7-sonnet",
                "claude-sonnet-4",
                "kimi-k2-thinking",
            ]
        ):
            self.max_output_tokens = (
                64000  # practical cap (litellm may allow 128k with header)
            )
            logger.debug(
                f"Setting max_output_tokens to {self.max_output_tokens} "
                f"for {self._model}"
            )
        elif self._model_info is not None:
            if isinstance(self._model_info.get("max_output_tokens"), int):
                self.max_output_tokens = self._model_info.get("max_output_tokens")
            elif isinstance(self._model_info.get("max_tokens"), int):
                # 'max_tokens' is ambiguous: some providers use it for total
                # context window, not output limit. Cap it to avoid requesting
                # output that exceeds the context window.
                max_tokens_value = self._model_info.get("max_tokens")
                assert isinstance(max_tokens_value, int)  # for type checker
                self.max_output_tokens = min(
                    max_tokens_value, DEFAULT_MAX_OUTPUT_TOKENS_CAP
                )
                if max_tokens_value > DEFAULT_MAX_OUTPUT_TOKENS_CAP:
                    logger.debug(
                        "Capping max_output_tokens from %s to %s for %s "
                        "(max_tokens may be context window, not output)",
                        max_tokens_value,
                        self.max_output_tokens,
                        self._model,
                    )

        # Special handling for o3 models
        if "o3" in self._model:
            o3_limit = 100000
            if self.max_output_tokens is None or self.max_output_tokens > o3_limit:
                self.max_output_tokens = o3_limit
                logger.debug(
                    "Clamping max_output_tokens to %s for %s",
                    self.max_output_tokens,
                    self._model,
                )

    def _validate_context_window_size(self) -> None:
        """Validate that the context window is large enough for OpenHands."""
        # Allow override via environment variable
        if os.environ.get(ENV_ALLOW_SHORT_CONTEXT_WINDOWS, "").lower() in (
            "true",
            "1",
            "yes",
        ):
            return

        # Unknown context window - cannot validate
        if self.max_input_tokens is None:
            return

        # Check minimum requirement
        if self.max_input_tokens < MIN_CONTEXT_WINDOW_TOKENS:
            raise LLMContextWindowTooSmallError(
                self.max_input_tokens, MIN_CONTEXT_WINDOW_TOKENS
            )

    def vision_is_active(self) -> bool:
        """Check if vision is supported and enabled.

        Returns:
            True if the model supports vision and it's not disabled.
        """
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return not self._disable_vision and self._supports_vision()

    def _supports_vision(self) -> bool:
        """Check if the model supports vision capabilities.

        Returns:
            True if model is vision capable. Returns False if model not
            supported by litellm.
        """
        # litellm.supports_vision currently returns False for 'openai/gpt-...'
        # or 'anthropic/claude-...' (with prefixes) but model_info will have
        # the correct value for some reason.
        # Check both the full model name and the name after proxy prefix
        model_for_caps = self.model_name_for_capabilities
        return (
            supports_vision(model_for_caps)
            or supports_vision(model_for_caps.split("/")[-1])
            or (
                self._model_info is not None
                and self._model_info.get("supports_vision", False)
            )
            or False  # fallback to False if model_info is None
        )

    def is_caching_prompt_active(self) -> bool:
        """Check if prompt caching is supported and enabled for current model.

        Returns:
            True if prompt caching is supported and enabled for the given model.
        """
        if not self._caching_prompt:
            return False
        # We don't need to look-up model_info, because
        # only Anthropic models need explicit caching breakpoints
        return (
            self._caching_prompt
            and get_features(self.model_name_for_capabilities).supports_prompt_cache
        )

    def uses_responses_api(self) -> bool:
        """Check if this model uses the OpenAI Responses API path.

        Returns:
            True if the model should use the Responses API.
        """
        # by default, uses = supports
        return get_features(self.model_name_for_capabilities).supports_responses_api
