import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import ImageContent, Message, TextContent
from openhands.sdk.llm.router.impl.multimodal import MultimodalRouter
from openhands.sdk.testing import TestLLM


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


def test_image_input_to_non_multimodal_model_returns_capability_message():
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
async def test_async_image_input_to_non_multimodal_model_returns_capability_message():
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


def test_image_input_guard_does_not_preempt_multimodal_router():
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
