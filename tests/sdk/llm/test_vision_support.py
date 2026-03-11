from unittest.mock import patch

import pytest
from pydantic import SecretStr

from openhands.sdk.llm import LLM, ImageContent, Message, TextContent


@pytest.mark.parametrize(
    "model",
    [
        # Plain model names
        "claude-sonnet-4-5-20250929",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
        # With provider/proxy prefixes
        "anthropic/claude-sonnet-4-5-20250929",
        "litellm_proxy/anthropic/claude-sonnet-4-5-20250929",
        "litellm_proxy/gemini-2.5-flash",
        "litellm_proxy/gemini-3-pro-preview",
    ],
)
def test_vision_is_active_supported_models(model):
    # Use real LiteLLM helpers (no patching/mocking). This test validates our
    # vision_is_active detection (prefix stripping + model_info fallback) against
    # LiteLLM's current knowledge base, without provider calls.
    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True


def _collect_image_url_parts(chat_message: dict) -> list[dict]:
    content = chat_message.get("content", [])
    return [
        p
        for p in content
        if isinstance(p, dict)
        and p.get("type") == "image_url"
        and isinstance(p.get("image_url"), dict)
        and p["image_url"].get("url")
    ]


def _has_input_image(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    if item.get("type") != "message":
        return False
    for c in item.get("content", []):
        if isinstance(c, dict) and c.get("type") == "input_image":
            return True
    return False


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-5-20250929",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
    ],
)
def test_chat_serializes_images_when_vision_supported(model):
    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True

    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    formatted = llm.format_messages_for_llm([msg])
    assert isinstance(formatted, list) and len(formatted) == 1

    parts = _collect_image_url_parts(formatted[0])
    assert len(parts) >= 1


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_message_with_image_does_not_enable_vision_for_text_only_model(
    mock_sv, _mock_model_info
):
    # For a model that does not support vision, images should not be serialized.
    llm = LLM(model="text-only-model", api_key=SecretStr("k"), usage_id="t")
    formatted = llm.format_messages_for_llm(
        [
            Message(
                role="user",
                content=[
                    TextContent(text="see image"),
                    ImageContent(image_urls=["https://example.com/image.png"]),
                ],
            )
        ]
    )
    assert isinstance(formatted, list) and len(formatted) == 1
    content = formatted[0]["content"]
    # Expect there to be no image_url entries since model is not vision-capable
    assert all(
        not (
            isinstance(part, dict)
            and part.get("type") == "image_url"
            and isinstance(part.get("image_url"), dict)
            and part["image_url"].get("url")
        )
        for part in content
    )


def test_disable_vision_overrides_litellm_detection():
    """Test that disable_vision=True overrides LiteLLM's vision capability detection.

    This is important for models like glm-4.7 where LiteLLM incorrectly reports
    vision support but the actual API (OpenRouter) only accepts text input.
    """
    # glm-4.7 via OpenRouter is reported by LiteLLM as vision-capable,
    # but we explicitly disable vision to prevent API errors
    llm = LLM(
        model="litellm_proxy/openrouter/z-ai/glm-4.7",
        api_key=SecretStr("k"),
        usage_id="t",
        disable_vision=True,
    )

    # Vision should be disabled despite LiteLLM reporting support
    assert llm.vision_is_active() is False

    # Messages with images should not include image_url parts
    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    formatted = llm.format_messages_for_llm([msg])
    assert isinstance(formatted, list) and len(formatted) == 1

    # Verify no image_url parts in formatted message
    parts = _collect_image_url_parts(formatted[0])
    assert len(parts) == 0


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_message_with_image_in_responses_does_not_include_input_image(
    mock_sv, _mock_model_info
):
    llm = LLM(model="text-only-model", api_key=SecretStr("k"), usage_id="t")

    instructions, input_items = llm.format_messages_for_responses(
        [
            Message(
                role="user",
                content=[
                    TextContent(text="see image"),
                    ImageContent(image_urls=["https://example.com/image.png"]),
                ],
            )
        ]
    )


@pytest.mark.parametrize(
    "model",
    [
        "claude-sonnet-4-5-20250929",
        "gemini-2.5-flash",
        "gemini-3-pro-preview",
    ],
)
def test_responses_serializes_images_when_vision_supported(model):
    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True

    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    instructions, input_items = llm.format_messages_for_responses([msg])
    assert instructions is None or isinstance(instructions, str)

    assert any(_has_input_image(item) for item in input_items)
