import asyncio
import threading
from typing import Any, ClassVar

import pytest
from litellm import ModelResponse
from pydantic import SecretStr

from openhands.sdk.llm import LLM, LLMResponse, Message, RouterLLM, TextContent
from openhands.sdk.llm.utils.runtime_metadata import ModelRuntimeMetadata


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


class SecondaryRouter(RouterLLM):
    router_name: str = "secondary_router"

    def select_llm(self, messages: list[Message]) -> str:
        return "secondary"


def test_router_limits_stay_on_first_llm_after_routing():
    primary_llm = MockLLM(
        model="gpt-4o", api_key=SecretStr("key-1"), usage_id="primary-llm"
    )
    secondary_llm = MockLLM(
        model="gpt-3.5-turbo", api_key=SecretStr("key-2"), usage_id="secondary-llm"
    )
    router = SecondaryRouter(
        llms_for_routing={"primary": primary_llm, "secondary": secondary_llm}
    )

    router.completion(messages=[Message(role="user", content=[TextContent(text="hi")])])

    assert router.active_llm is secondary_llm
    assert router.effective_max_input_tokens == primary_llm.effective_max_input_tokens
    assert router.effective_max_input_tokens != secondary_llm.effective_max_input_tokens
    assert router.vision_is_active() == primary_llm.vision_is_active()


@pytest.mark.parametrize(
    "primary_model, check",
    [
        ("anthropic/claude-sonnet-4-5", LLM.is_caching_prompt_active),
        ("gpt-5", LLM.uses_responses_api),
    ],
)
def test_router_state_and_features_come_from_first_llm(primary_model, check):
    primary_llm = LLM(
        model=primary_model, api_key=SecretStr("key-1"), usage_id="primary-llm"
    )
    secondary_llm = LLM(
        model="gpt-4o-mini", api_key=SecretStr("key-2"), usage_id="secondary-llm"
    )
    router = DummyRouter(
        llms_for_routing={"primary": primary_llm, "secondary": secondary_llm}
    )

    assert router.metrics is primary_llm.metrics
    assert router.telemetry is primary_llm.telemetry
    assert check(primary_llm)
    assert check(router)


class ThreadRecordingRouter(RouterLLM):
    router_name: str = "thread_recording_router"
    select_threads: ClassVar[list[int]] = []

    def select_llm(self, messages: list[Message]) -> str:
        self.select_threads.append(threading.get_ident())
        return "primary"


def test_router_async_paths_select_llm_off_the_event_loop():
    primary_llm = MockLLM(model="gpt-4o", api_key=SecretStr("key"), usage_id="p")
    router = ThreadRecordingRouter(llms_for_routing={"primary": primary_llm})
    msg = [Message(role="user", content=[TextContent(text="hello")])]

    async def call_all() -> int:
        await router.acompletion(messages=msg)
        await router.aresponses(messages=msg)
        await router.agenerate(messages=msg)
        return threading.get_ident()

    loop_thread = asyncio.run(call_all())

    assert len(router.select_threads) == 3
    assert loop_thread not in router.select_threads
    assert router.active_llm is primary_llm


def test_router_own_limits_and_disable_vision_win():
    primary_llm = LLM(model="gpt-4o", api_key=SecretStr("key-1"), usage_id="primary")
    router = DummyRouter(
        llms_for_routing={"primary": primary_llm},
        max_input_tokens=20_000,
        max_output_tokens=1_000,
        disable_vision=True,
    )

    assert primary_llm.vision_is_active()
    assert router.effective_max_input_tokens == 20_000
    assert router.effective_max_output_tokens == 1_000
    assert not router.vision_is_active()


def test_router_without_llms_raises_instead_of_recursing():
    with pytest.raises(ValueError, match="no configured LLMs"):
        DummyRouter()


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

    # generate/agenerate go through the router's own completion methods
    res_gen = router.generate(messages=msg)
    c4 = res_gen.message.content[0]
    assert isinstance(c4, TextContent) and c4.text == "mock_completion"

    async_gen = asyncio.run(router.agenerate(messages=msg))
    c5 = async_gen.message.content[0]
    assert isinstance(c5, TextContent) and c5.text == "mock_acompletion"


