"""Tests for inlining http(s) image URLs as base64 ``data:`` URLs."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, ImageContent, Message, TextContent
from openhands.sdk.llm.utils import image_inline
from openhands.sdk.llm.utils.image_inline import (
    _CACHE,
    maybe_inline_image_urls,
)
from openhands.sdk.llm.utils.model_features import get_features


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4"
    b"\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _CACHE.clear()
    yield
    _CACHE.clear()


def _stub_get(url: str, *, body: bytes = _TINY_PNG, content_type: str = "image/png"):
    """Return a context manager that mocks httpx.Client.stream for one URL."""

    class _StubResponse:
        def __init__(self) -> None:
            self.headers = {
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            }

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self, chunk_size: int = 65536):
            yield body

    class _StubClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> _StubClient:
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def stream(self, method: str, request_url: str):
            assert method == "GET"
            assert request_url == url

            class _Stream:
                def __enter__(self) -> _StubResponse:
                    return _StubResponse()

                def __exit__(self, *exc: Any) -> None:
                    return None

            return _Stream()

    return patch.object(image_inline.httpx, "Client", _StubClient)


def test_no_op_when_inline_not_required():
    msg = Message(
        role="user",
        content=[ImageContent(image_urls=["https://example.com/x.png"])],
    )
    out = maybe_inline_image_urls([msg], inline_required=False, vision_enabled=True)
    assert out == [msg] or out == [msg]
    img = out[0].content[0]
    assert isinstance(img, ImageContent)
    assert img.image_urls == ["https://example.com/x.png"]


def test_no_op_when_vision_disabled():
    msg = Message(
        role="user",
        content=[ImageContent(image_urls=["https://example.com/x.png"])],
    )
    out = maybe_inline_image_urls([msg], inline_required=True, vision_enabled=False)
    img = out[0].content[0]
    assert isinstance(img, ImageContent)
    assert img.image_urls == ["https://example.com/x.png"]


def test_inlines_http_url_to_base64_data_url():
    url = "https://example.com/x.png"
    msg = Message(role="user", content=[ImageContent(image_urls=[url])])

    with _stub_get(url):
        out = maybe_inline_image_urls([msg], inline_required=True, vision_enabled=True)

    img = out[0].content[0]
    assert isinstance(img, ImageContent)
    expected = "data:image/png;base64," + base64.b64encode(_TINY_PNG).decode("ascii")
    assert img.image_urls == [expected]
    # Input must not be mutated.
    original = msg.content[0]
    assert isinstance(original, ImageContent)
    assert original.image_urls == [url]


def test_data_url_passes_through_unchanged():
    data_url = "data:image/png;base64,AAAA"
    msg = Message(role="user", content=[ImageContent(image_urls=[data_url])])

    # No mock needed — must not perform any network call.
    out = maybe_inline_image_urls([msg], inline_required=True, vision_enabled=True)

    img = out[0].content[0]
    assert isinstance(img, ImageContent)
    assert img.image_urls == [data_url]


def test_fetch_failure_falls_back_to_original_url():
    url = "https://example.com/broken.png"
    msg = Message(role="user", content=[ImageContent(image_urls=[url])])

    class _BoomClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def stream(self, method: str, request_url: str):
            raise httpx.ConnectError("boom")

    with patch.object(image_inline.httpx, "Client", _BoomClient):
        out = maybe_inline_image_urls([msg], inline_required=True, vision_enabled=True)

    img = out[0].content[0]
    assert isinstance(img, ImageContent)
    assert img.image_urls == [url]


def test_cache_reuses_result_across_calls():
    url = "https://example.com/x.png"
    msg1 = Message(role="user", content=[ImageContent(image_urls=[url])])
    msg2 = Message(role="user", content=[ImageContent(image_urls=[url])])

    call_counter = {"n": 0}

    class _CountingClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc: Any) -> None:
            return None

        def stream(self, method: str, request_url: str):
            call_counter["n"] += 1

            class _Resp:
                def __init__(self) -> None:
                    self.headers = {
                        "Content-Type": "image/png",
                        "Content-Length": str(len(_TINY_PNG)),
                    }

                def raise_for_status(self) -> None:
                    return None

                def iter_bytes(self, chunk_size: int = 65536):
                    yield _TINY_PNG

            class _Stream:
                def __enter__(self) -> _Resp:
                    return _Resp()

                def __exit__(self, *exc: Any) -> None:
                    return None

            return _Stream()

    with patch.object(image_inline.httpx, "Client", _CountingClient):
        maybe_inline_image_urls([msg1], inline_required=True, vision_enabled=True)
        maybe_inline_image_urls([msg2], inline_required=True, vision_enabled=True)

    assert call_counter["n"] == 1


def test_model_features_marks_kimi_k2_6():
    assert get_features("moonshot/kimi-k2.6").requires_inline_image_data is True
    # The substring matcher also catches the same model when wrapped by the
    # litellm_proxy prefix — that is the path used in production runs.
    assert (
        get_features("litellm_proxy/moonshot/kimi-k2.6").requires_inline_image_data
        is True
    )


def test_model_features_does_not_mark_other_moonshot_models():
    # Only kimi-k2.6 is in the list today; sibling Kimi releases must not
    # be flagged so they continue to behave like before.
    assert get_features("moonshot/kimi-k2.5").requires_inline_image_data is False
    assert get_features("moonshot/kimi-k2-thinking").requires_inline_image_data is False
    # Hosted Kimi K2.6 on other clouds (bedrock/fireworks/azure) accepts URLs
    # and must not be auto-inlined.
    assert (
        get_features("bedrock/moonshotai.kimi-k2.5").requires_inline_image_data is False
    )
    assert (
        get_features(
            "fireworks_ai/accounts/fireworks/models/kimi-k2.6"
        ).requires_inline_image_data
        is False
    )


def test_llm_inline_image_urls_override_wins_over_capability():
    """The explicit LLM field must override the capability default."""
    url = "https://example.com/x.png"
    llm = LLM(
        model="anthropic/claude-sonnet-4-6",
        api_key=SecretStr("test-key"),
        inline_image_urls=True,
        usage_id="test",
    )
    message = Message(
        role="user",
        content=[TextContent(text="hi"), ImageContent(image_urls=[url])],
    )

    with (
        patch.object(LLM, "vision_is_active", return_value=True),
        _stub_get(url),
    ):
        formatted = llm.format_messages_for_llm([message])

    image_blocks = [
        item for item in formatted[0]["content"] if item.get("type") == "image_url"
    ]
    assert image_blocks
    sent_url = image_blocks[0]["image_url"]["url"]
    assert sent_url.startswith("data:image/png;base64,")


def test_llm_kimi_k2_6_auto_inlines_without_override():
    """No override needed when the model is in the capability list."""
    url = "https://example.com/x.png"
    llm = LLM(
        model="litellm_proxy/moonshot/kimi-k2.6",
        api_key=SecretStr("test-key"),
        usage_id="test",
    )
    message = Message(
        role="user",
        content=[ImageContent(image_urls=[url])],
    )

    with (
        patch.object(LLM, "vision_is_active", return_value=True),
        _stub_get(url),
    ):
        formatted = llm.format_messages_for_llm([message])

    image_blocks = [
        item for item in formatted[0]["content"] if item.get("type") == "image_url"
    ]
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_llm_inline_image_urls_false_disables_capability_default():
    """``inline_image_urls=False`` opts out even when the model would auto-opt-in."""
    url = "https://example.com/x.png"
    llm = LLM(
        model="litellm_proxy/moonshot/kimi-k2.6",
        api_key=SecretStr("test-key"),
        inline_image_urls=False,
        usage_id="test",
    )
    message = Message(
        role="user",
        content=[ImageContent(image_urls=[url])],
    )

    class _ShouldNotBeCalled:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError(
                "httpx.Client must not be constructed when inline_image_urls=False"
            )

    with (
        patch.object(LLM, "vision_is_active", return_value=True),
        patch.object(image_inline.httpx, "Client", _ShouldNotBeCalled),
    ):
        formatted = llm.format_messages_for_llm([message])

    image_blocks = [
        item for item in formatted[0]["content"] if item.get("type") == "image_url"
    ]
    assert image_blocks[0]["image_url"]["url"] == url
