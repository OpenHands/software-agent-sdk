"""Classify LLM responses and dispatch to type-specific handlers.

Contains:
  - ``LLMResponseType`` — enum for response classification.
  - ``classify_response`` — pure classifier function (no side effects).
  - ``ResponseDispatchMixin`` — handler methods mixed into ``Agent``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from openhands.sdk.agent.stream_context import StreamContext
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    AskUserRequestEvent,
    AskUserResponseEvent,
    MessageEvent,
    ObservationEvent,
    QuestionInfo,
)
from openhands.sdk.event.ask_user_schema import (
    AskUserAnswer,
    validate_ask_user_answers,
)
from openhands.sdk.llm import LLMResponse, Message, TextContent
from openhands.sdk.logger import get_logger
from openhands.sdk.tool.builtins.ask_user import (
    AskUserAction,
    AskUserObservation,
)


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


def build_ask_user_observation(
    questions: list[QuestionInfo],
    action: str,
    answers: dict[str, list[AskUserAnswer]],
) -> AskUserObservation:
    """Build the tool observation that reflects an ``ask_user`` resolution."""
    if action == "accept":
        validate_ask_user_answers(questions, answers)
        lines = [
            f"- {question.question} -> "
            + ", ".join(selection.label for selection in answers[question.id])
            for question in questions
            if answers.get(question.id)
        ]
        message = (
            "User answered:\n" + "\n".join(lines)
            if lines
            else "User accepted without selecting an option."
        )
        return AskUserObservation(resolution="accept", answers=answers, message=message)
    if action == "decline":
        return AskUserObservation(
            resolution="decline",
            message="User declined to answer the question(s).",
        )
    return AskUserObservation(
        resolution="cancel",
        message=(
            "The question was dismissed (cancelled). Proceed using your best "
            "judgement or try a different approach."
        ),
    )


def pending_ask_user_request(
    state: ConversationState,
) -> AskUserRequestEvent | None:
    """Return the single unresolved ``ask_user`` request, if any.

    A request is unresolved until a matching :class:`AskUserResponseEvent`
    carrying its ``request_id`` lands on the active branch.
    """
    branch = state.active_branch()
    resolved = {e.request_id for e in branch if isinstance(e, AskUserResponseEvent)}
    for event in reversed(branch):
        if isinstance(event, AskUserRequestEvent) and event.request_id not in resolved:
            return event
    return None


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


class ResponseDispatchMixin:
    """Handler methods for each ``LLMResponseType``. Mixed into ``Agent``.

    Expects the host class (``Agent``) to provide the members declared in the
    ``TYPE_CHECKING`` block below.
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
            stream: StreamContext | None = None,
        ) -> ActionEvent | None: ...

        def _execute_actions(
            self,
            conversation: LocalConversation,
            action_events: list[ActionEvent],
            on_event: ConversationCallbackType,
        ) -> None: ...

        async def _aexecute_actions(
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
        stream: StreamContext | None = None,
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
                # The streamed text is this action's thought, so the first
                # action event is what retires the slot.
                stream=stream if i == 0 else None,
            )
            if action_event is None:
                continue
            action_events.append(action_event)

        paused, action_events = self._handle_ask_user_actions(
            state, action_events, on_event
        )
        if paused:
            return

        if self._requires_user_confirmation(state, action_events):
            return

        if action_events:
            self._execute_actions(conversation, action_events, on_event)

        self._maybe_emit_vllm_tokens(llm_response, on_event)

    async def _ahandle_tool_calls(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        state: ConversationState,
        on_event: ConversationCallbackType,
        stream: StreamContext | None = None,
    ) -> None:
        """Async variant of :meth:`_handle_tool_calls`.

        Delegates tool execution to :meth:`_aexecute_actions` so each
        tool call runs in its own thread and multiple calls are scheduled
        concurrently via :func:`asyncio.gather`.
        """
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
                # The streamed text is this action's thought, so the first
                # action event is what retires the slot.
                stream=stream if i == 0 else None,
            )
            if action_event is None:
                continue
            action_events.append(action_event)

        paused, action_events = self._handle_ask_user_actions(
            state, action_events, on_event
        )
        if paused:
            return

        if self._requires_user_confirmation(state, action_events):
            return

        if action_events:
            await self._aexecute_actions(conversation, action_events, on_event)

        self._maybe_emit_vllm_tokens(llm_response, on_event)

    def _handle_ask_user_actions(
        self,
        state: ConversationState,
        action_events: list[ActionEvent],
        on_event: ConversationCallbackType,
    ) -> tuple[bool, list[ActionEvent]]:
        """Pause for ``ask_user`` calls, or reject them with a corrective result.

        Returns ``(paused, remaining_actions)``. ``ask_user`` actions are always
        removed from ``remaining_actions``: either they paused the run (a request
        event was emitted) or they were rejected with an observation because a
        request is already pending or a second one was in the same batch.
        """
        if not any(isinstance(ae.action, AskUserAction) for ae in action_events):
            return False, action_events

        pending = pending_ask_user_request(state)
        paused = False
        remaining: list[ActionEvent] = []
        for action_event in action_events:
            if not isinstance(action_event.action, AskUserAction):
                remaining.append(action_event)
                continue
            if pending is not None:
                self._reject_ask_user_action(
                    action_event,
                    on_event,
                    "Another ask_user request is already pending. Wait for the "
                    "user's answer before asking again.",
                )
                continue
            if paused:
                self._reject_ask_user_action(
                    action_event,
                    on_event,
                    "Only one ask_user request may be pending at a time.",
                )
                continue
            request = AskUserRequestEvent(
                request_id=str(uuid4()),
                questions=action_event.action.questions,
                action_id=action_event.id,
                tool_call_id=action_event.tool_call_id,
                tool_name=action_event.tool_name,
                timeout_seconds=action_event.action.timeout_seconds,
            )
            on_event(request)
            pending = request
            paused = True

        if paused:
            state.execution_status = (
                ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            )
        return paused, remaining

    def _reject_ask_user_action(
        self,
        action_event: ActionEvent,
        on_event: ConversationCallbackType,
        message: str,
    ) -> None:
        on_event(
            ObservationEvent(
                observation=AskUserObservation(resolution="cancel", message=message),
                action_id=action_event.id,
                tool_name=action_event.tool_name,
                tool_call_id=action_event.tool_call_id,
            )
        )

    def _resolve_pending_ask_user(
        self,
        state: ConversationState,
        pending_actions: list[ActionEvent],
        on_event: ConversationCallbackType,
    ) -> bool:
        """Resolve ``ask_user`` actions from the request/response pair.

        Called from the run loop when there are unmatched actions. Returns
        ``True`` when it handled at least one ``ask_user`` action; the run then
        either pauses again (no response yet) or emits the resolving observation.
        """
        ask_user_actions = [
            ae for ae in pending_actions if isinstance(ae.action, AskUserAction)
        ]
        if not ask_user_actions:
            return False

        branch = state.active_branch()
        requests_by_action = {
            e.action_id: e for e in branch if isinstance(e, AskUserRequestEvent)
        }
        responses_by_request = {
            e.request_id: e for e in branch if isinstance(e, AskUserResponseEvent)
        }

        handled = False
        still_pending = False
        for action_event in ask_user_actions:
            request = requests_by_action.get(action_event.id)
            if request is None:
                self._reject_ask_user_action(
                    action_event,
                    on_event,
                    "No ask_user request event was recorded for this action.",
                )
                handled = True
                continue
            response = responses_by_request.get(request.request_id)
            if response is None:
                still_pending = True
                handled = True
                continue
            on_event(
                ObservationEvent(
                    observation=build_ask_user_observation(
                        request.questions, response.action, response.answers
                    ),
                    action_id=action_event.id,
                    tool_name=action_event.tool_name,
                    tool_call_id=action_event.tool_call_id,
                )
            )
            handled = True

        if still_pending:
            state.execution_status = (
                ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            )
        return handled

    def _handle_content_response(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        state: ConversationState,
        on_event: ConversationCallbackType,
        stream: StreamContext | None = None,
        *,
        mask_secrets: bool = True,
    ) -> None:
        """Handle LLM response with text content — finishes conversation."""
        self._emit_message_event(
            message,
            llm_response,
            conversation,
            on_event,
            stream,
            mask_secrets=mask_secrets,
        )
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
        stream: StreamContext | None = None,
        *,
        response_type: LLMResponseType,
        mask_secrets: bool = True,
    ) -> None:
        """Handle LLM response with no user-facing content.

        Covers both reasoning-only and empty responses. Emits the message
        event and sends corrective feedback so the model knows it must
        produce a tool call or user-facing content.
        """
        if response_type is LLMResponseType.EMPTY:
            logger.warning("LLM produced empty response - continuing agent loop")
        self._emit_message_event(
            message,
            llm_response,
            conversation,
            on_event,
            stream,
            mask_secrets=mask_secrets,
        )
        self._maybe_emit_vllm_tokens(llm_response, on_event)
        self._send_corrective_nudge(on_event)

    def _emit_message_event(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        stream: StreamContext | None = None,
        *,
        mask_secrets: bool = True,
    ) -> MessageEvent:
        """Create and emit a MessageEvent, running critic if configured.

        The message carries the id its own stream minted, so a client holding
        an open slot retires it on ``frame.event.id == slot.item_id``.
        """
        minted: dict[str, Any] = {}
        if stream is not None and (item_id := stream.claim()):
            minted["id"] = item_id
        msg_event = MessageEvent(
            **minted,
            source="agent",
            llm_message=self._mask_secrets(message, conversation)
            if mask_secrets
            else message,
            llm_response_id=llm_response.id,
        )
        if self.critic is not None and self.critic.mode == "finish_and_message":
            critic_result = self._evaluate_with_critic(conversation, msg_event)
            if critic_result is not None:
                msg_event = msg_event.model_copy(
                    update={"critic_result": critic_result}
                )
        on_event(msg_event)
        # Retired only now: on_event can raise while persisting, and a slot
        # retired before that leaves the client holding it open forever.
        if stream is not None and minted:
            stream.commit()
        return msg_event

    @staticmethod
    def _mask_secrets(message: Message, conversation: LocalConversation) -> Message:
        """Return ``message`` with registered secret values masked in its text.

        ``thinking_blocks`` and ``responses_reasoning_item`` are left alone:
        they are signed provider payloads, and rewriting them invalidates the
        signature replayed on the next request.
        """
        mask = conversation.state.secret_registry.mask_secrets_in_output
        reasoning = message.reasoning_content
        return message.model_copy(
            update={
                "content": [
                    part.model_copy(update={"text": mask(part.text)})
                    if isinstance(part, TextContent)
                    else part
                    for part in message.content
                ],
                "reasoning_content": mask(reasoning) if reasoning else reasoning,
            }
        )

    def _send_corrective_nudge(self, on_event: ConversationCallbackType) -> None:
        """Inject corrective feedback when no tool call and no content.

        The model still receives this as a user-role message, but the event
        source marks that it came from the framework rather than the human.
        """
        logger.warning(
            "LLM response contained no tool call and no content"
            " - sending corrective feedback"
        )
        nudge = MessageEvent(
            source="environment",
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
