from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import PrivateAttr, ValidationError, model_validator

import openhands.sdk.security.analyzer as analyzer
import openhands.sdk.security.risk as risk
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.agent.critic_mixin import CriticMixin
from openhands.sdk.agent.parallel_executor import ParallelToolExecutor
from openhands.sdk.agent.utils import (
    fix_malformed_tool_arguments,
    make_llm_completion,
    normalize_tool_call,
    parse_tool_call_arguments,
    prepare_llm_messages,
)
from openhands.sdk.conversation import (
    ConversationCallbackType,
    ConversationState,
    ConversationTokenCallbackType,
    LocalConversation,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    Event,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
    TokenEvent,
    UserRejectObservation,
)
from openhands.sdk.event.condenser import (
    Condensation,
    CondensationRequest,
)
from openhands.sdk.llm import (
    LLMResponse,
    Message,
    MessageToolCall,
    ReasoningItemModel,
    RedactedThinkingBlock,
    TextContent,
    ThinkingBlock,
)
from openhands.sdk.llm.exceptions import (
    FunctionCallValidationError,
    LLMContextWindowExceedError,
    LLMMalformedConversationHistoryError,
)
from openhands.sdk.logger import get_logger
from openhands.sdk.observability.laminar import (
    maybe_init_laminar,
    observe,
    should_enable_observability,
)
from openhands.sdk.observability.utils import extract_action_name
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool import (
    Action,
    Observation,
)


if TYPE_CHECKING:
    from openhands.sdk.tool import ToolDefinition
from openhands.sdk.mcp.tool import MCPToolDefinition
from openhands.sdk.tool.builtins import (
    FinishAction,
    FinishTool,
    ThinkAction,
)


logger = get_logger(__name__)
maybe_init_laminar()


def _tool_has_summary_param(tool: ToolDefinition) -> bool:
    """Return True if the tool's own schema declares ``summary`` as a parameter.

    Checks both regular tool action_type model_fields and MCP tool inputSchema
    so that ``_extract_summary`` can avoid popping the field when it belongs
    to the tool (e.g. Jira's ticket title).
    """
    if "summary" in tool.action_type.model_fields:
        return True
    if isinstance(tool, MCPToolDefinition):
        props = tool.mcp_tool.inputSchema.get("properties", {})
        if "summary" in props:
            return True
    return False


# Maximum number of events to scan during init_state defensive checks.
# SystemPromptEvent must appear within this prefix (at index 0 or 1).
INIT_STATE_PREFIX_SCAN_WINDOW = 3


