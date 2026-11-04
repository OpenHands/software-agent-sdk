import asyncio
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest
from litellm.types.utils import ModelResponse
from pydantic import PrivateAttr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.sdk.llm import (
    LLM,
    ImageContent,
    LLMCallContext,
    LLMResponse,
    Message,
    MessageToolCall,
    TextContent,
)
from openhands.sdk.llm.router.impl.multimodal import MultimodalRouter
from openhands.sdk.llm.streaming import AnyTokenCallbackType, TokenCallbackType
from openhands.sdk.llm.utils.metrics import MetricsSnapshot, TokenUsage
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import ToolDefinition
from openhands.sdk.tool.builtins.vision_inspect import (
    VISION_PROFILE_USAGE_PREFIX,
    VisionInspectObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.llm.llm import LLMCallContext


class CapturingTestLLM(TestLLM):
    __test__ = False
    _calls: list[tuple[list[Message], list]] = PrivateAttr(default_factory=list)

    @property
    def calls(self) -> list[tuple[list[Message], list]]:
        return self._calls

    def completion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: TokenCallbackType | None = None,
        call_context: "LLMCallContext | None" = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._calls.append((messages, list(tools or [])))
        return super().completion(
            messages=messages,
            tools=tools,
            add_security_risk_prediction=add_security_risk_prediction,
            on_token=on_token,
            call_context=call_context,
            **kwargs,
        )


def _image_message() -> Message:
    return Message(
        role="user",
        content=[
            TextContent(text="Can you see this screenshot?"),
            ImageContent(image_urls=["https://example.com/screenshot.png"]),
        ],
    )


def _agent_response_text(conversation: LocalConversation) -> str:
    agent_messages = [
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "agent"
    ]
    assert len(agent_messages) == 1
    content = agent_messages[0].llm_message.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _last_agent_response_text(conversation: LocalConversation) -> str:
    agent_messages = [
        event
        for event in conversation.state.events
        if isinstance(event, MessageEvent) and event.source == "agent"
    ]
    content = agent_messages[-1].llm_message.content[0]
    assert isinstance(content, TextContent)
    return content.text


def test_image_input_to_non_multimodal_model_returns_capability_message(monkeypatch):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    llm = TestLLM.from_messages(
        [],
        model="litellm_proxy/openrouter/z-ai/glm-4.7",
        disable_vision=True,
    )
    conversation = Conversation(agent=Agent(llm=llm, tools=[]))

    conversation.send_message(_image_message())
    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm.call_count == 0
    text = _agent_response_text(conversation)
    assert "I received your image" in text
    assert "does not support image understanding" in text
    assert "litellm_proxy/openrouter/z-ai/glm-4.7" in text


@pytest.mark.asyncio
async def test_async_image_input_to_non_multimodal_model_returns_capability_message(
    monkeypatch,
):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    llm = TestLLM.from_messages(
        [],
        model="litellm_proxy/openrouter/z-ai/glm-4.7",
        disable_vision=True,
    )
    conversation = Conversation(agent=Agent(llm=llm, tools=[]))

    conversation.send_message(_image_message())
    await conversation.arun()

    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    assert llm.call_count == 0
    text = _agent_response_text(conversation)
    assert "I received your image" in text
    assert "does not support image understanding" in text


def test_image_input_guard_does_not_preempt_multimodal_router(monkeypatch):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    primary = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="router handled image")])],
        model="claude-sonnet-4-5-20250929",
    )
    secondary = TestLLM.from_messages([], model="text-only-model", disable_vision=True)
    router = MultimodalRouter(
        llms_for_routing={
            MultimodalRouter.PRIMARY_MODEL_KEY: primary,
            MultimodalRouter.SECONDARY_MODEL_KEY: secondary,
        }
    )
    conversation = Conversation(agent=Agent(llm=router, tools=[]))

    conversation.send_message(_image_message())
    conversation.run()

    assert primary.call_count == 1
    assert secondary.call_count == 0
    assert _agent_response_text(conversation) == "router handled image"


