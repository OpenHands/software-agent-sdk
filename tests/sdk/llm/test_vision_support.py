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
@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": True},
)
@patch("openhands.sdk.llm.llm.supports_vision")
def test_vision_is_active_supported_models(
    mock_supports_vision, _mock_model_info, model
):
    # Simulate LiteLLM saying these models support vision
    def _sv(name: str) -> bool:
        n = (name or "").lower()
        return (
            "claude-sonnet-4-5" in n or "gemini-2.5-flash" in n or "gemini-3-pro" in n
        )

    mock_supports_vision.side_effect = _sv

    llm = LLM(model=model, api_key=SecretStr("k"), usage_id="t")
    assert llm.vision_is_active() is True


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_message_with_image_forces_vision_true_in_chat(mock_sv, _mock_model_info):
    # Even if the model is not vision-capable per LiteLLM, a message containing
    # ImageContent should be serialized with the image parts preserved.
    llm = LLM(model="text-only-model", api_key=SecretStr("k"), usage_id="t")

    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    formatted = llm.format_messages_for_llm([msg])
    assert isinstance(formatted, list) and len(formatted) == 1
    content = formatted[0]["content"]
    # Expect at least one image_url entry present
    assert any(
        isinstance(part, dict)
        and part.get("type") == "image_url"
        and isinstance(part.get("image_url"), dict)
        and part["image_url"].get("url")
        for part in content
    )


@patch(
    "openhands.sdk.llm.llm.get_litellm_model_info",
    return_value={"supports_vision": False},
)
@patch("openhands.sdk.llm.llm.supports_vision", return_value=False)
def test_message_with_image_in_responses_includes_input_image(
    mock_sv, _mock_model_info
):
    llm = LLM(model="text-only-model", api_key=SecretStr("k"), usage_id="t")

    msg = Message(
        role="user",
        content=[
            TextContent(text="see image"),
            ImageContent(image_urls=["https://example.com/image.png"]),
        ],
    )
    instructions, input_items = llm.format_messages_for_responses([msg])

    # Expect an input message with input_image parts even though supports_vision=False
    assert instructions is None or isinstance(instructions, str)
    assert any(
        isinstance(item, dict)
        and item.get("type") == "message"
        and any(
            c.get("type") == "input_image"
            for c in item.get("content", [])
            if isinstance(c, dict)
        )
        for item in input_items
    )
