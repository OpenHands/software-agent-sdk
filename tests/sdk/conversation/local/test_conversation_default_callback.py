from pydantic import SecretStr

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.state import ConversationState
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import LLM, Message, TextContent


class ConversationDefaultCallbackDummyAgent(AgentBase):
    def __init__(self):
        llm = LLM(
            model="gpt-4o-mini", api_key=SecretStr("test-key"), usage_id="test-llm"
        )
        super().__init__(llm=llm, tools=[])

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        event = SystemPromptEvent(
            source="agent", system_prompt=TextContent(text="dummy"), tools=[]
        )
        on_event(event)

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        on_event(
            MessageEvent(
                source="agent",
                llm_message=Message(role="assistant", content=[TextContent(text="ok")]),
            )
        )


def test_default_callback_appends_on_init():
    agent = ConversationDefaultCallbackDummyAgent()
    events_seen: list[str] = []

    conversation = Conversation(
        agent=agent, callbacks=[lambda e: events_seen.append(e.id)]
    )

    assert len(conversation.state.events) == 1
    assert isinstance(conversation.state.events[0], SystemPromptEvent)
    assert conversation.state.events[0].id in events_seen


def test_send_message_appends_once():
    agent = ConversationDefaultCallbackDummyAgent()
    seen_ids: list[str] = []

    def user_cb(event):
        seen_ids.append(event.id)

    conversation = Conversation(agent=agent, callbacks=[user_cb])

    conversation.send_message(Message(role="user", content=[TextContent(text="hi")]))

    # Now we should have two events: initial system prompt and the user message
    assert len(conversation.state.events) == 2
    assert isinstance(conversation.state.events[-1], MessageEvent)

    # Ensure the user message event is appended exactly once in state
    last_id = conversation.state.events[-1].id
    assert sum(1 for e in conversation.state.events if e.id == last_id) == 1

    # Ensure callback saw both events
    assert set(seen_ids) == {e.id for e in conversation.state.events}
