"""The native DeepSeek image-loss guard reports its own rejection to the caller."""

import httpx
import pytest
from litellm.exceptions import InternalServerError

from openhands.sdk.llm.exceptions import LLMBadRequestError
from openhands.sdk.llm.utils.image_delivery import image_delivery_client


URL = "http://127.0.0.1:9/v1/chat/completions"
IMAGE_PART = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}
WITH_IMAGE = [{"role": "user", "content": [{"type": "text", "text": "x"}, IMAGE_PART]}]
FLATTENED = {"model": "deepseek-chat", "messages": [{"role": "user", "content": "x"}]}


def as_provider_error(error: Exception) -> InternalServerError:
    """What LiteLLM turns an exception from its HTTP client into."""
    return InternalServerError(
        message=f"DeepseekException - {error}",
        llm_provider="deepseek",
        model="deepseek-chat",
    )


def test_image_loss_surfaces_as_bad_request_through_litellm_wrapping():
    with pytest.raises(LLMBadRequestError, match="discarded image content"):
        with image_delivery_client(
            "deepseek", WITH_IMAGE, 5, asynchronous=False
        ) as guarded:
            try:
                guarded["client"].post(URL, json=FLATTENED)
            except Exception as error:
                raise as_provider_error(error) from error


@pytest.mark.asyncio
async def test_async_image_loss_surfaces_as_bad_request_through_litellm_wrapping():
    with pytest.raises(LLMBadRequestError, match="discarded image content"):
        with image_delivery_client(
            "deepseek", WITH_IMAGE, 5, asynchronous=True
        ) as guarded:
            try:
                await guarded["client"].post(URL, json=FLATTENED)
            except Exception as error:
                raise as_provider_error(error) from error
            finally:
                await guarded["client"].close()


def test_provider_failure_with_images_delivered_is_left_alone():
    """A real provider 500 must stay a retryable provider error."""
    outage = as_provider_error(RuntimeError("upstream exploded"))
    with pytest.raises(InternalServerError) as raised:
        with image_delivery_client(
            "deepseek", WITH_IMAGE, 5, asynchronous=False
        ) as guarded:
            # Port 9 (discard) refuses the connection: the guard let the
            # complete body through and the failure came from the network.
            with pytest.raises(httpx.ConnectError):
                guarded["client"].post(
                    URL, json={"model": "deepseek-chat", "messages": WITH_IMAGE}
                )
            raise outage
    assert raised.value is outage
