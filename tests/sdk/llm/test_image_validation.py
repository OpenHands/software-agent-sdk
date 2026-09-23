"""Undecodable images are dropped before the request leaves the SDK.

See https://github.com/OpenHands/software-agent-sdk/issues/5225. A corrupted
base64 payload in a persisted observation makes the provider 400 on every
turn rebuilt from history, permanently wedging the conversation.
"""

import base64
from unittest.mock import patch

from openhands.sdk.llm import LLM, ImageContent, Message, TextContent
from openhands.sdk.llm.utils.image_validation import (
    UNDECODABLE_IMAGE_PLACEHOLDER,
    drop_undecodable_images,
)


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM"
    "IQAAAABJRU5ErkJggg=="
)
VALID_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"
# What the secret masker produced in the reported incident.
CORRUPTED_URL = VALID_URL.replace("AAAA", "<secret-hidden>", 1)


def _image_message(*urls: str) -> Message:
    return Message(
        role="tool",
        tool_call_id="call_1",
        name="browser",
        content=[TextContent(text="screenshot"), ImageContent(image_urls=list(urls))],
    )


def test_valid_images_pass_through_untouched():
    messages = [_image_message(VALID_URL)]

    assert drop_undecodable_images(messages) is messages


def test_wrapped_base64_is_not_treated_as_corrupt():
    encoded = base64.encodebytes(PNG_BYTES).decode("ascii")
    messages = [_image_message(f"data:image/png;base64,{encoded}")]

    assert drop_undecodable_images(messages) is messages


def test_http_urls_are_left_to_the_provider():
    messages = [_image_message("https://example.com/screenshot.png")]

    assert drop_undecodable_images(messages) is messages


def test_corrupted_image_is_replaced_with_a_placeholder():
    result = drop_undecodable_images([_image_message(CORRUPTED_URL)])

    assert not any(isinstance(item, ImageContent) for item in result[0].content)
    texts = [item.text for item in result[0].content if isinstance(item, TextContent)]
    assert texts == ["screenshot", UNDECODABLE_IMAGE_PLACEHOLDER]


def test_only_the_corrupted_url_of_a_pair_is_dropped():
    result = drop_undecodable_images([_image_message(VALID_URL, CORRUPTED_URL)])

    images = [item for item in result[0].content if isinstance(item, ImageContent)]
    assert [url for image in images for url in image.image_urls] == [VALID_URL]


def test_original_messages_are_not_mutated():
    original = _image_message(CORRUPTED_URL)

    drop_undecodable_images([original])

    image = original.content[1]
    assert isinstance(image, ImageContent)
    assert image.image_urls == [CORRUPTED_URL]


def test_formatted_chat_request_carries_no_corrupted_image():
    llm = LLM(model="claude-sonnet-4-5-20250929", usage_id="test-llm")

    with patch.object(LLM, "vision_is_active", return_value=True):
        formatted = llm.format_messages_for_llm([_image_message(CORRUPTED_URL)])

    assert "<secret-hidden>" not in str(formatted)
    assert UNDECODABLE_IMAGE_PLACEHOLDER in str(formatted)


def test_formatted_responses_request_carries_no_corrupted_image():
    llm = LLM(model="gpt-5", usage_id="test-llm")

    with patch.object(LLM, "vision_is_active", return_value=True):
        _instructions, items = llm.format_messages_for_responses(
            [_image_message(CORRUPTED_URL)]
        )

    assert "<secret-hidden>" not in str(items)
