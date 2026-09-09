from collections.abc import Sequence
from pathlib import Path

from openhands.sdk.agent.base import AgentBase
from openhands.sdk.conversation import LocalConversation
from openhands.sdk.conversation.state import (
    ConversationExecutionStatus,
    ConversationState,
)
from openhands.sdk.conversation.types import (
    ConversationCallbackType,
    ConversationTokenCallbackType,
)
from openhands.sdk.event import Event
from openhands.sdk.event.llm_convertible import MessageEvent, SystemPromptEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.testing import TestLLM


class DeterministicAgent(AgentBase):
    def __init__(self) -> None:
        super().__init__(
            llm=TestLLM.from_messages([]),
            tools=[],
            include_default_tools=[],
        )

    def init_state(
        self, state: ConversationState, on_event: ConversationCallbackType
    ) -> None:
        on_event(
            SystemPromptEvent(
                id="system-prompt",
                timestamp="2026-09-01T12:00:00",
                source="agent",
                system_prompt=TextContent(text="Deterministic recorder fixture"),
                tools=[],
            )
        )

    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        on_event(
            MessageEvent(
                id="assistant-message",
                timestamp="2026-09-01T12:00:02",
                source="agent",
                llm_message=Message(
                    role="assistant",
                    content=[TextContent(text="Fixture complete")],
                ),
            )
        )
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED


def run_deterministic_conversation(
    workspace: Path,
    callbacks: Sequence[ConversationCallbackType] = (),
) -> list[Event]:
    events: list[Event] = []
    conversation = LocalConversation(
        agent=DeterministicAgent(),
        workspace=workspace,
        callbacks=[*callbacks, events.append],
        max_iteration_per_run=1,
        stuck_detection=False,
    )
    try:
        conversation.send_message("Record this deterministic run")
        conversation.run()
        return events
    finally:
        conversation.close()