@dataclass(frozen=True, slots=True)
class _ActionBatch:
    """Immutable result of preparing a batch of actions for execution.

    Owns the full lifecycle of a tool-call batch: preparation (truncation,
    blocked-action partitioning, execution), event emission, and post-batch
    state transitions. Agent-specific logic (iterative refinement, state
    mutation) is injected via callables so the batch stays decoupled from
    the Agent class.
    """

    action_events: list[ActionEvent]
    has_finish: bool
    blocked_reasons: dict[str, str] = field(default_factory=dict)
    results_by_id: dict[str, list[Event]] = field(default_factory=dict)

    @staticmethod
    def _truncate_at_finish(
        action_events: list[ActionEvent],
    ) -> tuple[list[ActionEvent], bool]:
        """
        Return (events[:finish+1], True) or (events, False).
        Discards and logs any calls after FinishTool.
        """
        finish_idx = next(
            (
                i
                for i, ae in enumerate(action_events)
                if ae.tool_name == FinishTool.name
            ),
            None,
        )
        if finish_idx is None:
            return action_events, False

        discarded = action_events[finish_idx + 1 :]
        if discarded:
            names = [ae.tool_name for ae in discarded]
            logger.warning(
                f"Discarding {len(discarded)} tool call(s) "
                f"after FinishTool: {', '.join(names)}"
            )
        return action_events[: finish_idx + 1], True

    @classmethod
    def prepare(
        cls,
        action_events: list[ActionEvent],
        state: ConversationState,
        executor: ParallelToolExecutor,
        tool_runner: Callable[[ActionEvent], list[Event]],
        tools: dict[str, ToolDefinition] | None = None,
    ) -> _ActionBatch:
        """Truncate, partition blocked actions, execute the rest, return the batch."""
        action_events, has_finish = cls._truncate_at_finish(action_events)

        blocked_reasons: dict[str, str] = {}
        executable: list[ActionEvent] = []
        for ae in action_events:
            reason = state.pop_blocked_action(ae.id)
            if reason is not None:
                blocked_reasons[ae.id] = reason
            else:
                executable.append(ae)

        executed_results = executor.execute_batch(executable, tool_runner, tools)
        results_by_id = dict(zip([ae.id for ae in executable], executed_results))

        return cls(
            action_events=action_events,
            has_finish=has_finish,
            blocked_reasons=blocked_reasons,
            results_by_id=results_by_id,
        )

    def emit(self, on_event: ConversationCallbackType) -> None:
        """Emit all events in original action order."""
        for ae in self.action_events:
            reason = self.blocked_reasons.get(ae.id)
            if reason is not None:
                logger.info(f"Action '{ae.tool_name}' blocked by hook: {reason}")
                on_event(
                    UserRejectObservation(
                        action_id=ae.id,
                        tool_name=ae.tool_name,
                        tool_call_id=ae.tool_call_id,
                        rejection_reason=reason,
                        rejection_source="hook",
                    )
                )
            else:
                for event in self.results_by_id[ae.id]:
                    on_event(event)

    def finalize(
        self,
        on_event: ConversationCallbackType,
        check_iterative_refinement: Callable[[ActionEvent], tuple[bool, str | None]],
        mark_finished: Callable[[], None],
    ) -> None:
        """Transition state after FinishTool, or inject iterative-refinement followup.

        Args:
            on_event: Callback for emitting events.
            check_iterative_refinement: Returns (should_continue, followup)
                for a FinishTool action event.
            mark_finished: Called to set the conversation execution status
                to FINISHED when the agent is done.
        """
        # Nothing to finalise: no FinishTool, or it was blocked by a hook.
        if not self.has_finish or self.action_events[-1].id in self.blocked_reasons:
            return

        should_continue, followup = check_iterative_refinement(self.action_events[-1])
        if should_continue and followup:
            on_event(
                MessageEvent(
                    source="user",
                    llm_message=Message(
                        role="user",
                        content=[TextContent(text=followup)],
                    ),
                )
            )
        else:
            mark_finished()


