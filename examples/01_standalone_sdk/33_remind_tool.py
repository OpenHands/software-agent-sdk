"""Example demonstrating a custom "remind" tool that sends deferred messages.

This example shows how to create a tool that:
1. Accepts a message and delay in seconds
2. Schedules the message to be sent back to the conversation after the delay
3. Returns immediately, allowing the agent to continue working

This demonstrates async/deferred tool behavior - the tool returns immediately
with a confirmation, but schedules a future action (sending a message) that
will be processed by the agent later.

Use case: Human-in-the-loop as a tool call, where the tool can inject messages
into the conversation at a later time without blocking the agent.
"""

import os
import threading
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import Field, SecretStr

from openhands.sdk import (
    LLM,
    Agent,
    Conversation,
    ImageContent,
    Observation,
    TextContent,
)
from openhands.sdk.tool import Tool, ToolDefinition, ToolExecutor, register_tool
from openhands.sdk.tool.schema import Action


if TYPE_CHECKING:
    from openhands.sdk.conversation import LocalConversation


class RemindAction(Action):
    """Action to schedule a reminder message."""

    message: str = Field(description="The reminder message to send")
    delay_seconds: float = Field(
        description="Number of seconds to wait before sending the reminder",
        gt=0,
    )


class RemindObservation(Observation):
    """Observation confirming the reminder was scheduled."""

    scheduled: bool = Field(default=True)
    message: str = Field(description="The scheduled message")
    delay_seconds: float = Field(description="The delay in seconds")

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [
            TextContent(
                text=(
                    f"Reminder scheduled: '{self.message}' "
                    f"will be sent in {self.delay_seconds} seconds."
                )
            )
        ]


class RemindExecutor(ToolExecutor[RemindAction, RemindObservation]):
    """Executor that schedules reminders to be sent to the conversation."""

    def __call__(
        self, action: RemindAction, conversation: "LocalConversation | None" = None
    ) -> RemindObservation:
        if conversation is None:
            return RemindObservation(
                scheduled=False,
                message=action.message,
                delay_seconds=action.delay_seconds,
            )

        def send_reminder():
            time.sleep(action.delay_seconds)
            reminder_text = f"[REMINDER]: {action.message}"
            conversation.send_message(reminder_text)

        thread = threading.Thread(target=send_reminder, daemon=True)
        thread.start()

        return RemindObservation(
            scheduled=True,
            message=action.message,
            delay_seconds=action.delay_seconds,
        )


_REMIND_DESCRIPTION = """Schedule a reminder message to be sent after a delay.

Use this tool when you need to:
- Set a reminder for yourself or the user
- Schedule a follow-up message
- Create a delayed notification

The reminder will be injected into the conversation after the specified delay,
and you will see it as a new user message that you should acknowledge.
"""


class RemindTool(ToolDefinition[RemindAction, RemindObservation]):
    """A tool that schedules reminder messages to be sent after a delay."""

    @classmethod
    def create(cls, conv_state=None, **kwargs) -> Sequence["RemindTool"]:  # noqa: ARG003
        return [
            cls(
                description=_REMIND_DESCRIPTION,
                action_type=RemindAction,
                observation_type=RemindObservation,
                executor=RemindExecutor(),
            )
        ]


# Register the tool so it can be resolved by name
register_tool(RemindTool.name, RemindTool)


if __name__ == "__main__":
    api_key = os.getenv("LLM_API_KEY")
    assert api_key is not None, "LLM_API_KEY environment variable is not set."
    model = os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")

    llm = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )

    tools = [Tool(name=RemindTool.name)]
    agent = Agent(llm=llm, tools=tools)
    conversation = Conversation(agent=agent, workspace=".")

    print("=== Remind Tool Example ===")
    print("Asking the agent to set a reminder for 3 seconds from now...\n")

    conversation.send_message(
        "Please set a reminder for 3 seconds from now with the message "
        "'Time to check on the task!'. After setting the reminder, "
        "tell me what you did and then wait for the reminder to arrive. "
        "When you see the reminder message, acknowledge it."
    )

    conversation.run()

    print("\n=== Example Complete ===")
    cost = llm.metrics.accumulated_cost
    print(f"EXAMPLE_COST: {cost}")
