"""Cross-package tests for secret masking of opaque tool payloads."""

import base64

from openhands.sdk.conversation.secret_registry import SecretRegistry
from openhands.sdk.llm import ImageContent
from openhands.tools.browser_use.definition import BrowserObservation


def test_preserves_browser_screenshot_payload_and_llm_content():
    payload = base64.b64encode(b"abc").decode()
    registry = SecretRegistry()
    registry.update_secrets({"TOKEN": payload})
    registry.get_secret_value("TOKEN")
    observation = BrowserObservation(screenshot_data=payload)

    masked = registry.mask_secrets_in_model(observation)

    assert masked.screenshot_data == payload
    assert masked.screenshot_data is not None
    assert base64.b64decode(masked.screenshot_data, validate=True) == b"abc"
    llm_content = masked.to_llm_content[-1]
    assert isinstance(llm_content, ImageContent)
    assert llm_content.image_urls == [f"data:image/png;base64,{payload}"]