class Agent(CriticMixin, AgentBase):
    """Main agent implementation for OpenHands.

    The Agent class provides the core functionality for running AI agents that can
    interact with tools, process messages, and execute actions. It inherits from
    AgentBase and implements the agent execution logic. Critic-related functionality
    is provided by CriticMixin.

    Attributes:
        llm: The language model instance used for reasoning.
        tools: List of tools available to the agent.
        name: Optional agent identifier.
        system_prompt: Custom system prompt (uses default if not provided).

    Example:
        ```python
        from openhands.sdk import LLM, Agent, Tool
        from pydantic import SecretStr

        llm = LLM(model="claude-sonnet-4-20250514", api_key=SecretStr("key"))
        tools = [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]
        agent = Agent(llm=llm, tools=tools)
        ```
    """

    _parallel_executor: ParallelToolExecutor = PrivateAttr(
        default_factory=ParallelToolExecutor
    )

    def model_post_init(self, __context: object) -> None:
        super().model_post_init(__context)
        self._parallel_executor = ParallelToolExecutor(
            max_workers=self.tool_concurrency_limit
        )

    @model_validator(mode="before")
    @classmethod
    def _add_security_prompt_as_default(cls, data):
        """Ensure llm_security_analyzer=True is always set before initialization."""
        if not isinstance(data, dict):
            return data

        kwargs = data.get("system_prompt_kwargs") or {}
        if not isinstance(kwargs, dict):
            kwargs = {}

        kwargs.setdefault("llm_security_analyzer", True)
        data["system_prompt_kwargs"] = kwargs
        return data

    def init_state(
        self,
        state: ConversationState,
        on_event: ConversationCallbackType,
    ) -> None:
        """Initialize conversation state.

        Invariants enforced by this method:
        - If a SystemPromptEvent is already present, it must be within the first 3
          events (index 0 or 1 in practice; index 2 is included in the scan window
          to detect a user message appearing before the system prompt).
        - A user MessageEvent should not appear before the SystemPromptEvent.

        These invariants keep event ordering predictable for downstream components
        (condenser, UI, etc.) and also prevent accidentally materializing the full
        event history during initialization.
        """
        super().init_state(state, on_event=on_event)

        # Defensive check: Analyze state to detect unexpected initialization scenarios
        # These checks help diagnose issues related to lazy loading and event ordering
        # See: https://github.com/OpenHands/software-agent-sdk/issues/1785
        #
        # NOTE: len() is O(1) for EventLog (file-backed implementation).
        event_count = len(state.events)

        # NOTE: state.events is intentionally an EventsListBase (Sequence-like), not
        # a plain list. Avoid materializing the full history via list(state.events)
        # here (conversations can reach 30k+ events).
        #
        # Invariant: when init_state is called, SystemPromptEvent (if present) must be
        # at index 0 or 1.
        #
        # Rationale:
        # - Local conversations start empty and init_state is responsible for adding
        #   the SystemPromptEvent as the first event.
        # - Remote conversations may receive an initial ConversationStateUpdateEvent
        #   from the agent-server immediately after subscription. In a typical remote
        #   session prefix you may see:
        #     [ConversationStateUpdateEvent, SystemPromptEvent, MessageEvent, ...]
        #
        # We intentionally only inspect the first few events (cheap for both local and
        # remote) to enforce this invariant.
        prefix_events = state.events[:INIT_STATE_PREFIX_SCAN_WINDOW]

        has_system_prompt = any(isinstance(e, SystemPromptEvent) for e in prefix_events)
        has_user_message = any(
            isinstance(e, MessageEvent) and e.source == "user" for e in prefix_events
        )
        # Log state for debugging initialization order issues
        logger.debug(
            f"init_state called: conversation_id={state.id}, "
            f"event_count={event_count}, "
            f"has_system_prompt={has_system_prompt}, "
            f"has_user_message={has_user_message}"
        )

        if has_system_prompt:
            # Restoring/resuming conversations is normal: a system prompt already
            # present means this conversation was initialized previously.
            logger.debug(
                "init_state: SystemPromptEvent already present; skipping init. "
                f"conversation_id={state.id}, event_count={event_count}."
            )
            return

        # Assert: A user message should never appear before the system prompt.
        #
        # NOTE: This is a best-effort check based on the first few events only.
        # Remote conversations can include a ConversationStateUpdateEvent near the
        # start, so we scan a small prefix window.
        if has_user_message:
            event_types = [type(e).__name__ for e in prefix_events]
            logger.error(
                f"init_state: User message found in prefix before SystemPromptEvent! "
                f"conversation_id={state.id}, prefix_events={event_types}"
            )
            raise AssertionError(
                "Unexpected state: user message exists before SystemPromptEvent. "
                f"conversation_id={state.id}, event_count={event_count}, "
                f"prefix_event_types={event_types}."
            )

        # Prepare system message with separate static and dynamic content.
        # The dynamic_context is included as a second content block in the
        # system message (without a cache marker) to enable cross-conversation
        # prompt caching of the static system prompt.
        #
        # Agent pulls secrets from conversation's secret_registry to include
        # them in the dynamic context. This ensures secret names and descriptions
        # appear in the system prompt.
        dynamic_context = self.get_dynamic_context(state)
        event = SystemPromptEvent(
            source="agent",
            system_prompt=TextContent(text=self.static_system_message),
            # Tools are stored as ToolDefinition objects and converted to
            # OpenAI format with security_risk parameter during LLM completion.
            # See make_llm_completion() in agent/utils.py for details.
            tools=list(self.tools_map.values()),
            dynamic_context=TextContent(text=dynamic_context)
            if dynamic_context
            else None,
        )
        on_event(event)

    def get_dynamic_context(self, state: ConversationState) -> str | None:
        """Get dynamic context for the system prompt, including secrets from state.

        This method pulls secrets from the conversation's secret_registry and
        merges them with agent_context to build the dynamic portion of the
        system prompt.

        Args:
            state: The conversation state containing the secret_registry.

        Returns:
            The dynamic context string, or None if no context is configured.
        """
        # Get secret infos from conversation's secret_registry
        secret_infos = state.secret_registry.get_secret_infos()

        if not self.agent_context:
            # No agent_context but we might have secrets from registry
            if secret_infos:
                from openhands.sdk.context.agent_context import AgentContext

                # Create a minimal context just for secrets
                temp_context = AgentContext()
                return temp_context.get_system_message_suffix(
                    llm_model=self.llm.model,
                    llm_model_canonical=self.llm.model_canonical_name,
                    additional_secret_infos=secret_infos,
                )
            return None

        return self.agent_context.get_system_message_suffix(
            llm_model=self.llm.model,
            llm_model_canonical=self.llm.model_canonical_name,
            additional_secret_infos=secret_infos,
        )

    def _execute_actions(
        self,
        conversation: LocalConversation,
        action_events: list[ActionEvent],
        on_event: ConversationCallbackType,
    ) -> None:
        """Prepare a batch, emit results, and handle finish."""
        state = conversation.state
        batch = _ActionBatch.prepare(
            action_events,
            state=state,
            executor=self._parallel_executor,
            tool_runner=lambda ae: self._execute_action_event(conversation, ae),
            tools=self.tools_map,
        )
        batch.emit(on_event)
        batch.finalize(
            on_event=on_event,
            check_iterative_refinement=lambda ae: (
                self._check_iterative_refinement(conversation, ae)
            ),
            mark_finished=lambda: setattr(
                state,
                "execution_status",
                ConversationExecutionStatus.FINISHED,
            ),
        )

    @observe(name="agent.step", ignore_inputs=["state", "on_event"])
    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
        on_token: ConversationTokenCallbackType | None = None,
    ) -> None:
        state = conversation.state
        # Check for pending actions (implicit confirmation)
        # and execute them before sampling new actions.
        pending_actions = ConversationState.get_unmatched_actions(state.events)
        if pending_actions:
            logger.info(
                "Confirmation mode: Executing %d pending action(s)",
                len(pending_actions),
            )
            self._execute_actions(conversation, pending_actions, on_event)
            return

        # Check if the last user message was blocked by a UserPromptSubmit hook
        # If so, skip processing and mark conversation as finished
        if state.last_user_message_id is not None:
            reason = state.pop_blocked_message(state.last_user_message_id)
            if reason is not None:
                logger.info(f"User message blocked by hook: {reason}")
                state.execution_status = ConversationExecutionStatus.FINISHED
                return
        elif state.blocked_messages:
            logger.debug(
                "Blocked messages exist but last_user_message_id is None; "
                "skipping hook check for legacy conversation state."
            )

        # Prepare LLM messages using the utility function
        _messages_or_condensation = prepare_llm_messages(
            state.events, condenser=self.condenser, llm=self.llm
        )

        # Process condensation event before agent sampels another action
        if isinstance(_messages_or_condensation, Condensation):
            on_event(_messages_or_condensation)
            return

        _messages = _messages_or_condensation

        logger.debug(
            "Sending messages to LLM: "
            f"{json.dumps([m.model_dump() for m in _messages[1:]], indent=2)}"
        )

        try:
            llm_response = make_llm_completion(
                self.llm,
                _messages,
                tools=list(self.tools_map.values()),
                on_token=on_token,
            )
        except FunctionCallValidationError as e:
            logger.warning(f"LLM generated malformed function call: {e}")
            error_message = MessageEvent(
                source="user",
                llm_message=Message(
                    role="user",
                    content=[TextContent(text=str(e))],
                ),
            )
            on_event(error_message)
            return
        except LLMMalformedConversationHistoryError as e:
            # The provider rejected the current message history as structurally
            # invalid (for example, broken tool_use/tool_result pairing). Route
            # this into condensation recovery, but keep the logs distinct from
            # true context-window exhaustion so upstream event-stream bugs remain
            # visible.
            if (
                self.condenser is not None
                and self.condenser.handles_condensation_requests()
            ):
                logger.warning(
                    "LLM raised malformed conversation history error, "
                    "triggering condensation retry with condensed history: "
                    f"{e}"
                )
                on_event(CondensationRequest())
                return
            logger.warning(
                "LLM raised malformed conversation history error but no "
                "condenser can handle condensation requests. This usually "
                "indicates an upstream event-stream or resume bug: "
                f"{e}"
            )
            raise e
        except LLMContextWindowExceedError as e:
            # If condenser is available and handles requests, trigger condensation
            if (
                self.condenser is not None
                and self.condenser.handles_condensation_requests()
            ):
                logger.warning(
                    "LLM raised context window exceeded error, triggering condensation"
                )
                on_event(CondensationRequest())
                return
            # No condenser available or doesn't handle requests; log helpful warning
            self._log_context_window_exceeded_warning()
            raise e

        # LLMResponse already contains the converted message and metrics snapshot
        message: Message = llm_response.message

        # Check if this is a reasoning-only response (e.g., from reasoning models)
        # or a message-only response without tool calls
        has_reasoning = (
            message.responses_reasoning_item is not None
            or message.reasoning_content is not None
            or (message.thinking_blocks and len(message.thinking_blocks) > 0)
        )
        has_content = any(
            isinstance(c, TextContent) and c.text.strip() for c in message.content
        )

        if message.tool_calls and len(message.tool_calls) > 0:
            if not all(isinstance(c, TextContent) for c in message.content):
                logger.warning(
                    "LLM returned tool calls but message content is not all "
                    "TextContent - ignoring non-text content"
                )

            # Generate unique batch ID for this LLM response
            thought_content = [c for c in message.content if isinstance(c, TextContent)]

            action_events: list[ActionEvent] = []
            for i, tool_call in enumerate(message.tool_calls):
                action_event = self._get_action_event(
                    tool_call,
                    conversation=conversation,
                    llm_response_id=llm_response.id,
                    on_event=on_event,
                    security_analyzer=state.security_analyzer,
                    thought=thought_content
                    if i == 0
                    else [],  # Only first gets thought
                    # Only first gets reasoning content
                    reasoning_content=message.reasoning_content if i == 0 else None,
                    # Only first gets thinking blocks
                    thinking_blocks=list(message.thinking_blocks) if i == 0 else [],
                    responses_reasoning_item=message.responses_reasoning_item
                    if i == 0
                    else None,
                )
                if action_event is None:
                    continue
                action_events.append(action_event)

            # Handle confirmation mode - exit early if actions need confirmation
            if self._requires_user_confirmation(state, action_events):
                return

            if action_events:
                self._execute_actions(conversation, action_events, on_event)

            # Emit VLLM token ids if enabled before returning
            self._maybe_emit_vllm_tokens(llm_response, on_event)
            return

        # No tool calls - emit message event for reasoning or content responses
        if not has_reasoning and not has_content:
            logger.warning("LLM produced empty response - continuing agent loop")

        msg_event = MessageEvent(
            source="agent",
            llm_message=message,
            llm_response_id=llm_response.id,
        )
        # Run critic evaluation if configured for finish_and_message mode
        if self.critic is not None and self.critic.mode == "finish_and_message":
            critic_result = self._evaluate_with_critic(conversation, msg_event)
            if critic_result is not None:
                # Create new event with critic result
                msg_event = msg_event.model_copy(
                    update={"critic_result": critic_result}
                )
        on_event(msg_event)

        # Emit VLLM token ids if enabled
        self._maybe_emit_vllm_tokens(llm_response, on_event)

        # Finish conversation if LLM produced content (awaits user input)
        # Continue if only reasoning without content (e.g., GPT-5 codex thinking)
        if has_content:
            logger.debug("LLM produced a message response - awaits user input")
            state.execution_status = ConversationExecutionStatus.FINISHED
            return

        # When the LLM produced no tool call and no user-facing content,
        # inject corrective feedback so the model knows it must act.
        # This prevents the monologue stuck-detector from firing when the
        # model simply forgot to emit a function call (common with Qwen,
        # which sometimes places tool-call XML inside reasoning_content).
        if not has_content:
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

    def _requires_user_confirmation(
        self, state: ConversationState, action_events: list[ActionEvent]
    ) -> bool:
        """
        Decide whether user confirmation is needed to proceed.

        Rules:
            1. Confirmation mode is enabled
            2. Every action requires confirmation
            3. A single `FinishAction` never requires confirmation
            4. A single `ThinkAction` never requires confirmation
        """
        # A single `FinishAction` or `ThinkAction` never requires confirmation
        if len(action_events) == 1 and isinstance(
            action_events[0].action, (FinishAction, ThinkAction)
        ):
            return False

        # If there are no actions there is nothing to confirm
        if len(action_events) == 0:
            return False

        # If a security analyzer is registered, use it to grab the risks of the actions
        # involved. If not, we'll set the risks to UNKNOWN.
        if state.security_analyzer is not None:
            risks = [
                risk
                for _, risk in state.security_analyzer.analyze_pending_actions(
                    action_events
                )
            ]
        else:
            risks = [risk.SecurityRisk.UNKNOWN] * len(action_events)

        # Grab the confirmation policy from the state and pass in the risks.
        if any(state.confirmation_policy.should_confirm(risk) for risk in risks):
            state.execution_status = (
                ConversationExecutionStatus.WAITING_FOR_CONFIRMATION
            )
            return True

        return False

    def _extract_security_risk(
        self,
        arguments: dict,
        tool_name: str,
        read_only_tool: bool,
        security_analyzer: analyzer.SecurityAnalyzerBase | None = None,
    ) -> risk.SecurityRisk:
        requires_sr = isinstance(security_analyzer, LLMSecurityAnalyzer)
        raw = arguments.pop("security_risk", None)

        # Default risk value for action event
        # Tool is marked as read-only so security risk can be ignored
        if read_only_tool:
            return risk.SecurityRisk.UNKNOWN

        # Raises exception if failed to pass risk field when expected
        # Exception will be sent back to agent as error event
        # Strong models like GPT-5 can correct itself by retrying
        if requires_sr and raw is None:
            raise ValueError(
                f"Failed to provide security_risk field in tool '{tool_name}'"
            )

        # When no security analyzer is configured, ignore any security_risk field
        # from LLM and return UNKNOWN. This ensures that security_risk is only
        # evaluated when a security analyzer is explicitly set.
        if security_analyzer is None:
            return risk.SecurityRisk.UNKNOWN

        # When using non-LLM security analyzer without security risk field
        # safely ignore missing security risk fields
        if not requires_sr and raw is None:
            return risk.SecurityRisk.UNKNOWN

        # Raises exception if invalid risk enum passed by LLM
        security_risk = risk.SecurityRisk(raw)
        return security_risk

    def _extract_summary(
        self,
        tool_name: str,
        arguments: dict,
        tool: ToolDefinition | None = None,
    ) -> str:
        """Extract and validate the summary field from tool arguments.

        Summary field is always requested but optional - if LLM doesn't provide
        it or provides invalid data, we generate a default summary using the
        tool name and arguments.

        When the tool's own schema declares ``summary`` as a real parameter
        (e.g. Jira's ticket title), the value is **read but not removed** so
        that ``action_from_arguments`` validation still succeeds.  The tool's
        own ``summary`` value is reused as the event-level summary because it
        is usually descriptive (e.g. a Jira ticket title).

        Args:
            tool_name: Name of the tool being called
            arguments: Dictionary of tool arguments from LLM
            tool: The tool definition (used to check if "summary" is a
                declared parameter of the tool's schema)

        Returns:
            The summary string - either from LLM or a default generated one
        """
        if tool is not None and _tool_has_summary_param(tool):
            # "summary" belongs to the tool — read it but don't pop it.
            # Reuse the tool's own value as the event summary (e.g. a Jira
            # ticket title is a reasonable description of the action).
            summary = arguments.get("summary")
            if isinstance(summary, str) and summary.strip():
                return summary.strip()
            args_str = json.dumps(arguments)
            return f"{tool_name}: {args_str}"

        summary = arguments.pop("summary", None)

        # If valid summary provided by LLM, use it
        if summary is not None and isinstance(summary, str) and summary.strip():
            return summary

        # Generate default summary: {tool_name}: {arguments}
        args_str = json.dumps(arguments)
        return f"{tool_name}: {args_str}"

    def _emit_tool_error(
        self,
        *,
        error: str,
        tool_name: str,
        tool_call: MessageToolCall,
        llm_response_id: str,
        on_event: ConversationCallbackType,
        thought: list[TextContent] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] | None = None,
        responses_reasoning_item: ReasoningItemModel | None = None,
    ) -> None:
        tc_event = ActionEvent(
            source="agent",
            thought=thought or [],
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks or [],
            responses_reasoning_item=responses_reasoning_item,
            tool_call=tool_call,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            llm_response_id=llm_response_id,
            action=None,
        )
        on_event(tc_event)
        on_event(
            AgentErrorEvent(
                error=error,
                tool_name=tool_name,
                tool_call_id=tool_call.id,
            )
        )

    def _get_action_event(
        self,
        tool_call: MessageToolCall,
        conversation: LocalConversation,
        llm_response_id: str,
        on_event: ConversationCallbackType,
        security_analyzer: analyzer.SecurityAnalyzerBase | None = None,
        thought: list[TextContent] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[ThinkingBlock | RedactedThinkingBlock] | None = None,
        responses_reasoning_item: ReasoningItemModel | None = None,
    ) -> ActionEvent | None:
        """Converts a tool call into an ActionEvent, validating arguments.

        NOTE: state will be mutated in-place.
        """
        requested_tool_name = tool_call.name
        tool: ToolDefinition | None = None
        normalized_tool_call = tool_call

        # Validate arguments
        security_risk: risk.SecurityRisk = risk.SecurityRisk.UNKNOWN
        try:
            # Parse and normalize tool call (handles aliasing, terminal fallback,
            # and argument sanitization)
            arguments = parse_tool_call_arguments(tool_call.arguments)
            tool_name, arguments = normalize_tool_call(
                requested_tool_name,
                arguments,
                self.tools_map.keys(),
            )

            tool = self.tools_map.get(tool_name, None)
            if tool is None:
                available = list(self.tools_map.keys())
                err = f"Tool '{tool_name}' not found. Available: {available}"
                logger.error(err)
                self._emit_tool_error(
                    error=err,
                    tool_name=tool_name,
                    tool_call=tool_call,
                    llm_response_id=llm_response_id,
                    on_event=on_event,
                    thought=thought,
                    reasoning_content=reasoning_content,
                    thinking_blocks=thinking_blocks,
                    responses_reasoning_item=responses_reasoning_item,
                )
                return

            arguments = fix_malformed_tool_arguments(arguments, tool.action_type)
            normalized_tool_call = tool_call.model_copy(
                update={
                    "name": tool_name,
                    "arguments": json.dumps(arguments),
                }
            )
            security_risk = self._extract_security_risk(
                arguments,
                tool.name,
                tool.annotations.readOnlyHint if tool.annotations else False,
                security_analyzer,
            )
            assert "security_risk" not in arguments, (
                "Unexpected 'security_risk' key found in tool arguments"
            )

            summary = self._extract_summary(tool.name, arguments, tool=tool)

            action: Action = tool.action_from_arguments(arguments)

        except (ValueError, json.JSONDecodeError, ValidationError) as e:
            # normalize_tool_call or json.loads or Pydantic validation raised an
            # error. Build concise error message with parameter names only (not values).
            parsed_args = parse_tool_call_arguments(tool_call.arguments)
            keys = list(parsed_args.keys()) if isinstance(parsed_args, dict) else None
            params = (
                f"Parameters provided: {keys}"
                if keys is not None
                else "Arguments: unparseable JSON"
            )
            display_tool_name = tool.name if tool is not None else requested_tool_name
            err = f"Error validating tool '{display_tool_name}': {e}. {params}"
            # Persist assistant function_call so next turn has matching call_id
            tc_event = ActionEvent(
                source="agent",
                thought=thought or [],
                reasoning_content=reasoning_content,
                thinking_blocks=thinking_blocks or [],
                responses_reasoning_item=responses_reasoning_item,
                tool_call=tool_call,
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                llm_response_id=llm_response_id,
                action=None,
            )
            on_event(tc_event)
            self._emit_tool_error(
                error=err,
                tool_name=display_tool_name,
                tool_call=tool_call,
                llm_response_id=llm_response_id,
                on_event=on_event,
                thought=thought,
                reasoning_content=reasoning_content,
                thinking_blocks=thinking_blocks,
                responses_reasoning_item=responses_reasoning_item,
            )
            return

        # Create initial action event
        action_event = ActionEvent(
            action=action,
            thought=thought or [],
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks or [],
            responses_reasoning_item=responses_reasoning_item,
            tool_name=tool.name,
            tool_call_id=normalized_tool_call.id,
            tool_call=normalized_tool_call,
            llm_response_id=llm_response_id,
            security_risk=security_risk,
            summary=summary,
        )

        # Run critic evaluation if configured
        if self._should_evaluate_with_critic(action):
            critic_result = self._evaluate_with_critic(conversation, action_event)
            if critic_result is not None:
                # Create new event with critic result
                action_event = action_event.model_copy(
                    update={"critic_result": critic_result}
                )

        on_event(action_event)
        return action_event

    @observe()
    def _execute_action_event(
        self,
        conversation: LocalConversation,
        action_event: ActionEvent,
    ) -> list[Event]:
        """Execute a single tool and return the resulting events.

        Called from parallel threads by _execute_actions. This method must
        not mutate shared conversation state (blocked_actions,
        execution_status) — those transitions are handled by the caller
        on the main thread.

        Note: the tool itself receives ``conversation`` and may mutate it
        (e.g. filesystem, working directory). Thread safety of individual
        tools is the tool's responsibility.

        Returns a list of events (observation or error). Events are NOT
        emitted here — the caller is responsible for emitting them in order.
        """
        tool = self.tools_map.get(action_event.tool_name, None)
        if tool is None:
            raise RuntimeError(
                f"Tool '{action_event.tool_name}' not found. This should not happen "
                "as it was checked earlier."
            )

        # Execute actions!
        try:
            if should_enable_observability():
                tool_name = extract_action_name(action_event)
                observation: Observation = observe(name=tool_name, span_type="TOOL")(
                    tool
                )(action_event.action, conversation)
            else:
                observation = tool(action_event.action, conversation)
            assert isinstance(observation, Observation), (
                f"Tool '{tool.name}' executor must return an Observation"
            )
        except ValueError as e:
            # Tool execution raised a ValueError (e.g., invalid argument combination)
            # Convert to AgentErrorEvent so the agent can correct itself
            err = f"Error executing tool '{tool.name}': {e}"
            logger.warning(err)
            error_event = AgentErrorEvent(
                error=err,
                tool_name=tool.name,
                tool_call_id=action_event.tool_call.id,
            )
            return [error_event]

        obs_event = ObservationEvent(
            observation=observation,
            action_id=action_event.id,
            tool_name=tool.name,
            tool_call_id=action_event.tool_call.id,
        )
        return [obs_event]

    def _maybe_emit_vllm_tokens(
        self, llm_response: LLMResponse, on_event: ConversationCallbackType
    ) -> None:
        if (
            "return_token_ids" in self.llm.litellm_extra_body
        ) and self.llm.litellm_extra_body["return_token_ids"]:
            token_event = TokenEvent(
                source="agent",
                prompt_token_ids=llm_response.raw_response["prompt_token_ids"],
                response_token_ids=llm_response.raw_response["choices"][0][
                    "provider_specific_fields"
                ]["token_ids"],
            )
            on_event(token_event)

    def _log_context_window_exceeded_warning(self) -> None:
        """Log a helpful warning when context window is exceeded without a condenser."""
        if self.condenser is None:
            situation = (
                "The LLM's context window has been exceeded, but no condenser is "
                "configured."
            )
            config = f"  • Condenser: None\n  • LLM Model: {self.llm.model}"
            advice = (
                "To prevent this error, configure a condenser to automatically "
                "summarize\n"
                "conversation history when it gets too long."
            )
        else:
            condenser_type = type(self.condenser).__name__
            handles_requests = self.condenser.handles_condensation_requests()
            condenser_config = self.condenser.model_dump(
                exclude={"llm"}, exclude_none=True
            )
            condenser_llm_obj = getattr(self.condenser, "llm", None)
            condenser_llm = (
                condenser_llm_obj.model if condenser_llm_obj is not None else "N/A"
            )

            situation = "The LLM's context window has been exceeded."
            config = (
                f"  • Condenser Type: {condenser_type}\n"
                f"  • Handles Condensation Requests: {handles_requests}\n"
                f"  • Condenser LLM: {condenser_llm}\n"
                f"  • Agent LLM Model: {self.llm.model}\n"
                f"  • Condenser Config: {json.dumps(condenser_config, indent=4)}"
            )
            advice = (
                "Your condenser is configured but does not handle condensation "
                "requests\n"
                "(handles_condensation_requests() returned False).\n"
                "\n"
                "To fix this:\n"
                "  1. Use LLMSummarizingCondenser which handles condensation "
                "requests, OR\n"
                "  2. Implement handles_condensation_requests() in your custom "
                "condenser"
            )

        logger.warning(
            "\n"
            "=" * 80 + "\n"
            "⚠️  CONTEXT WINDOW EXCEEDED ERROR\n"
            "=" * 80 + "\n"
            "\n"
            f"{situation}\n"
            "\n"
            "Current configuration:\n"
            f"{config}\n"
            "\n"
            f"{advice}\n"
            "\n"
            "Example configuration:\n"
            "\n"
            "  from openhands.sdk import Agent, LLM\n"
            "  from openhands.sdk.context.condenser import "
            "LLMSummarizingCondenser\n"
            "\n"
            "  agent = Agent(\n"
            "      llm=LLM(model='your-model'),\n"
            "      condenser=LLMSummarizingCondenser(\n"
            "          llm=LLM(model='your-model'),\n"
            "          max_size=240,\n"
            "          keep_first=2\n"
            "      )\n"
            "  )\n"
            "\n"
            "For more information, see: "
            "https://docs.openhands.dev/sdk/guides/context-condenser\n"
            "=" * 80
        )
