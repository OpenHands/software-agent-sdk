"""Routing behavior shared by the sync and async RouterLLM entry points."""

import asyncio
from collections.abc import Sequence

import pytest
from litellm.types.utils import Choices, Message as LiteLLMMessage, ModelResponse
from pydantic import Field

from openhands.sdk.llm import LLM, LLMResponse, Message, TextContent
from openhands.sdk.llm.llm import LLMCallContext
from openhands.sdk.llm.router import RouterLLM
from openhands.sdk.llm.streaming import AnyTokenCallbackType, TokenCallbackType
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.tool import Action, ToolDefinition


class _RouterStubAction(Action):
    text: str


class _RouterStubTool(ToolDefinition):
    @classmethod
    def create(cls, *args, **kwargs) -> Sequence["_RouterStubTool"]:
        return [cls(description="stub", action_type=_RouterStubAction)]


def _response(model: str) -> LLMResponse:
    return LLMResponse(
        message=Message(role="assistant", content=[TextContent(text=model)]),
        metrics=MetricsSnapshot(
            model_name=model,
            accumulated_cost=0.0,
            max_budget_per_task=None,
            accumulated_token_usage=TokenUsage(model=model),
        ),
        raw_response=ModelResponse(
            id=f"{model}-id",
            choices=[
                Choices(
                    finish_reason="stop",
                    index=0,
                    message=LiteLLMMessage(content=model, role="assistant"),
                )
            ],
            created=0,
            model=model,
            object="chat.completion",
        ),
    )


class _RecordingLLM(LLM):
    """Child LLM that records its calls instead of contacting a provider."""

    calls: list[dict] = Field(default_factory=list)
    barrier: asyncio.Barrier | None = None

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        self.calls.append(
            {
                "kind": "completion",
                "messages": messages,
                "tools": tools,
                "add_security_risk_prediction": add_security_risk_prediction,
                "on_token": on_token,
                "call_context": call_context,
                **kwargs,
            }
        )
        return _response(self.model)

    async def acompletion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs,
    ) -> LLMResponse:
        if self.barrier is not None:
            # Hold every caller here until all of them have been dispatched, so
            # the assertions below run with both requests genuinely in flight.
            await self.barrier.wait()
        self.calls.append(
            {
                "kind": "acompletion",
                "messages": messages,
                "tools": tools,
                "add_security_risk_prediction": add_security_risk_prediction,
                "on_token": on_token,
                "call_context": call_context,
                **kwargs,
            }
        )
        return _response(self.model)


class _KeyedRouter(RouterLLM):
    """Routes to the child named by the first message's text."""

    def select_llm(self, messages: list[Message]) -> str:
        content = messages[0].content[0]
        assert isinstance(content, TextContent)
        return content.text


def _message(text: str) -> Message:
    return Message(role="user", content=[TextContent(text=text)])


def _router(**children: LLM) -> _KeyedRouter:
    return _KeyedRouter(usage_id="router", llms_for_routing=dict(children))


@pytest.fixture
def children() -> dict[str, _RecordingLLM]:
    return {
        "primary": _RecordingLLM(model="primary-model", usage_id="primary"),
        "secondary": _RecordingLLM(model="secondary-model", usage_id="secondary"),
    }


async def test_acompletion_routes_to_the_selected_child(children):
    router = _router(**children)

    result = await router.acompletion(messages=[_message("secondary")])

    assert result.raw_response.model == "secondary-model"
    assert [call["kind"] for call in children["secondary"].calls] == ["acompletion"]
    assert children["primary"].calls == []


def test_completion_routes_to_the_selected_child(children):
    router = _router(**children)

    result = router.completion(messages=[_message("secondary")])

    assert result.raw_response.model == "secondary-model"
    assert [call["kind"] for call in children["secondary"].calls] == ["completion"]
    assert children["primary"].calls == []


async def test_sync_and_async_reach_the_same_child(children):
    router = _router(**children)

    sync_result = router.completion(messages=[_message("primary")])
    async_result = await router.acompletion(messages=[_message("primary")])

    assert sync_result.raw_response.model == async_result.raw_response.model
    assert [call["kind"] for call in children["primary"].calls] == [
        "completion",
        "acompletion",
    ]


async def test_concurrent_async_calls_do_not_cross_dispatch():
    barrier = asyncio.Barrier(2)
    router = _router(
        primary=_RecordingLLM(
            model="primary-model", usage_id="primary", barrier=barrier
        ),
        secondary=_RecordingLLM(
            model="secondary-model", usage_id="secondary", barrier=barrier
        ),
    )

    primary, secondary = await asyncio.gather(
        router.acompletion(messages=[_message("primary")]),
        router.acompletion(messages=[_message("secondary")]),
    )

    assert primary.raw_response.model == "primary-model"
    assert secondary.raw_response.model == "secondary-model"


@pytest.mark.parametrize("is_async", [False, True])
async def test_unknown_selector_key_fails_deterministically(children, is_async):
    router = _router(**children)

    with pytest.raises(ValueError, match="not a key in llms_for_routing"):
        if is_async:
            await router.acompletion(messages=[_message("missing")])
        else:
            router.completion(messages=[_message("missing")])

    assert children["primary"].calls == []
    assert children["secondary"].calls == []


@pytest.mark.parametrize("is_async", [False, True])
async def test_call_arguments_reach_the_selected_child(children, is_async):
    router = _router(**children)
    tools = _RouterStubTool.create()
    context = LLMCallContext()

    def on_token(chunk):
        return None

    messages = [_message("primary")]
    if is_async:
        await router.acompletion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=True,
            on_token=on_token,
            call_context=context,
            temperature=0.25,
        )
    else:
        router.completion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=True,
            on_token=on_token,
            call_context=context,
            temperature=0.25,
        )

    (call,) = children["primary"].calls
    assert call["tools"] is tools
    assert call["add_security_risk_prediction"] is True
    assert call["on_token"] is on_token
    assert call["call_context"] is context
    assert call["temperature"] == 0.25


async def test_active_llm_still_tracks_the_last_selection(children):
    router = _router(**children)

    await router.acompletion(messages=[_message("secondary")])

    assert router.active_llm is children["secondary"]