def test_nonvision_model_can_use_vision_profile_tool(monkeypatch):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: True
    )
    monkeypatch.setattr(
        "openhands.sdk.tool.builtins.vision_inspect._candidate_vision_profiles",
        lambda: ["vision-profile"],
    )

    parent = cast(
        CapturingTestLLM,
        CapturingTestLLM.from_messages(
            [
                Message(
                    role="assistant",
                    content=[TextContent(text="I will inspect the image.")],
                    tool_calls=[
                        MessageToolCall(
                            id="call_vision",
                            name="inspect_image_with_vision",
                            arguments=(
                                '{"image_index": 0, "question": "What is shown?"}'
                            ),
                            origin="completion",
                        )
                    ],
                ),
                Message(
                    role="assistant",
                    content=[TextContent(text="The image shows a cat.")],
                ),
            ],
            model="text-only-model",
            disable_vision=True,
        ),
    )
    vision = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="It shows a cat.")])],
        model="gpt-4o",
        base_url="https://vision.example.test/",
    )

    def fake_get_or_create_profile_llm(self, profile_name, usage_id):
        assert profile_name == "vision-profile"
        assert usage_id == f"{VISION_PROFILE_USAGE_PREFIX}:vision-profile"
        return vision

    monkeypatch.setattr(
        LocalConversation,
        "get_or_create_profile_llm",
        fake_get_or_create_profile_llm,
    )

    conversation = Conversation(agent=Agent(llm=parent, tools=[]))
    conversation.send_message(_image_message())
    conversation.run()

    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
    assert parent.call_count == 2
    assert vision.call_count == 1
    assert _last_agent_response_text(conversation) == "The image shows a cat."

    first_parent_messages, first_parent_tools = parent.calls[0]
    latest_user = next(
        message for message in reversed(first_parent_messages) if message.role == "user"
    )
    assert not latest_user.contains_image
    assert any(
        isinstance(content, TextContent)
        and "inspect_image_with_vision" in content.text
        and "image_index=0" in content.text
        for content in latest_user.content
    )
    assert any(tool.name == "inspect_image_with_vision" for tool in first_parent_tools)

    observations = [
        event
        for event in conversation.state.events
        if isinstance(event, ObservationEvent)
        and event.tool_name == "inspect_image_with_vision"
    ]
    assert len(observations) == 1
    observation = cast(VisionInspectObservation, observations[0].observation)
    assert observation.base_url == "https://vision.example.test/"
    observation_content = observation.content[0]
    assert isinstance(observation_content, TextContent)
    assert "It shows a cat." in observation_content.text


def test_profile_helper_registers_auxiliary_vision_llm(monkeypatch):
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    parent = TestLLM.from_messages([], model="text-only-model", disable_vision=True)
    conversation = Conversation(agent=Agent(llm=parent, tools=[]))
    conversation._ensure_agent_ready()

    vision = TestLLM.from_messages(
        [Message(role="assistant", content=[TextContent(text="vision answer")])],
        model="gpt-4o",
    )
    monkeypatch.setattr(
        conversation._profile_store,
        "load",
        lambda profile_name, cipher=None: vision,
    )

    usage_id = f"{VISION_PROFILE_USAGE_PREFIX}:vision-profile"
    loaded = conversation.get_or_create_profile_llm("vision-profile", usage_id)

    assert loaded is conversation.llm_registry.get(usage_id)
    assert conversation.state.stats.usage_to_metrics[usage_id] is loaded.metrics


class _ImageInjectingLLM(LLM):
    """LLM whose first call injects an image user message mid-step."""

    __test__ = False
    _convo_box: list[LocalConversation] = PrivateAttr(default_factory=list)
    _calls: list[list[tuple[str, bool]]] = PrivateAttr(default_factory=list)

    def uses_responses_api(self) -> bool:
        return False

    async def acompletion(
        self,
        messages: list[Message],
        tools: Sequence[ToolDefinition] | None = None,
        add_security_risk_prediction: bool = False,
        on_token: AnyTokenCallbackType | None = None,
        call_context: LLMCallContext | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self._calls.append([(m.role, m.contains_image) for m in messages])
        if len(self._calls) == 1:
            convo = self._convo_box[0]

            def _send() -> None:
                convo.send_message(_image_message())

            sender = threading.Thread(target=_send)
            sender.start()
            await asyncio.to_thread(sender.join)
        return LLMResponse(
            message=Message(role="assistant", content=[TextContent(text="done")]),
            metrics=MetricsSnapshot(
                model_name=self.model,
                accumulated_cost=0.0,
                max_budget_per_task=0.0,
                accumulated_token_usage=TokenUsage(model=self.model),
            ),
            raw_response=MagicMock(spec=ModelResponse, id="resp-1"),
        )


@pytest.mark.asyncio
async def test_image_sent_mid_step_is_not_passed_to_nonvision_model(
    monkeypatch, tmp_path
):
    """An image arriving mid-step must still trigger the vision guard.

    A user message that lands while the LLM call is in flight is recorded before
    the agent's reply, so the continued run ends with an assistant turn and
    ``prepare_llm_messages`` appends a request-only user nudge. That synthetic
    turn is the *latest* user message, so the "does the latest user message
    contain an image" check misses the real image and the image is handed to a
    non-vision model.
    """
    monkeypatch.setattr(
        "openhands.sdk.agent.base.has_vision_profile_available", lambda: False
    )
    llm = _ImageInjectingLLM(
        model="litellm_proxy/openrouter/z-ai/glm-4.7", disable_vision=True
    )
    conversation = LocalConversation(
        agent=Agent(llm=llm, tools=[]),
        workspace=tmp_path,
        visualizer=None,
    )
    llm._convo_box.append(conversation)

    conversation.send_message("first message")
    await conversation.arun()

    assert not any(
        contains_image for call in llm._calls for (_role, contains_image) in call
    ), f"image was sent to a non-vision model: {llm._calls}"
    assert any(
        isinstance(event, MessageEvent)
        and "does not support image understanding" in str(event.llm_message)
        for event in conversation.state.events
    ), "capability message was not emitted for the mid-step image"
    conversation.close()
