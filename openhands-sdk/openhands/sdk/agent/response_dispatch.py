"""Classify LLM responses and dispatch to type-specific handlers.

Contains:
  - ``LLMResponseType`` — enum for response classification.
  - ``classify_response`` — pure classifier function (no side effects).
  - ``ResponseDispatchMixin`` — handler methods mixed into ``Agent``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from openhands.sdk.agent.stream_context import StreamContext
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import ActionEvent, Event, MessageEvent
from openhands.sdk.llm import LLMResponse, Message, TextContent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.conversation import (
        ConversationCallbackType,
        ConversationState,
        LocalConversation,
    )
    from openhands.sdk.critic.base import CriticBase, CriticResult
    from openhands.sdk.llm import (
        MessageToolCall,
        ReasoningItemModel,
        RedactedThinkingBlock,
        ThinkingBlock,
    )
    from openhands.sdk.security.analyzer import SecurityAnalyzerBase

logger = get_logger(__name__)


# Corrective feedback for a content-without-tool-call turn under the "nudge"
# policy. Unlike the empty-response nudge, the model DID produce a message
# (preserved in history), so the feedback points at the missing tool call and at
# the explicit way to signal completion.
_CONTENT_NUDGE_TEXT = (
    "Your last message did not include a function call. If the task is "
    "complete, call the `finish` tool; otherwise continue with the next "
    "tool call."
)

# How far back to scan the active branch when deciding whether to nudge. The
# decision only needs the tail since the last human turn (or the last action),
# so this is a cap on work, not a semantic window.
_NUDGE_SCAN_WINDOW = 64

# A human message that is one of these is a hold, not a task — a content-only
# reply to it must not be nudged into "continuing".
_HOLD_PHRASES = frozenset(
    {"stop", "wait", "hold on", "pause", "cancel", "never mind", "nevermind"}
)
# Openers of an explicit refusal/inability. A model that says it cannot proceed
# is answering, not narrating; nudging it only produces the same refusal again.
_REFUSAL_OPENERS = (
    "i can't",
    "i cannot",
    "i can not",
    "i won't",
    "i will not",
    "i'm unable",
    "i am unable",
    "i'm not able",
    "i am not able",
)


def _message_text_of(message: Message) -> str:
    return " ".join(
        part.text for part in message.content if isinstance(part, TextContent)
    ).strip()


def _message_text(event: MessageEvent) -> str:
    return _message_text_of(event.llm_message)


def _is_content_nudge(event: Event) -> bool:
    """Is ``event`` a synthetic nudge emitted under ``content_response_policy``?

    The fixed nudge text is the marker: it is emitted by the framework
    (``source="environment"``), never by the human or the model.
    """
    return (
        isinstance(event, MessageEvent)
        and event.source == "environment"
        and _message_text(event) == _CONTENT_NUDGE_TEXT
    )


def _last_user_message_reads_like_task(events: list[Event]) -> bool:
    """False when the human's last message is a question, a hold, or chit-chat.

    A model replying in prose to any of those is *answering*, and nudging it to
    "continue with the next tool call" would be wrong. With no human message in
    the window we assume a task (the agent was clearly told to do something).
    """
    for event in reversed(events):
        if isinstance(event, MessageEvent) and event.source == "user":
            text = _message_text(event).lower().rstrip(".! ")
            if not text or text.endswith("?"):
                return False
            if text in _HOLD_PHRASES:
                return False
            if len(text.split()) < 2:  # "hi", "thanks", "ok" — not a task
                return False
            return True
    return True


def _looks_like_answer_or_refusal(text: str) -> bool:
    """The model's own message is a question back to the user, or a refusal."""
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    return stripped.lower().startswith(_REFUSAL_OPENERS)