def _text_response(text: str, llm: LLM) -> LLMResponse:
    return LLMResponse(
        message=Message(role="assistant", content=[TextContent(text=text)]),
        metrics=llm.metrics.get_snapshot(),
        raw_response=ModelResponse(id="mock-resp"),
    )


class InterceptRouter(DummyRouter):
    def completion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        return _text_response("intercepted", self)

    async def acompletion(self, *args: Any, **kwargs: Any) -> LLMResponse:
        return _text_response("async-intercepted", self)


def test_router_generate_calls_subclass_completion():
    primary = LLM(model="gpt-4o", api_key=SecretStr("key"), usage_id="primary")
    router = InterceptRouter(llms_for_routing={"primary": primary})
    msg = [Message(role="user", content=[TextContent(text="hello")])]

    sync = router.generate(messages=msg)
    c0 = sync.message.content[0]
    assert isinstance(c0, TextContent) and c0.text == "intercepted"

    async_res = asyncio.run(router.agenerate(messages=msg))
    c1 = async_res.message.content[0]
    assert isinstance(c1, TextContent) and c1.text == "async-intercepted"


def test_overlapping_calls_use_the_model_they_selected():
    class RecordingLLM(MockLLM):
        def completion(self, *args: Any, **kwargs: Any) -> LLMResponse:
            self.calls.append(self.usage_id)
            return super().completion(*args, **kwargs)

        calls: ClassVar[list[str]] = []

    class ByMessage(RouterLLM):
        router_name: str = "by_message"

        def select_llm(self, messages: list[Message]) -> str:
            content = messages[0].content[0]
            assert isinstance(content, TextContent)
            return content.text

    RecordingLLM.calls = []
    primary = RecordingLLM(model="gpt-4o", api_key=SecretStr("k1"), usage_id="primary")
    secondary = RecordingLLM(
        model="gpt-4o-mini", api_key=SecretStr("k2"), usage_id="secondary"
    )
    router = ByMessage(llms_for_routing={"primary": primary, "secondary": secondary})
    barrier = threading.Barrier(2)

    def call(name: str) -> None:
        barrier.wait(timeout=2)
        router.completion(
            messages=[Message(role="user", content=[TextContent(text=name)])]
        )

    threads = [
        threading.Thread(target=call, args=(name,)) for name in ("primary", "secondary")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert RecordingLLM.calls.count("primary") == 1
    assert RecordingLLM.calls.count("secondary") == 1


class CountingLLM(LLM):
    calls: int = 0

    def resolve_runtime_metadata(
        self, *, force: bool = False
    ) -> ModelRuntimeMetadata | None:
        self.calls += 1
        return ModelRuntimeMetadata(source=self.model, max_input_tokens=111)

    async def aresolve_runtime_metadata(
        self, *, force: bool = False
    ) -> ModelRuntimeMetadata | None:
        self.calls += 1
        return ModelRuntimeMetadata(source=self.model, max_input_tokens=222)


def test_router_delegation_runtime_metadata_and_tokens():
    """Router resolves metadata for the first configured LLM only."""
    primary_llm = CountingLLM(
        model="gpt-4o",
        api_key=SecretStr("key-1"),
        usage_id="primary-llm",
        max_input_tokens=32768,
        max_output_tokens=4096,
    )
    secondary_llm = CountingLLM(
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

    res_sync = router.resolve_runtime_metadata()
    assert res_sync is not None and res_sync.source == "gpt-4o"
    res_async = asyncio.run(router.aresolve_runtime_metadata())
    assert res_async is not None and res_async.max_input_tokens == 222
    assert primary_llm.calls == 2
    assert secondary_llm.calls == 0

    router.active_llm = secondary_llm
    restored = DummyRouter.model_validate_json(router.model_dump_json())
    first = next(iter(restored.llms_for_routing.values()))
    assert restored.active_llm is not None
    assert restored.fallback_llm is first
    assert restored.fallback_llm is not restored.active_llm


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
