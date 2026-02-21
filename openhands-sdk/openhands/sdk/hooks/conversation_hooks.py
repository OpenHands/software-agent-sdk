"""Hook integration for conversations."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openhands.sdk.event import (
    ActionEvent,
    Event,
    HookExecutionEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.hooks.executor import HookResult
from openhands.sdk.hooks.manager import HookManager
from openhands.sdk.hooks.types import HookEventType
from openhands.sdk.llm import TextContent
from openhands.sdk.logger import get_logger


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

logger = get_logger(__name__)

# Type alias for the callback function that emits events
EventEmitter = Callable[[Event], None]


class HookEventProcessor:
    """Processes events and runs hooks at appropriate points.

    Call set_conversation_state() after creating Conversation for blocking to work.

    HookExecutionEvent is emitted for each hook execution when emit_hook_events=True,
    providing full observability into hook execution for clients.
    """

    def __init__(
        self,
        hook_manager: HookManager,
        original_callback: Any = None,
        emit_hook_events: bool = True,
    ):
        self.hook_manager = hook_manager
        self.original_callback = original_callback
        self._conversation_state: ConversationState | None = None
        self.emit_hook_events = emit_hook_events

    def set_conversation_state(self, state: "ConversationState") -> None:
        """Set conversation state for blocking support."""
        self._conversation_state = state

    def _emit_hook_execution_event(
        self,
        hook_event_type: HookEventType,
        hook_command: str,
        result: HookResult,
        tool_name: str | None = None,
        action_id: str | None = None,
        message_id: str | None = None,
        hook_input: dict[str, Any] | None = None,
    ) -> None:
        """Emit a HookExecutionEvent for observability."""
        if not self.emit_hook_events or not self.original_callback:
            return

        event = HookExecutionEvent(
            hook_event_type=hook_event_type.value,
            hook_command=hook_command,
            tool_name=tool_name,
            success=result.success,
            blocked=result.blocked,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            reason=result.reason,
            additional_context=result.additional_context,
            error=result.error,
            action_id=action_id,
            message_id=message_id,
            hook_input=hook_input,
        )
        self.original_callback(event)

    def on_event(self, event: Event) -> None:
        """Process an event and run appropriate hooks."""
        # Track the event to pass to callbacks (may be modified by hooks)
        callback_event = event

        # Run PreToolUse hooks for action events
        if isinstance(event, ActionEvent) and event.action is not None:
            self._handle_pre_tool_use(event)

        # Run PostToolUse hooks for observation events
        if isinstance(event, ObservationEvent):
            self._handle_post_tool_use(event)

        # Run UserPromptSubmit hooks for user messages
        if isinstance(event, MessageEvent) and event.source == "user":
            callback_event = self._handle_user_prompt_submit(event)

        # Call original callback with (possibly modified) event
        if self.original_callback:
            self.original_callback(callback_event)

    def _handle_pre_tool_use(self, event: ActionEvent) -> None:
        """Handle PreToolUse hooks. Blocked actions are marked in conversation state."""
        if not self.hook_manager.has_hooks(HookEventType.PRE_TOOL_USE):
            return

        tool_name = event.tool_name
        tool_input: dict[str, Any] = {}

        # Extract tool input from action
        if event.action is not None:
            try:
                tool_input = event.action.model_dump()
            except Exception as e:
                logger.debug(f"Could not extract tool input: {e}")

        # Get hooks to emit events with command info
        hooks = self.hook_manager.config.get_hooks_for_event(
            HookEventType.PRE_TOOL_USE, tool_name
        )

        should_continue, results = self.hook_manager.run_pre_tool_use(
            tool_name=tool_name,
            tool_input=tool_input,
        )

        # Emit HookExecutionEvents for each hook
        for hook, result in zip(hooks, results, strict=False):
            self._emit_hook_execution_event(
                hook_event_type=HookEventType.PRE_TOOL_USE,
                hook_command=hook.command,
                result=result,
                tool_name=tool_name,
                action_id=event.id,
                hook_input={"tool_name": tool_name, "tool_input": tool_input},
            )

        if not should_continue:
            reason = self.hook_manager.get_blocking_reason(results)
            logger.warning(f"Hook blocked action {tool_name}: {reason}")

            # Mark this action as blocked in the conversation state
            # The Agent will check this and emit a rejection instead of executing
            if self._conversation_state is not None:
                block_reason = reason or "Blocked by hook"
                self._conversation_state.block_action(event.id, block_reason)
            else:
                logger.warning(
                    "Cannot block action: conversation state not set. "
                    "Call processor.set_conversation_state(conversation.state) "
                    "after creating the Conversation."
                )

    def _handle_post_tool_use(self, event: ObservationEvent) -> None:
        """Handle PostToolUse hooks after an action completes."""
        if not self.hook_manager.has_hooks(HookEventType.POST_TOOL_USE):
            return

        # O(1) lookup of corresponding action from state events
        action_event = None
        if self._conversation_state is not None:
            try:
                idx = self._conversation_state.events.get_index(event.action_id)
                event_at_idx = self._conversation_state.events[idx]
                if isinstance(event_at_idx, ActionEvent):
                    action_event = event_at_idx
            except KeyError:
                pass  # action not found

        if action_event is None:
            return

        tool_name = event.tool_name
        tool_input: dict[str, Any] = {}
        tool_response: dict[str, Any] = {}

        # Extract tool input from action
        if action_event.action is not None:
            try:
                tool_input = action_event.action.model_dump()
            except Exception as e:
                logger.debug(f"Could not extract tool input: {e}")

        # Extract structured tool response from observation
        if event.observation is not None:
            try:
                tool_response = event.observation.model_dump()
            except Exception as e:
                logger.debug(f"Could not extract tool response: {e}")

        # Get hooks to emit events with command info
        hooks = self.hook_manager.config.get_hooks_for_event(
            HookEventType.POST_TOOL_USE, tool_name
        )

        results = self.hook_manager.run_post_tool_use(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_response=tool_response,
        )

        # Emit HookExecutionEvents for each hook and log errors
        for hook, result in zip(hooks, results, strict=False):
            self._emit_hook_execution_event(
                hook_event_type=HookEventType.POST_TOOL_USE,
                hook_command=hook.command,
                result=result,
                tool_name=tool_name,
                action_id=action_event.id,
                hook_input={
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "tool_response": tool_response,
                },
            )
            if result.error:
                logger.warning(f"PostToolUse hook error: {result.error}")

    def _handle_user_prompt_submit(self, event: MessageEvent) -> MessageEvent:
        """Handle UserPromptSubmit hooks before processing a user message.

        Returns the (possibly modified) event. If hooks inject additional_context,
        a new MessageEvent is created with the context appended to extended_content.
        """
        if not self.hook_manager.has_hooks(HookEventType.USER_PROMPT_SUBMIT):
            return event

        # Extract message text
        message = ""
        if event.llm_message and event.llm_message.content:
            for content in event.llm_message.content:
                if isinstance(content, TextContent):
                    message += content.text

        # Get hooks to emit events with command info
        hooks = self.hook_manager.config.get_hooks_for_event(
            HookEventType.USER_PROMPT_SUBMIT
        )

        should_continue, additional_context, results = (
            self.hook_manager.run_user_prompt_submit(message=message)
        )

        # Emit HookExecutionEvents for each hook
        for hook, result in zip(hooks, results, strict=False):
            self._emit_hook_execution_event(
                hook_event_type=HookEventType.USER_PROMPT_SUBMIT,
                hook_command=hook.command,
                result=result,
                message_id=event.id,
                hook_input={"message": message},
            )

        if not should_continue:
            reason = self.hook_manager.get_blocking_reason(results)
            logger.warning(f"Hook blocked user message: {reason}")

            # Mark this message as blocked in the conversation state
            # The Agent will check this and skip processing the message
            if self._conversation_state is not None:
                block_reason = reason or "Blocked by hook"
                self._conversation_state.block_message(event.id, block_reason)
            else:
                logger.warning(
                    "Cannot block message: conversation state not set. "
                    "Call processor.set_conversation_state(conversation.state) "
                    "after creating the Conversation."
                )

        # Inject additional_context into extended_content
        if additional_context:
            logger.debug(f"Hook injecting context: {additional_context[:100]}...")
            new_extended_content = list(event.extended_content) + [
                TextContent(text=additional_context)
            ]
            # MessageEvent is frozen, so create a new one
            event = MessageEvent(
                source=event.source,
                llm_message=event.llm_message,
                llm_response_id=event.llm_response_id,
                activated_skills=event.activated_skills,
                extended_content=new_extended_content,
                sender=event.sender,
            )

        return event

    def is_action_blocked(self, action_id: str) -> bool:
        """Check if an action was blocked by a hook."""
        if self._conversation_state is None:
            return False
        return action_id in self._conversation_state.blocked_actions

    def is_message_blocked(self, message_id: str) -> bool:
        """Check if a message was blocked by a hook."""
        if self._conversation_state is None:
            return False
        return message_id in self._conversation_state.blocked_messages

    def run_session_start(self) -> None:
        """Run SessionStart hooks. Call after conversation is created."""
        hooks = self.hook_manager.config.get_hooks_for_event(
            HookEventType.SESSION_START
        )
        results = self.hook_manager.run_session_start()

        for hook, result in zip(hooks, results, strict=False):
            self._emit_hook_execution_event(
                hook_event_type=HookEventType.SESSION_START,
                hook_command=hook.command,
                result=result,
            )
            if result.error:
                logger.warning(f"SessionStart hook error: {result.error}")

    def run_session_end(self) -> None:
        """Run SessionEnd hooks. Call before conversation is closed."""
        hooks = self.hook_manager.config.get_hooks_for_event(HookEventType.SESSION_END)
        results = self.hook_manager.run_session_end()

        for hook, result in zip(hooks, results, strict=False):
            self._emit_hook_execution_event(
                hook_event_type=HookEventType.SESSION_END,
                hook_command=hook.command,
                result=result,
            )
            if result.error:
                logger.warning(f"SessionEnd hook error: {result.error}")

    def run_stop(self, reason: str | None = None) -> tuple[bool, str | None]:
        """Run Stop hooks. Returns (should_stop, feedback)."""
        if not self.hook_manager.has_hooks(HookEventType.STOP):
            return True, None

        hooks = self.hook_manager.config.get_hooks_for_event(HookEventType.STOP)
        should_stop, results = self.hook_manager.run_stop(reason=reason)

        # Emit events and log errors
        for hook, result in zip(hooks, results, strict=False):
            self._emit_hook_execution_event(
                hook_event_type=HookEventType.STOP,
                hook_command=hook.command,
                result=result,
                hook_input={"reason": reason} if reason else None,
            )
            if result.error:
                logger.warning(f"Stop hook error: {result.error}")

        # Collect feedback if denied
        feedback = None
        if not should_stop:
            reason_text = self.hook_manager.get_blocking_reason(results)
            logger.info(f"Stop hook denied stopping: {reason_text}")
            feedback_parts = [
                r.additional_context for r in results if r.additional_context
            ]
            if feedback_parts:
                feedback = "\n".join(feedback_parts)
            elif reason_text:
                feedback = reason_text

        return should_stop, feedback


def create_hook_callback(
    hook_config: HookConfig | None = None,
    working_dir: str | None = None,
    session_id: str | None = None,
    original_callback: Any = None,
    emit_hook_events: bool = True,
) -> tuple[HookEventProcessor, Any]:
    """Create a hook-enabled event callback. Returns (processor, callback).

    Args:
        hook_config: Configuration for hooks to run.
        working_dir: Working directory for hook execution.
        session_id: Session ID passed to hooks.
        original_callback: Callback to chain after hook processing.
        emit_hook_events: If True, emit HookExecutionEvent for each hook execution.
            Defaults to True for full observability.

    Returns:
        Tuple of (HookEventProcessor, callback function).
    """
    hook_manager = HookManager(
        config=hook_config,
        working_dir=working_dir,
        session_id=session_id,
    )

    processor = HookEventProcessor(
        hook_manager=hook_manager,
        original_callback=original_callback,
        emit_hook_events=emit_hook_events,
    )

    return processor, processor.on_event
