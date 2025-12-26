from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.llm.utils.metrics import Metrics, TokenUsage


class TokenDisplayMode(str, Enum):
    PER_CONTEXT = "per_context"  # show metrics for the latest request only
    ACCUMULATED = "accumulated"  # show accumulated tokens across all requests

    @classmethod
    def from_str(cls, value: str | None) -> TokenDisplayMode:
        if not value:
            return cls.PER_CONTEXT
        v = value.strip().lower().replace("-", "_")
        if v in {"per_context", "per_request", "latest", "current"}:
            return cls.PER_CONTEXT
        if v in {"accumulated", "total", "sum"}:
            return cls.ACCUMULATED
        # default to per-context to match current visual default and tests
        return cls.PER_CONTEXT


@dataclass(frozen=True)
class TokenDisplay:
    # Raw counts (not abbreviated)
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    reasoning_tokens: int
    context_window: int
    # Rate [0.0, 1.0] or None if undefined
    cache_hit_rate: float | None
    # Total accumulated cost in USD
    total_cost: float
    # Optional delta of input tokens compared to previous request
    since_last_input_tokens: int | None = None


def _get_combined_metrics(stats: ConversationStats | None) -> Metrics | None:
    if not stats:
        return None
    try:
        return stats.get_combined_metrics()
    except Exception:
        return None


def compute_token_display(
    stats: ConversationStats | None,
    mode: TokenDisplayMode = TokenDisplayMode.PER_CONTEXT,
    include_since_last: bool = False,
) -> TokenDisplay | None:
    """Compute token display values from conversation stats.

    Args:
        stats: ConversationStats to read metrics from
        mode: Whether to show per-context (latest request) or accumulated values
        include_since_last: If True, include the delta of input tokens compared to
            the previous request (when available)

    Returns:
        TokenDisplay with raw numeric values, or None if metrics are unavailable
    """
    combined = _get_combined_metrics(stats)
    if not combined:
        return None

    # No token usage recorded yet
    if not combined.token_usages:
        return None

    total_cost = combined.accumulated_cost or 0.0

    if mode == TokenDisplayMode.ACCUMULATED:
        usage: TokenUsage | None = combined.accumulated_token_usage
        if usage is None:
            return None
        input_tokens = usage.prompt_tokens or 0
        output_tokens = usage.completion_tokens or 0
        cache_read = usage.cache_read_tokens or 0
        reasoning_tokens = usage.reasoning_tokens or 0
        context_window = usage.context_window or 0
        cache_hit_rate = (cache_read / input_tokens) if input_tokens > 0 else None
        since_last: int | None = None
    else:  # PER_CONTEXT
        usage = combined.token_usages[-1]
        input_tokens = usage.prompt_tokens or 0
        output_tokens = usage.completion_tokens or 0
        cache_read = usage.cache_read_tokens or 0
        reasoning_tokens = usage.reasoning_tokens or 0
        context_window = usage.context_window or 0
        cache_hit_rate = (cache_read / input_tokens) if input_tokens > 0 else None
        since_last = None
        if include_since_last and len(combined.token_usages) >= 2:
            prev = combined.token_usages[-2]
            since_last = max(0, (usage.prompt_tokens or 0) - (prev.prompt_tokens or 0))

    return TokenDisplay(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        reasoning_tokens=reasoning_tokens,
        context_window=context_window,
        cache_hit_rate=cache_hit_rate,
        total_cost=total_cost,
        since_last_input_tokens=since_last,
    )


def get_default_mode_from_env() -> TokenDisplayMode:
    """Resolve default token display mode from env var.

    Env var: OH_TOKENS_VIEW_MODE
      - "per_context" (default)
      - "accumulated"
      - also accepts aliases: per_request/latest/current and total/sum
    """
    value = os.environ.get("OH_TOKENS_VIEW_MODE")
    return TokenDisplayMode.from_str(value)
