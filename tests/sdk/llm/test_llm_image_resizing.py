import base64
import io
from unittest.mock import patch

from PIL import Image
from pydantic import SecretStr

from openhands.sdk.llm import LLM, ImageContent, Message, TextContent
from openhands.sdk.llm.utils.image_resize import maybe_resize_messages_for_provider


def _make_png_data_url(width: int, height: int) -> str:
    image = Image.new("RGB", (width, height), color="red")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _data_url_dimensions(url: str) -> tuple[int, int]:
    _header, _sep, encoded = url.partition(";base64,")
    image_bytes = base64.b64decode(encoded)
    with Image.open(io.BytesIO(image_bytes)) as image:
        return image.size


def _image_urls_from_chat_message(chat_message: dict) -> list[str]:
    return [
        item["image_url"]["url"]
        for item in chat_message["content"]
        if item.get("type") == "image_url"
    ]


def _format_for_provider(
    llm: LLM, messages: list[Message], *, provider: str
) -> list[dict]:
    with (
        patch.object(LLM, "vision_is_active", return_value=True),
        patch.object(LLM, "_infer_litellm_provider", return_value=provider),
    ):
        return llm.format_messages_for_llm(messages)


def test_maybe_resize_messages_for_provider_does_not_mutate_inputs():
    original_url = _make_png_data_url(2400, 1200)
    original_message = Message(
        role="user",
        content=[
            TextContent(text="Describe these images."),
            ImageContent(image_urls=[original_url] * 21),
        ],
    )

    resized_messages = maybe_resize_messages_for_provider(
        [original_message], provider="anthropic", vision_enabled=True
    )

    resized_content = resized_messages[0].content[1]
    assert isinstance(resized_content, ImageContent)
    assert resized_messages[0] is not original_message
    assert _data_url_dimensions(resized_content.image_urls[0]) == (2000, 1000)

    original_content = original_message.content[1]
    assert isinstance(original_content, ImageContent)
    assert original_content.image_urls[0] == original_url


def test_anthropic_many_image_requests_resize_base64_images():
    original_url = _make_png_data_url(2400, 1200)
    message = Message(
        role="user",
        content=[
            TextContent(text="Describe these images."),
            ImageContent(image_urls=[original_url] * 21),
        ],
    )
    llm = LLM(
        model="anthropic/claude-opus-4-6",
        api_key=SecretStr("test-key"),
        usage_id="test-anthropic-many-image",
    )

    formatted = _format_for_provider(llm, [message], provider="anthropic")

    image_urls = _image_urls_from_chat_message(formatted[0])
    assert len(image_urls) == 21
    assert _data_url_dimensions(image_urls[0]) == (2000, 1000)
    original_content = message.content[1]
    assert isinstance(original_content, ImageContent)
    assert original_content.image_urls[0] == original_url


def test_anthropic_exactly_twenty_images_use_standard_limit():
    original_url = _make_png_data_url(8001, 400)
    message = Message(
        role="user",
        content=[
            TextContent(text="Describe these images."),
            ImageContent(image_urls=[original_url] * 20),
        ],
    )
    llm = LLM(
        model="anthropic/claude-opus-4-6",
        api_key=SecretStr("test-key"),
        usage_id="test-anthropic-twenty-images",
    )

    formatted = _format_for_provider(llm, [message], provider="anthropic")

    image_urls = _image_urls_from_chat_message(formatted[0])
    assert len(image_urls) == 20
    assert _data_url_dimensions(image_urls[0]) == (8000, 400)


def test_anthropic_single_image_requests_do_not_resize():
    original_url = _make_png_data_url(2400, 2400)
    message = Message(
        role="user",
        content=[
            TextContent(text="Describe this image."),
            ImageContent(image_urls=[original_url]),
        ],
    )
    llm = LLM(
        model="anthropic/claude-opus-4-6",
        api_key=SecretStr("test-key"),
        usage_id="test-anthropic-single-image",
    )

    formatted = _format_for_provider(llm, [message], provider="anthropic")

    image_urls = _image_urls_from_chat_message(formatted[0])
    assert image_urls == [original_url]
    assert _data_url_dimensions(image_urls[0]) == (2400, 2400)


def test_anthropic_single_image_requests_resize_above_standard_limit():
    original_url = _make_png_data_url(8001, 400)
    message = Message(
        role="user",
        content=[
            TextContent(text="Describe this image."),
            ImageContent(image_urls=[original_url]),
        ],
    )
    llm = LLM(
        model="anthropic/claude-opus-4-6",
        api_key=SecretStr("test-key"),
        usage_id="test-anthropic-single-image-large",
    )

    formatted = _format_for_provider(llm, [message], provider="anthropic")

    image_urls = _image_urls_from_chat_message(formatted[0])
    assert _data_url_dimensions(image_urls[0]) == (8000, 400)


def test_anthropic_many_image_requests_leave_url_images_unchanged():
    image_url = "https://example.com/image.png"
    message = Message(
        role="user",
        content=[
            TextContent(text="Describe these images."),
            ImageContent(image_urls=[image_url] * 21),
        ],
    )
    llm = LLM(
        model="anthropic/claude-opus-4-6",
        api_key=SecretStr("test-key"),
        usage_id="test-anthropic-url-images",
    )

    formatted = _format_for_provider(llm, [message], provider="anthropic")

    assert _image_urls_from_chat_message(formatted[0]) == [image_url] * 21


def test_non_anthropic_many_image_requests_do_not_resize():
    original_url = _make_png_data_url(2400, 1200)
    message = Message(
        role="user",
        content=[
            TextContent(text="Describe these images."),
            ImageContent(image_urls=[original_url] * 25),
        ],
    )
    llm = LLM(
        model="gpt-4o",
        api_key=SecretStr("test-key"),
        usage_id="test-openai-many-image",
    )

    formatted = _format_for_provider(llm, [message], provider="openai")

    image_urls = _image_urls_from_chat_message(formatted[0])
    assert len(image_urls) == 25
    assert _data_url_dimensions(image_urls[0]) == (2400, 1200)
