"""
Debate tools for inter-agent communication.

This module provides tools that allow reviewer agents to communicate
with each other during the debate phase.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from models import DebateState, ReviewerModel
from pydantic import Field

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    pass


# Type for the message handler callback
MessageHandler = Callable[[ReviewerModel, ReviewerModel | None, str], str]


class SendToReviewerAction(Action):
    """Action to send a message to another reviewer."""

    recipient: str = Field(
        description=(
            "The reviewer to send the message to. "
            "Options: 'gpt', 'claude', 'gemini', or 'all' to broadcast."
        )
    )
    message: str = Field(description="The message to send to the reviewer(s).")


class SendToReviewerObservation(Observation):
    """Observation from sending a message."""

    recipient: str = Field(description="Who received the message")
    response: str = Field(default="", description="Response from the recipient")


class SendToReviewerExecutor(ToolExecutor):
    """Executor for sending messages to other reviewers.

    This executor manages inter-agent communication by:
    1. Placing messages in a shared queue
    2. Waiting for responses (synchronous communication)
    3. Recording all messages in the debate state
    """

    def __init__(
        self,
        sender_model: ReviewerModel,
        debate_state: DebateState,
        message_queue: MessageQueue,
    ):
        self._sender_model = sender_model
        self._debate_state = debate_state
        self._message_queue = message_queue

    def _resolve_recipient(self, recipient_str: str) -> ReviewerModel | None:
        """Resolve recipient string to model enum."""
        recipient_map = {
            "gpt": ReviewerModel.GPT_5_2,
            "gpt-5.2": ReviewerModel.GPT_5_2,
            "gpt5.2": ReviewerModel.GPT_5_2,
            "claude": ReviewerModel.CLAUDE_SONNET_4_5,
            "sonnet": ReviewerModel.CLAUDE_SONNET_4_5,
            "claude-sonnet": ReviewerModel.CLAUDE_SONNET_4_5,
            "gemini": ReviewerModel.GEMINI_3_FLASH,
            "gemini-3": ReviewerModel.GEMINI_3_FLASH,
            "flash": ReviewerModel.GEMINI_3_FLASH,
            "all": None,  # None means broadcast
        }
        return recipient_map.get(recipient_str.lower())

    def __call__(  # type: ignore[override]
        self,
        action: SendToReviewerAction,
        conversation: Any = None,  # noqa: ARG002
    ) -> SendToReviewerObservation:
        """Send a message to another reviewer and wait for response."""
        recipient_str = action.recipient.strip().lower()

        # Handle "all" broadcast
        if recipient_str == "all":
            recipient = None
        else:
            recipient = self._resolve_recipient(recipient_str)
            if recipient is None:
                return SendToReviewerObservation.from_text(
                    text=(
                        f"Unknown recipient '{action.recipient}'. "
                        "Valid options: gpt, claude, gemini, or all"
                    ),
                    recipient=action.recipient,
                    is_error=True,
                )

            # Can't send to self
            if recipient == self._sender_model:
                return SendToReviewerObservation.from_text(
                    text="Cannot send a message to yourself.",
                    recipient=action.recipient,
                    is_error=True,
                )

        # Record the message in debate state
        self._debate_state.add_message(
            sender=self._sender_model,
            content=action.message,
            recipient=recipient,
        )

        # Send through the message queue and wait for response
        response = self._message_queue.send_and_wait(
            sender=self._sender_model,
            recipient=recipient,
            message=action.message,
        )

        recipient_name = recipient.display_name if recipient else "all reviewers"
        return SendToReviewerObservation.from_text(
            text=f"Message sent to {recipient_name}.\n\nResponse: {response}",
            recipient=action.recipient,
            response=response,
        )


class SendToReviewerTool(
    ToolDefinition[SendToReviewerAction, SendToReviewerObservation]
):
    """Tool definition for sending messages to other reviewers."""

    @classmethod
    def create(  # type: ignore[override]
        cls,
        sender_model: ReviewerModel,
        debate_state: DebateState,
        message_queue: MessageQueue,
    ) -> Sequence[SendToReviewerTool]:
        """Create a SendToReviewerTool for a specific reviewer.

        Args:
            sender_model: The model that will use this tool
            debate_state: Shared debate state
            message_queue: Shared message queue

        Returns:
            List containing configured tool instance
        """
        # Build list of other reviewers for the description
        other_reviewers = [m.display_name for m in ReviewerModel if m != sender_model]
        reviewer_list = ", ".join(other_reviewers)

        description = f"""Send a message to another reviewer during the debate.

