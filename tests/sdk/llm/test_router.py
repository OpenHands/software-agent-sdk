import asyncio
from typing import Any

import pytest
from litellm import ModelResponse
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, RouterLLM, TextContent


class DummyRouter(RouterLLM):
    """Concrete RouterLLM for testing delegation."""

    router_name: str = "dummy_router"

    def select_llm(self, messages: list[Message]) -> str:
        return "primary"


class ExtendedLLM(LLM):
    """LLM subclass with additional custom capabilities."""

    custom_attribute: str = "custom_value"

    def custom_capability(self) -> str:
        return "capability_ok"


def test_router_delegation_fallback_and_properties():
    """RouterLLM provides typed fallback and delegates LLM capabilities."""
    primary_llm = ExtendedLLM(
        model="gpt-4o",
        api_key=SecretStr("key-1"),
        usage_id="primary-llm",
    )
    secondary_llm = ExtendedLLM(
        model="gpt-4o-mini",
        api_key=SecretStr("key-2"),
        usage_id="secondary-llm",
        custom_attribute="secondary_value",
    )

    router = DummyRouter(
        llms_for_routing={"primary": primary_llm, "secondary": secondary_llm}
    )

    # Initial fallback is the first configured LLM
    assert router.fallback_llm is primary_llm

    # Typed properties delegation
    sample_msgs = [Message(role="user", content=[TextContent(text="hi")])]
    assert router.get_token_count(sample_msgs) > 0
    assert router.vision_is_active() == primary_llm.vision_is_active()
    assert router.effective_max_input_tokens == primary_llm.effective_max_input_tokens
    assert router.effective_max_output_tokens == primary_llm.effective_max_output_tokens
    assert router.metrics is not None

    # Dynamic fallback delegation for unhandled custom attributes/methods
    assert router.custom_attribute == "custom_value"
    assert router.custom_capability() == "capability_ok"

    # Private attributes are not delegated to fallback
    with pytest.raises(AttributeError):
        _ = router._non_existent_private_attr

    # Non-existent public attributes raise AttributeError from fallback
    with pytest.raises(AttributeError):
        _ = router.non_existent_public_attribute


class MockLLM(LLM):
    """LLM subclass with mockable call tracking."""

    def completion(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="mock_completion")],
            ),
            metrics=self.metrics.get_snapshot(),
            raw_response=ModelResponse(id="mock-resp"),
        )

    async def acompletion(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="mock_acompletion")],
            ),
            metrics=self.metrics.get_snapshot(),
            raw_response=ModelResponse(id="mock-resp"),
        )

    def responses(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="mock_responses")],
            ),
            metrics=self.metrics.get_snapshot(),
            raw_response=ModelResponse(id="mock-resp"),
        )

    async def aresponses(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="mock_aresponses")],
            ),
            metrics=self.metrics.get_snapshot(),
            raw_response=ModelResponse(id="mock-resp"),
        )

    def generate(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="mock_generate")],
            ),
            metrics=self.metrics.get_snapshot(),
            raw_response=ModelResponse(id="mock-resp"),
        )

    async def agenerate(self, *args, **kwargs) -> LLMResponse:
        return LLMResponse(
            message=Message(
                role="assistant",
                content=[TextContent(text="mock_agenerate")],
            ),
            metrics=self.metrics.get_snapshot(),
            raw_response=ModelResponse(id="mock-resp"),
        )


def test_router_delegation_sync_and_async():
    """RouterLLM delegates completion, responses, and generation (sync & async)."""
    primary_llm = MockLLM(
        model="gpt-4o",
        api_key=SecretStr("key-1"),
        usage_id="primary-llm",
    )
    secondary_llm = MockLLM(
        model="gpt-4o-mini",
        api_key=SecretStr("key-2"),
        usage_id="secondary-llm",
    )

    router = DummyRouter(
        llms_for_routing={"primary": primary_llm, "secondary": secondary_llm}
    )

    msg = [Message(role="user", content=[TextContent(text="hello")])]

    # Sync completion
    res_comp = router.completion(messages=msg)
    c0 = res_comp.message.content[0]
    assert isinstance(c0, TextContent) and c0.text == "mock_completion"
    assert router.active_llm is primary_llm

    # Sync responses
    res_resp = router.responses(messages=msg)
    c1 = res_resp.message.content[0]
    assert isinstance(c1, TextContent) and c1.text == "mock_responses"

    # Async acompletion
    async_comp = asyncio.run(router.acompletion(messages=msg))
    c2 = async_comp.message.content[0]
    assert isinstance(c2, TextContent) and c2.text == "mock_acompletion"

    # Async aresponses
    async_resp = asyncio.run(router.aresponses(messages=msg))
    c3 = async_resp.message.content[0]
    assert isinstance(c3, TextContent) and c3.text == "mock_aresponses"

    # Sync generate
    res_gen = router.generate(messages=msg)
    c4 = res_gen.message.content[0]
    assert isinstance(c4, TextContent) and c4.text == "mock_generate"

    # Async agenerate
    async_gen = asyncio.run(router.agenerate(messages=msg))
    c5 = async_gen.message.content[0]
    assert isinstance(c5, TextContent) and c5.text == "mock_agenerate"


def test_router_delegation_runtime_metadata_and_tokens():
    """RouterLLM delegates metadata resolution and effective token limits."""
    primary_llm = ExtendedLLM(
        model="gpt-4o",
        api_key=SecretStr("key-1"),
        usage_id="primary-llm",
        max_input_tokens=32768,
        max_output_tokens=4096,
    )
    secondary_llm = ExtendedLLM(
        model="gpt-4o-mini",
        api_key=SecretStr("key-2"),
        usage_id="secondary-llm",
        max_input_tokens=16384,
        max_output_tokens=2048,
    )

    router = DummyRouter(
        llms_for_routing={"primary": primary_llm, "secondary": secondary_llm}
    )

    assert router.effective_max_input_tokens == 32768
    assert router.effective_max_output_tokens == 4096

    # Metadata resolution succeeds across routes
    res_sync = router.resolve_runtime_metadata()
    assert res_sync is None or res_sync.max_input_tokens is not None

    res_async = asyncio.run(router.aresolve_runtime_metadata())
    assert res_async is None or res_async.max_input_tokens is not None


def test_router_unknown_model_selection_raises_key_error():
    """RouterLLM raises KeyError when select_llm returns an unconfigured key."""

    class InvalidRouter(RouterLLM):
        router_name: str = "invalid_router"

        def select_llm(self, messages: list[Message]) -> str:
            return "non_existent"

    primary_llm = ExtendedLLM(
        model="gpt-4o",
        api_key=SecretStr("key-1"),
        usage_id="primary-llm",
    )
    router = InvalidRouter(llms_for_routing={"primary": primary_llm})
    sample_msgs = [Message(role="user", content=[TextContent(text="hi")])]

    with pytest.raises(KeyError, match="selected unknown LLM 'non_existent'"):
        router.completion(messages=sample_msgs)


def test_router_delegation_supports_fallback_custom_getattr():
    """RouterLLM.__getattr__ delegates to fallback __getattr__ when present."""

    class DynamicLLM(ExtendedLLM):
        def __getattr__(self, name: str) -> Any:
            if name == "dynamic_field":
                return "dynamic_ok"
            return super().__getattr__(name)  # pyright: ignore[reportAttributeAccessIssue]

    primary_llm = DynamicLLM(
        model="gpt-4o",
        api_key=SecretStr("key-1"),
        usage_id="primary-llm",
    )
    router = DummyRouter(llms_for_routing={"primary": primary_llm})
    assert router.dynamic_field == "dynamic_ok"
