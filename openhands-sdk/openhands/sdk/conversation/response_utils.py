"""Utility functions for extracting agent responses from conversation events."""

from collections.abc import Sequence

from pydantic import BaseModel

from openhands.sdk.event import ActionEvent, MessageEvent
from openhands.sdk.event.base import Event
from openhands.sdk.llm.message import content_to_str
from openhands.sdk.tool.builtins.finish import FinishAction, FinishTool


def get_agent_final_response(events: Sequence[Event]) -> str:
    """Extract the final response from the agent.

    An agent can end a conversation in two ways:
    1. By calling the finish tool
    2. By returning a text message with no tool calls

    Args:
        events: List of conversation events to search through.

    Returns:
        The final response message from the agent, or empty string if not found.
    """
    # Find the last finish action or message event from the agent
    for event in reversed(events):
        # Case 1: finish tool call
        if (
            isinstance(event, ActionEvent)
            and event.source == "agent"
            and event.tool_name == FinishTool.name
        ):
            # Extract message from finish tool call
            if event.action is not None and isinstance(event.action, FinishAction):
                return event.action.message
            else:
                break
        # Case 2: text message with no tool calls (MessageEvent)
        elif isinstance(event, MessageEvent) and event.source == "agent":
            text_parts = content_to_str(event.llm_message.content)
            return "".join(text_parts)
    return ""


def get_structured_response[T: BaseModel](
    events: Sequence[Event],
    response_schema: type[T],
) -> T | None:
    """Extract and validate structured response from conversation events.

    Searches backwards through events to find the last finish action from the
    agent and validates its data against the provided Pydantic model.

    Args:
        events: List of conversation events to search through.
        response_schema: The Pydantic model class to validate against.

    Returns:
        A validated instance of response_schema, or None if no valid
        finish action is found.

    Example:
        ```python
        from pydantic import BaseModel, Field
        from openhands.sdk.conversation.response_utils import get_structured_response

        class CodeReviewResult(BaseModel):
            score: int = Field(description="Score from 1-10")
            issues: list[str] = Field(description="Issues found")
            can_merge: bool = Field(description="Ready to merge")

        # After conversation completes
        result = get_structured_response(conversation.events, CodeReviewResult)
        if result:
            print(f"Score: {result.score}, Can merge: {result.can_merge}")
        ```
    """
    for event in reversed(events):
        if (
            isinstance(event, ActionEvent)
            and event.source == "agent"
            and event.tool_name == FinishTool.name
            and event.action is not None
        ):
            # Extract action data and validate against schema
            action_data = event.action.model_dump(exclude={"kind"})
            return response_schema.model_validate(action_data)
    return None