You are {sender_model.display_name}. You can send messages to:
- {reviewer_list}
- Or 'all' to broadcast to everyone

Use this tool to:
- Share your analysis or perspective
- Ask clarifying questions
- Respond to points raised by others
- Work toward consensus on issues

Example:
- recipient: "claude"
- message: "I disagree with your point about error handling. The current approach..."
"""

        executor = SendToReviewerExecutor(
            sender_model=sender_model,
            debate_state=debate_state,
            message_queue=message_queue,
        )

        return [
            cls(
                action_type=SendToReviewerAction,
                observation_type=SendToReviewerObservation,
                description=description,
                annotations=ToolAnnotations(
                    title="send_to_reviewer",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                executor=executor,
            )
        ]


class ConcludeDebateAction(Action):
    """Action to conclude the debate with a final position."""

    final_position: str = Field(
        description="Your final position and summary of key points from the debate."
    )
    consensus_points: str = Field(
        default="", description="Points where all reviewers agree (if any)."
    )
    remaining_disagreements: str = Field(
        default="", description="Points where disagreement remains (if any)."
    )


class ConcludeDebateObservation(Observation):
    """Observation from concluding the debate."""

    status: str = Field(description="Status of the conclusion")


class ConcludeDebateExecutor(ToolExecutor):
    """Executor for concluding the debate."""

    def __init__(
        self,
        sender_model: ReviewerModel,
        debate_state: DebateState,
        conclusion_callback: Callable[[ReviewerModel, str, str, str], None],
    ):
        self._sender_model = sender_model
        self._debate_state = debate_state
        self._conclusion_callback = conclusion_callback

    def __call__(  # type: ignore[override]
        self,
        action: ConcludeDebateAction,
        conversation: Any = None,  # noqa: ARG002
    ) -> ConcludeDebateObservation:
        """Record the reviewer's conclusion."""
        self._conclusion_callback(
            self._sender_model,
            action.final_position,
            action.consensus_points,
            action.remaining_disagreements,
        )

        return ConcludeDebateObservation.from_text(
            text="Your conclusion has been recorded. Thank you for participating in the debate.",  # noqa: E501
            status="concluded",
        )


class ConcludeDebateTool(
    ToolDefinition[ConcludeDebateAction, ConcludeDebateObservation]
):
    """Tool definition for concluding the debate."""

    @classmethod
    def create(  # type: ignore[override]
        cls,
        sender_model: ReviewerModel,
        debate_state: DebateState,
        conclusion_callback: Callable[[ReviewerModel, str, str, str], None],
    ) -> Sequence[ConcludeDebateTool]:
        """Create a ConcludeDebateTool for a specific reviewer."""
        description = f"""Conclude your participation in the debate.

You are {sender_model.display_name}. Use this tool when you're ready to:
- Summarize your final position
- Note points of consensus
- Acknowledge remaining disagreements

Call this tool when:
- You've engaged with the other reviewers sufficiently
- You've made your key points
- Further discussion won't significantly change positions
"""

        executor = ConcludeDebateExecutor(
            sender_model=sender_model,
            debate_state=debate_state,
            conclusion_callback=conclusion_callback,
        )

        return [
            cls(
                action_type=ConcludeDebateAction,
                observation_type=ConcludeDebateObservation,
                description=description,
                annotations=ToolAnnotations(
                    title="conclude_debate",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                executor=executor,
            )
        ]


class MessageQueue:
    """Thread-safe message queue for inter-agent communication.

    This class coordinates communication between agents by:
    1. Managing message routing between agents
    2. Implementing turn-based communication (one agent speaks at a time)
    3. Providing response waiting mechanism
    """

    def __init__(
        self,
        response_handler: Callable[[ReviewerModel, ReviewerModel | None, str], str],
        timeout: float = 120.0,
    ):
        """Initialize the message queue.

        Args:
            response_handler: Callback to generate responses to messages
            timeout: Maximum time to wait for a response
        """
        self._response_handler = response_handler
        self._timeout = timeout
        self._lock = threading.Lock()
        self._response_queues: dict[ReviewerModel, queue.Queue[str]] = {
            model: queue.Queue() for model in ReviewerModel
        }

    def send_and_wait(
        self,
        sender: ReviewerModel,
        recipient: ReviewerModel | None,
        message: str,
    ) -> str:
        """Send a message and wait for a response.

        Args:
            sender: The sending model
            recipient: The recipient model (None for broadcast)
            message: The message content

        Returns:
            Response from the recipient(s)
        """
        with self._lock:
            # Get response from handler (this should invoke the recipient agent)
            response = self._response_handler(sender, recipient, message)
            return response
