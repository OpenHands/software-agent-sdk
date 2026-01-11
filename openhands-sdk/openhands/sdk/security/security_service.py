from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState

from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.security import risk
from openhands.sdk.security.llm_analyzer import LLMSecurityAnalyzer
from openhands.sdk.tool.builtins.finish import FinishAction
from openhands.sdk.tool.builtins.think import ThinkAction


class AccessConfirmRes(BaseModel):
    access_confirm: bool
    security_level: risk.SecurityRisk | None = None


class SecurityServiceBase(ABC):
    """
    Security service interface defining core security-related methods.
    """

    @abstractmethod
    def access_confirm(self, action_events: list[ActionEvent]) -> AccessConfirmRes:
        """
        Determine whether user confirmation is required to proceed with actions.

        :param action_events: List of pending action events
        :return: AccessConfirm object will never return None:
                 - access_confirm: bool = True (needs user confirmation)
                 / False (no confirmation needed)
                 - security_level: Optional[risk.SecurityRisk] = Security risk
                 level of the actions (defaults to the highest risk level among all
                 actions; None if no risk))
        """
        pass

    def extract_security_risk(
        self,
        arguments: dict,
        tool_name: str,
        read_only_tool: bool,
    ) -> Any:
        """
        Extracts the security risk level from tool invocation arguments,
        with adaptive logic for different LLM security analyzers.

        Args:
            arguments: Dictionary of tool invocation parameters, which may
                       or may not contain the "security_risk" key.
            tool_name: Name of the target tool, used to locate the problematic
                       tool in exception messages.
            read_only_tool: Boolean flag indicating if the tool is read-only
                            (no security risk check required).

        """
        pass


class DefaultSecurityService(SecurityServiceBase):
    def __init__(
        self,
        state: "ConversationState",
    ):
        self._state = state

    def access_confirm(
        self,
        action_events: list[ActionEvent],
    ) -> AccessConfirmRes:
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
            return AccessConfirmRes(access_confirm=False)
        if len(action_events) == 1 and isinstance(
            action_events[0].action, (FinishAction, ThinkAction)
        ):
            return AccessConfirmRes(access_confirm=False)
        # If a security analyzer is registered, use it to grab the risks of the actions
        # involved. If not, we'll set the risks to UNKNOWN.
        non_unknown_risks = []
        if self._state.security_analyzer is not None:
            risks = [
                r
                for _, r in self._state.security_analyzer.analyze_pending_actions(
                    action_events
                )
            ]
            non_unknown_risks = [r for r in risks if r != risk.SecurityRisk.UNKNOWN]
        else:
            risks = [risk.SecurityRisk.UNKNOWN for _ in action_events]

        access_confirm = any(
            self._state.confirmation_policy.should_confirm(r) for r in risks
        )
        # Return the highest risk level.
        if non_unknown_risks:
            security_level = max(
                non_unknown_risks,
                key=lambda x: {
                    risk.SecurityRisk.LOW: 1,
                    risk.SecurityRisk.MEDIUM: 2,
                    risk.SecurityRisk.HIGH: 3,
                }[x],
            )
        else:
            security_level = risk.SecurityRisk.UNKNOWN

        return AccessConfirmRes(
            access_confirm=access_confirm, security_level=security_level
        )

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
