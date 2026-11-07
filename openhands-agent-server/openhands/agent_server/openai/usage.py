"""Token use for one gateway request."""

from openhands.sdk.llm.utils.metrics import TokenUsage


def token_usage_delta(
    before: TokenUsage | None, after: TokenUsage | None
) -> TokenUsage | None:
    if after is None:
        return None
    previous = before or TokenUsage()
    return TokenUsage(
        model=after.model,
        prompt_tokens=max(0, after.prompt_tokens - previous.prompt_tokens),
        completion_tokens=max(0, after.completion_tokens - previous.completion_tokens),
        cache_read_tokens=max(0, after.cache_read_tokens - previous.cache_read_tokens),
        cache_write_tokens=max(
            0, after.cache_write_tokens - previous.cache_write_tokens
        ),
        reasoning_tokens=max(0, after.reasoning_tokens - previous.reasoning_tokens),
    )
