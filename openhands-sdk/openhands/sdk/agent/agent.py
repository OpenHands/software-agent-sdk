import json

from pydantic import ValidationError, model_validator

import openhands.sdk.security.analyzer as analyzer
import openhands.sdk.security.risk as risk
from openhands.sdk.agent.base import AgentBase
from openhands.sdk.agent.utils import (
    fix_malformed_tool_arguments,
    make_llm_completion,
    prepare_llm_messages,
)
from openhands.sdk.conversation import (
    ConversationCallbackType,
    ConversationState,
    LocalConversation,
)
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    LLMConvertibleEvent,
    MessageEvent,
    ObservationEvent,
    SystemPromptEvent,
    TokenEvent,
)
from openhands.sdk.event.condenser import Condensation, CondensationRequest
from openhands.sdk.llm import (
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
from openhands.sdk.tool.builtins import (
    FinishAction,
    FinishTool,
    ThinkAction,
)


logger = get_logger(__name__)
maybe_init_laminar()


class Agent(AgentBase):
    """Main agent implementation for OpenHands.

    The Agent class provides the core functionality for running AI agents that can
    interact with tools, process messages, and execute actions. It inherits from
    AgentBase and implements the agent execution logic.

    Example:
        >>> from openhands.sdk import LLM, Agent, Tool
        >>> llm = LLM(model="claude-sonnet-4-20250514", api_key=SecretStr("key"))
        >>> tools = [Tool(name="TerminalTool"), Tool(name="FileEditorTool")]
        >>> agent = Agent(llm=llm, tools=tools)
    """

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
        super().init_state(state, on_event=on_event)
        # TODO(openhands): we should add test to test this init_state will actually
        # modify state in-place

        llm_convertible_messages = [
            event for event in state.events if isinstance(event, LLMConvertibleEvent)
        ]
        if len(llm_convertible_messages) == 0:
            # Prepare system message
            event = SystemPromptEvent(
                source="agent",
                system_prompt=TextContent(text=self.system_message),
                # Always expose a 'security_risk' parameter in tool schemas.
                # This ensures the schema remains consistent, even if the
                # security analyzer is disabled. Validation of this field
                # happens dynamically at runtime depending on the analyzer
                # configured. This allows weaker models to omit risk field
                # and bypass validation requirements when analyzer is disabled.
                # For detailed logic, see `_extract_security_risk` method.
                tools=[
                    t.to_openai_tool(add_security_risk_prediction=True)
                    for t in self.tools_map.values()
                ],
            )
            on_event(event)

    def _execute_actions(
        self,
        conversation: LocalConversation,
        action_events: list[ActionEvent],
        on_event: ConversationCallbackType,
    ):
        for action_event in action_events:
            self._execute_action_event(conversation, action_event, on_event=on_event)

    @observe(name="agent.step", ignore_inputs=["state", "on_event"])
    def step(
        self,
        conversation: LocalConversation,
        on_event: ConversationCallbackType,
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

        # Prepare LLM messages using the utility function
        _messages_or_condensation = prepare_llm_messages(
            state.events, condenser=self.condenser
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
                self.llm, _messages, tools=list(self.tools_map.values())
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
        except LLMContextWindowExceedError:
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
            # No condenser available; re-raise for client handling
            raise

        # LLMResponse already contains the converted message and metrics snapshot
        message: Message = llm_response.message

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

        else:
            logger.debug("LLM produced a message response - awaits user input")
            state.execution_status = ConversationExecutionStatus.FINISHED
            msg_event = MessageEvent(
                source="agent",
                llm_message=message,
                llm_response_id=llm_response.id,
            )
            on_event(msg_event)

        # If using VLLM, we can get the raw prompt and response tokens
        # that can be useful for RL training.
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

        # When using weaker models without security analyzer
        # safely ignore missing security risk fields
        if not requires_sr and raw is None:
            return risk.SecurityRisk.UNKNOWN

        # Raises exception if invalid risk enum passed by LLM
        security_risk = risk.SecurityRisk(raw)
        return security_risk

    def _get_action_event(
        self,
        tool_call: MessageToolCall,
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
        tool_name = tool_call.name
        tool = self.tools_map.get(tool_name, None)
        # Handle non-existing tools
        if tool is None:
            available = list(self.tools_map.keys())
            err = f"Tool '{tool_name}' not found. Available: {available}"
            logger.error(err)
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
            event = AgentErrorEvent(
                error=err,
                tool_name=tool_name,
                tool_call_id=tool_call.id,
            )
            on_event(event)
            return

        # Validate arguments
        security_risk: risk.SecurityRisk = risk.SecurityRisk.UNKNOWN
        try:
            arguments = json.loads(tool_call.arguments)

            # Fix malformed arguments (e.g., JSON strings for list/dict fields)
            arguments = fix_malformed_tool_arguments(arguments, tool.action_type)
            security_risk = self._extract_security_risk(
                arguments,
                tool.name,
                tool.annotations.readOnlyHint if tool.annotations else False,
                security_analyzer,
            )
            assert "security_risk" not in arguments, (
                "Unexpected 'security_risk' key found in tool arguments"
            )

            action: Action = tool.action_from_arguments(arguments)
        except (json.JSONDecodeError, ValidationError, ValueError) as e:
            err = (
                f"Error validating args {tool_call.arguments} for tool "
                f"'{tool.name}': {e}"
            )
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
            event = AgentErrorEvent(
                error=err,
                tool_name=tool_name,
                tool_call_id=tool_call.id,
            )
            on_event(event)
            return

        action_event = ActionEvent(
            action=action,
            thought=thought or [],
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks or [],
            responses_reasoning_item=responses_reasoning_item,
            tool_name=tool.name,
            tool_call_id=tool_call.id,
            tool_call=tool_call,
            llm_response_id=llm_response_id,
            security_risk=security_risk,
        )
        on_event(action_event)
        return action_event

    @observe(ignore_inputs=["state", "on_event"])
    def _execute_action_event(
        self,
        conversation: LocalConversation,
        action_event: ActionEvent,
        on_event: ConversationCallbackType,
    ):
        """Execute an action event and update the conversation state.

        It will call the tool's executor and update the state & call callback fn
        with the observation.
        """
        state = conversation.state
        tool = self.tools_map.get(action_event.tool_name, None)
        if tool is None:
            raise RuntimeError(
                f"Tool '{action_event.tool_name}' not found. This should not happen "
                "as it was checked earlier."
            )

        # Execute actions!
        if should_enable_observability():
            tool_name = extract_action_name(action_event)
            observation: Observation = observe(name=tool_name, span_type="TOOL")(tool)(
                action_event.action, conversation
            )
        else:
            observation = tool(action_event.action, conversation)
        assert isinstance(observation, Observation), (
            f"Tool '{tool.name}' executor must return an Observation"
        )

        obs_event = ObservationEvent(
            observation=observation,
            action_id=action_event.id,
            tool_name=tool.name,
            tool_call_id=action_event.tool_call.id,
        )
        on_event(obs_event)

        # Set conversation state
        if tool.name == FinishTool.name:
            state.execution_status = ConversationExecutionStatus.FINISHED
        return obs_event
