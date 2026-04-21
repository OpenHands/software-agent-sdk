"""Classify LLM responses and dispatch to type-specific handlers.

Contains:
  - ``LLMResponseType`` — enum for response classification.
  - ``classify_response`` — pure classifier function (no side effects).
  - ``ResponseDispatchMixin`` — handler methods mixed into ``Agent``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import LLMResponse, Message, TextContent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.conversation import (
        ConversationCallbackType,
        ConversationState,
        LocalConversation,
    )
    from openhands.sdk.critic.base import CriticBase, CriticResult
    from openhands.sdk.event import ActionEvent
    from openhands.sdk.llm import (
        MessageToolCall,
        ReasoningItemModel,
        RedactedThinkingBlock,
        ThinkingBlock,
    )
    from openhands.sdk.security.analyzer import SecurityAnalyzerBase

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class LLMResponseType(StrEnum):
    """Mutually exclusive classification of an LLM response."""

    TOOL_CALLS = "tool_calls"
    CONTENT = "content"
    REASONING_ONLY = "reasoning_only"
    EMPTY = "empty"


def classify_response(message: Message) -> LLMResponseType:
    """Classify an LLM response message into exactly one type.

    Decision priority (first match wins):
      1. TOOL_CALLS  — message contains tool calls
      2. CONTENT     — message contains non-blank TextContent
      3. REASONING_ONLY — message has reasoning but no visible content
      4. EMPTY       — nothing useful

    This function is pure: no side effects, no logging, no mutation.
    """
    if message.tool_calls:
        return LLMResponseType.TOOL_CALLS

    if any(isinstance(c, TextContent) and c.text.strip() for c in message.content):
        return LLMResponseType.CONTENT

    if (
        message.responses_reasoning_item is not None
        or message.reasoning_content is not None
        or message.thinking_blocks
    ):
        return LLMResponseType.REASONING_ONLY

    return LLMResponseType.EMPTY


# ---------------------------------------------------------------------------
# Dispatch mixin
# ---------------------------------------------------------------------------


@runtime_checkable
class _AgentProtocol(Protocol):
    """Subset of ``Agent`` that ``ResponseDispatchMixin`` depends on."""

    critic: CriticBase | None

    def _get_action_event(
        self,
        tool_call: MessageToolCall,
        conversation: LocalConversation,
        llm_response_id: str,
        on_event: ConversationCallbackType,
        security_analyzer: SecurityAnalyzerBase | None = None,
        thought: list[TextContent] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] | None = None,
        responses_reasoning_item: ReasoningItemModel | None = None,
    ) -> ActionEvent | None: ...

    def _execute_actions(
        self,
        conversation: LocalConversation,
        action_events: list[ActionEvent],
        on_event: ConversationCallbackType,
    ) -> None: ...

    def _requires_user_confirmation(
        self,
        state: ConversationState,
        action_events: list[ActionEvent],
    ) -> bool: ...

    def _maybe_emit_vllm_tokens(
        self,
        llm_response: LLMResponse,
        on_event: ConversationCallbackType,
    ) -> None: ...

    def _evaluate_with_critic(
        self,
        conversation: LocalConversation,
        event: ActionEvent | MessageEvent,
    ) -> CriticResult | None: ...


class ResponseDispatchMixin:
    """Handler methods for each ``LLMResponseType``. Mixed into ``Agent``.

    Expects the host class to satisfy :class:`_AgentProtocol`.
    """

    # Declared for pyright — the actual implementations live on Agent.
    if TYPE_CHECKING:
        critic: CriticBase | None

        def _get_action_event(
            self,
            tool_call: MessageToolCall,
            conversation: LocalConversation,
            llm_response_id: str,
            on_event: ConversationCallbackType,
            security_analyzer: SecurityAnalyzerBase | None = None,
            thought: list[TextContent] | None = None,
            reasoning_content: str | None = None,
            thinking_blocks: (
                list[ThinkingBlock | RedactedThinkingBlock] | None
            ) = None,
            responses_reasoning_item: ReasoningItemModel | None = None,
        ) -> ActionEvent | None: ...

        def _execute_actions(
            self,
            conversation: LocalConversation,
            action_events: list[ActionEvent],
            on_event: ConversationCallbackType,
        ) -> None: ...

        def _requires_user_confirmation(
            self,
            state: ConversationState,
            action_events: list[ActionEvent],
        ) -> bool: ...

        def _maybe_emit_vllm_tokens(
            self,
            llm_response: LLMResponse,
            on_event: ConversationCallbackType,
        ) -> None: ...

        def _evaluate_with_critic(
            self,
            conversation: LocalConversation,
            event: ActionEvent | MessageEvent,
        ) -> CriticResult | None: ...

    def _handle_tool_calls(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Handle LLM response containing tool calls."""
        if not all(isinstance(c, TextContent) for c in message.content):
            logger.warning(
                "LLM returned tool calls but message content is not all "
                "TextContent - ignoring non-text content"
            )

        thought_content = [c for c in message.content if isinstance(c, TextContent)]

        action_events: list[ActionEvent] = []
        assert message.tool_calls, "classify_response guarantees tool_calls"
        for i, tool_call in enumerate(message.tool_calls):
            action_event = self._get_action_event(
                tool_call,
                conversation=conversation,
                llm_response_id=llm_response.id,
                on_event=on_event,
                security_analyzer=state.security_analyzer,
                thought=thought_content if i == 0 else [],
                reasoning_content=(message.reasoning_content if i == 0 else None),
                thinking_blocks=(list(message.thinking_blocks) if i == 0 else []),
                responses_reasoning_item=(
                    message.responses_reasoning_item if i == 0 else None
                ),
            )
            if action_event is None:
                continue
            action_events.append(action_event)

        if self._requires_user_confirmation(state, action_events):
            return

        if action_events:
            self._execute_actions(conversation, action_events, on_event)

        self._maybe_emit_vllm_tokens(llm_response, on_event)

    def _handle_content_response(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Handle LLM response with text content — finishes conversation."""
        self._emit_message_event(message, llm_response, conversation, on_event)
        self._maybe_emit_vllm_tokens(llm_response, on_event)
        logger.debug("LLM produced a message response - awaits user input")
        state.execution_status = ConversationExecutionStatus.FINISHED

    def _handle_no_content_response(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        state: ConversationState,  # noqa: ARG002
        on_event: ConversationCallbackType,
        *,
        response_type: LLMResponseType,
    ) -> None:
        """Handle LLM response with no user-facing content.

        Covers both reasoning-only and empty responses. Emits the message
        event and sends corrective feedback so the model knows it must
        produce a tool call or user-facing content.
        """
        if response_type is LLMResponseType.EMPTY:
            logger.warning("LLM produced empty response - continuing agent loop")
        self._emit_message_event(message, llm_response, conversation, on_event)
        self._maybe_emit_vllm_tokens(llm_response, on_event)
        self._send_corrective_nudge(on_event)

    def _emit_message_event(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
    ) -> MessageEvent:
        """Create and emit a MessageEvent, running critic if configured."""
        msg_event = MessageEvent(
            source="agent",
            llm_message=message,
            llm_response_id=llm_response.id,
        )
        if self.critic is not None and self.critic.mode == "finish_and_message":
            critic_result = self._evaluate_with_critic(conversation, msg_event)
            if critic_result is not None:
                msg_event = msg_event.model_copy(
                    update={"critic_result": critic_result}
                )
        on_event(msg_event)
        return msg_event

    def _send_corrective_nudge(self, on_event: ConversationCallbackType) -> None:
        """Inject corrective feedback when no tool call and no content.

        Prevents the monologue stuck-detector from firing when the model
        simply forgot to emit a function call.
        """
        logger.warning(
            "LLM response contained no tool call and no content"
            " - sending corrective feedback"
        )
        nudge = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(
                        text=(
                            "Your last response did not include a "
                            "function call or a message. Please "
                            "use a tool to proceed with the task."
                        )
                    )
                ],
            ),
        )
        on_event(nudge)