def should_send_content_nudge(message: Message, events: list[Event]) -> bool:
    """Decide whether a content-without-tool-call turn gets ONE synthetic nudge.

    Pure function of the model's message and the tail of the active branch, so
    the bound is structural rather than a property of conversation-level stuck
    detection. All of these must hold:

    - the human's last message reads like a task (not a question / hold /
      chit-chat) — see :func:`_last_user_message_reads_like_task`;
    - the model's message is not itself a question or an explicit refusal;
    - the model has not *already* been nudged since it last acted: if a content
      nudge sits after the last ``ActionEvent`` (and after the last human turn),
      the model answered a nudge with more prose, and we give up. That bounds a
      runaway prose turn at +1 step, while a model that narrates, is nudged,
      acts, and later narrates again is still nudged each time.
    """
    if not _last_user_message_reads_like_task(events):
        return False
    if _looks_like_answer_or_refusal(_message_text_of(message)):
        return False
    for event in reversed(events):
        if isinstance(event, ActionEvent):
            return True  # acted since the last nudge — a fresh streak
        if isinstance(event, MessageEvent) and event.source == "user":
            return True  # a new human turn resets the bound
        if _is_content_nudge(event):
            return False  # already nudged once this streak
    return True


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
        content_response_policy: Literal["finish", "nudge"]

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

        if self._requires_user_confirmation(state, action_events):
            return

        if action_events:
            await self._aexecute_actions(conversation, action_events, on_event)

        self._maybe_emit_vllm_tokens(llm_response, on_event)

    def _handle_content_response(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        state: ConversationState,
        on_event: ConversationCallbackType,
        stream: StreamContext | None = None,
    ) -> None:
        """Handle LLM response with text content.

        Under the default ``content_response_policy="finish"`` the message is
        treated as the final answer and the conversation is marked FINISHED.
        Under ``"nudge"`` the message is emitted (preserved in history), then —
        if :func:`should_send_content_nudge` allows it — corrective feedback is
        sent and the loop continues, reserving completion for an explicit
        signal such as the ``finish`` tool. This keeps weaker/local models that
        narrate a step in prose before its tool call from being silently
        treated as done mid-task (#3992). The nudge is bounded: at most one per
        prose streak (a second consecutive content-only turn finishes), and
        never when the human asked a question, said to hold, or the model is
        itself asking or refusing.

        Relation to the ``Stop`` hook: the hook fires at FINISHED and can deny
        the stop (shell-level control, injected feedback); ``"nudge"`` acts
        in-loop *before* FINISHED is reached, so a nudged turn never triggers
        the hook. Reach for the hook when you want an external policy on
        stopping; reach for ``"nudge"`` for the narrate-then-act model failure.
        Don't stack them expecting both to fire on the same turn.
        """
        self._emit_message_event(message, llm_response, conversation, on_event, stream)
        self._maybe_emit_vllm_tokens(llm_response, on_event)
        if self.content_response_policy == "nudge":
            recent = state.active_branch(limit=_NUDGE_SCAN_WINDOW)
            if should_send_content_nudge(message, recent):
                logger.debug(
                    "LLM produced a message response without a tool call - "
                    "content_response_policy='nudge', continuing agent loop"
                )
                self._send_corrective_nudge(on_event, text=_CONTENT_NUDGE_TEXT)
                return
            logger.debug(
                "LLM produced a message response without a tool call - "
                "content_response_policy='nudge' but the nudge is not warranted "
                "(already nudged, or the turn is an answer/refusal/hold) - finishing"
            )
        else:
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
    ) -> None:
        """Handle LLM response with no user-facing content.

        Covers both reasoning-only and empty responses. Emits the message
        event and sends corrective feedback so the model knows it must
        produce a tool call or user-facing content.
        """
        if response_type is LLMResponseType.EMPTY:
            logger.warning("LLM produced empty response - continuing agent loop")
        self._emit_message_event(message, llm_response, conversation, on_event, stream)
        self._maybe_emit_vllm_tokens(llm_response, on_event)
        self._send_corrective_nudge(on_event)

    def _emit_message_event(
        self,
        message: Message,
        llm_response: LLMResponse,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        stream: StreamContext | None = None,
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
            llm_message=self._mask_secrets(message, conversation),
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

    def _send_corrective_nudge(
        self,
        on_event: ConversationCallbackType,
        *,
        text: str | None = None,
    ) -> None:
        """Inject corrective feedback when no tool call and no content.

        The model still receives this as a user-role message, but the event
        source marks that it came from the framework rather than the human, so
        repeated synthetic nudges do not reset the human-turn boundary the
        stuck detector uses. ``text`` overrides the default for the
        content-without-tool-call case under ``content_response_policy="nudge"``.
        """
        logger.warning(
            "LLM response contained no tool call - sending corrective feedback"
        )
        nudge = MessageEvent(
            source="environment",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(
                        text=text
                        or (
                            "Your last response did not include a "
                            "function call or a message. Please "
                            "use a tool to proceed with the task."
                        )
                    )
                ],
            ),
        )
        on_event(nudge)
