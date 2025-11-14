from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.security import risk
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.tool.builtins.think import ThinkAction


class SecurityService:
    def __init__(
        self,
        state: "ConversationState",
    ):
        self._state = state

    def requires_confirmation(
        self,
        action_events: list[ActionEvent],
    ) -> bool:
        """
        Decide whether user confirmation is needed to proceed.

        Rules:
            1. Confirmation mode is enabled
            2. Every action requires confirmation
            3. A single `FinishAction` never requires confirmation
            4. A single `ThinkAction` never requires confirmation
        """
        # If there are no actions there is nothing to confirm
        if len(action_events) == 0:
            return False

        if all(
            isinstance(action, (FinishAction, ThinkAction)) for action in action_events
        ):
            return False
        # If a security analyzer is registered, use it to grab the risks of the actions
        # involved. If not, we'll set the risks to UNKNOWN.
        if self._state.security_analyzer is not None:
            risks = (
                r
                for _, r in self._state.security_analyzer.analyze_pending_actions(
                    action_events
                )
            )
        else:
            risks = (risk.SecurityRisk.UNKNOWN for _ in action_events)

        return any(self._state.confirmation_policy.should_confirm(r) for r in risks)

    def extract_security_risk(
        self,
        arguments: dict,
        tool_name: str,
        read_only_tool: bool,
    ) -> risk.SecurityRisk:
        requires_sr = isinstance(self._state.security_analyzer, LLMSecurityAnalyzer)
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
