import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from litellm.types.utils import ModelResponseStream


# Type alias for stream chunks
LLMStreamChunk = ModelResponseStream

TokenCallbackType = Callable[[LLMStreamChunk], None]
AsyncTokenCallbackType = Callable[[LLMStreamChunk], Coroutine[Any, Any, None]]

# Accepts either sync or async token callbacks for async methods.
AnyTokenCallbackType = TokenCallbackType | AsyncTokenCallbackType


async def _invoke_token_callback(
    cb: AnyTokenCallbackType, chunk: LLMStreamChunk
) -> None:
    """Invoke a token callback, awaiting if it is a coroutine function."""
    if asyncio.iscoroutinefunction(cb):
        await cb(chunk)
    else:
        cb(chunk)  # type: ignore[arg-type]
