"""Verify image delivery at adapters known to flatten multimodal content."""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler

from openhands.sdk.llm.exceptions import LLMBadRequestError


def _image_count(value: Any) -> int:
    if isinstance(value, Mapping):
        return int(value.get("type") == "image_url") + sum(
            _image_count(v) for v in value.values()
        )
    if isinstance(value, list):
        return sum(_image_count(v) for v in value)
    return 0


def _check_delivery(expected: int, data: Any, body: Any) -> None:
    payload = body if body is not None else data
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    if _image_count(payload) < expected:
        raise LLMBadRequestError(
            "The installed LiteLLM adapter discarded image content before sending "
            "the request. No request was sent. Use a compatible adapter or an "
            "explicitly configured OpenAI-compatible transport; enabling vision "
            "alone cannot fix adapter-level image loss."
        )


class _ImageDeliveryHTTPHandler(HTTPHandler):
    def __init__(self, expected: int, timeout: Any):
        super().__init__(timeout=timeout)
        self.expected = expected

    def post(self, *args: Any, **kwargs: Any):
        data = kwargs.get("data", args[1] if len(args) > 1 else None)
        body = kwargs.get("json", args[2] if len(args) > 2 else None)
        _check_delivery(self.expected, data, body)
        return super().post(*args, **kwargs)


class _AsyncImageDeliveryHTTPHandler(AsyncHTTPHandler):
    def __init__(self, expected: int, timeout: Any):
        super().__init__(timeout=timeout)
        self.expected = expected

    async def post(self, *args: Any, **kwargs: Any):
        data = kwargs.get("data", args[1] if len(args) > 1 else None)
        body = kwargs.get("json", args[2] if len(args) > 2 else None)
        _check_delivery(self.expected, data, body)
        return await super().post(*args, **kwargs)


@contextmanager
def image_delivery_client(
    provider: str | None,
    messages: list[dict[str, Any]],
    timeout: Any,
    *,
    asynchronous: bool,
) -> Iterator[dict[str, Any]]:
    """Guard DeepSeek's downstream flattening without rewriting the user's route.

    LiteLLM 1.93 strips all DeepSeek images; newer adapters can still strip tool
    images. Inspect the final request, not SDK-formatted input (#5760).
    """
    count = _image_count(messages)
    if provider != "deepseek" or not count:
        yield {}
        return
    client = (
        _AsyncImageDeliveryHTTPHandler(count, timeout)
        if asynchronous
        else _ImageDeliveryHTTPHandler(count, timeout)
    )
    try:
        yield {"client": client}
    finally:
        if not asynchronous:
            client.close()
