"""Undecodable images are dropped before the request leaves the SDK.

Prevention pass companion to the run-loop rollback recovery
(``LLMHistoryContentRejectedError``). See
https://github.com/OpenHands/software-agent-sdk/issues/5224 for the masker
bug that produces such payloads today and issue #5225 for the wedge the
rollback recovery mitigates.
"""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from openhands.sdk.llm import LLM, ImageContent, Message, TextContent
from openhands.sdk.llm.utils import image_validation
from openhands.sdk.llm.utils.image_validation import (
    UNDECODABLE_IMAGE_PLACEHOLDER,
    _is_decodable,
    drop_undecodable_images,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
VALID_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
# The exact shape the secret masker produced in the reported incident: the
# angle brackets are not valid base64 characters.
CORRUPTED_URL = VALID_URL.replace("AAAA", "<secret-hidden>", 1)


@pytest.fixture(autouse=True)
def _clear_decode_cache() -> None:
    """Give each test a clean cache — tests share process state otherwise."""
    _is_decodable.cache_clear()


def _image_message(*urls: str) -> Message:
    return Message(
        role="tool",
        tool_call_id="call_1",
        name="browser",
        content=[TextContent(text="screenshot"), ImageContent(image_urls=list(urls))],
    )


# --- ``drop_undecodable_images`` -----------------------------------------


def test_valid_images_pass_through_untouched() -> None:
    messages = [_image_message(VALID_URL)]

    # Identity, not just equality: the common case must not allocate.
    assert drop_undecodable_images(messages) is messages


def test_wrapped_base64_is_not_treated_as_corrupt() -> None:
    """RFC 2045 encoders wrap at 76 chars; those newlines are legal."""
    wrapped = base64.encodebytes(PNG_BYTES).decode("ascii")
    assert "\n" in wrapped

    messages = [_image_message(f"data:image/png;base64,{wrapped}")]

    assert drop_undecodable_images(messages) is messages


def test_correctly_padded_short_payload_is_accepted() -> None:
    # 'YQ==' is the canonical padded encoding of b'a' — exercises padding.
    messages = [_image_message("data:image/png;base64,YQ==")]

    assert drop_undecodable_images(messages) is messages


def test_http_urls_are_left_to_the_provider() -> None:
    messages = [_image_message("https://example.com/screenshot.png")]

    assert drop_undecodable_images(messages) is messages


def test_non_data_url_without_base64_marker_is_left_alone() -> None:
    # A percent-encoded data URL isn't ours to validate.
    messages = [_image_message("data:text/plain,hello%20world")]

    assert drop_undecodable_images(messages) is messages


def test_corrupted_image_is_replaced_with_a_placeholder() -> None:
    result = drop_undecodable_images([_image_message(CORRUPTED_URL)])

    assert not any(isinstance(item, ImageContent) for item in result[0].content)
    texts = [item.text for item in result[0].content if isinstance(item, TextContent)]
    assert texts == ["screenshot", UNDECODABLE_IMAGE_PLACEHOLDER]


def test_only_the_corrupted_url_of_a_pair_is_dropped() -> None:
    result = drop_undecodable_images([_image_message(VALID_URL, CORRUPTED_URL)])

    images = [item for item in result[0].content if isinstance(item, ImageContent)]
    assert [url for image in images for url in image.image_urls] == [VALID_URL]
    # The surviving image is followed by the placeholder for the dropped one.
    assert any(
        isinstance(item, TextContent) and item.text == UNDECODABLE_IMAGE_PLACEHOLDER
        for item in result[0].content
    )


def test_padding_missing_is_dropped() -> None:
    # Correct alphabet but wrong length — b64decode(validate=True) rejects.
    messages = [_image_message("data:image/png;base64,YQ")]

    result = drop_undecodable_images(messages)

    assert not any(isinstance(item, ImageContent) for item in result[0].content)


def test_original_messages_are_not_mutated() -> None:
    original = _image_message(CORRUPTED_URL)

    drop_undecodable_images([original])

    image = original.content[1]
    assert isinstance(image, ImageContent)
    assert image.image_urls == [CORRUPTED_URL]


def test_multiple_messages_only_the_dirty_one_is_rebuilt() -> None:
    clean_a = _image_message(VALID_URL)
    dirty = _image_message(CORRUPTED_URL)
    clean_b = _image_message(VALID_URL)

    result = drop_undecodable_images([clean_a, dirty, clean_b])

    # Non-mutated messages are the same objects.
    assert result[0] is clean_a
    assert result[2] is clean_b
    assert result[1] is not dirty


# --- decode cache --------------------------------------------------------


def test_decode_is_cached_across_calls() -> None:
    _is_decodable.cache_clear()
    with patch.object(
        image_validation.base64, "b64decode", wraps=image_validation.base64.b64decode
    ) as spy:
        assert _is_decodable(VALID_URL) is True
        assert _is_decodable(VALID_URL) is True
        assert _is_decodable(VALID_URL) is True

    assert spy.call_count == 1


# --- integration with the LLM formatters --------------------------------


def test_formatted_chat_request_carries_no_corrupted_image() -> None:
    llm = LLM(model="claude-sonnet-4-5-20250929", usage_id="test-llm")

    with patch.object(LLM, "vision_is_active", return_value=True):
        formatted = llm.format_messages_for_llm([_image_message(CORRUPTED_URL)])

    assert "<secret-hidden>" not in str(formatted)
    assert UNDECODABLE_IMAGE_PLACEHOLDER in str(formatted)


def test_formatted_responses_request_carries_no_corrupted_image() -> None:
    llm = LLM(model="gpt-5", usage_id="test-llm")

    with patch.object(LLM, "vision_is_active", return_value=True):
        _instructions, items = llm.format_messages_for_responses(
            [_image_message(CORRUPTED_URL)]
        )

    assert "<secret-hidden>" not in str(items)
    assert UNDECODABLE_IMAGE_PLACEHOLDER in str(items)


@pytest.mark.asyncio
async def test_async_chat_formatter_also_drops_corrupt_images() -> None:
    llm = LLM(model="claude-sonnet-4-5-20250929", usage_id="test-llm")

    with patch.object(LLM, "vision_is_active", return_value=True):
        formatted = await llm.aformat_messages_for_llm([_image_message(CORRUPTED_URL)])

    assert "<secret-hidden>" not in str(formatted)


@pytest.mark.asyncio
async def test_async_responses_formatter_also_drops_corrupt_images() -> None:
    llm = LLM(model="gpt-5", usage_id="test-llm")

    with patch.object(LLM, "vision_is_active", return_value=True):
        _instructions, items = await llm.aformat_messages_for_responses(
            [_image_message(CORRUPTED_URL)]
        )

    assert "<secret-hidden>" not in str(items)


def test_opt_out_disables_the_check() -> None:
    llm = LLM(
        model="claude-sonnet-4-5-20250929",
        usage_id="test-llm",
        drop_undecodable_images=False,
    )

    with patch.object(LLM, "vision_is_active", return_value=True):
        formatted = llm.format_messages_for_llm([_image_message(CORRUPTED_URL)])

    # With the check disabled the poisoned payload reaches the wire, which is
    # what the run-loop rollback exists to catch.
    assert "<secret-hidden>" in str(formatted)
    assert UNDECODABLE_IMAGE_PLACEHOLDER not in str(formatted)
