from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation
    from openhands.sdk.conversation.state import ConversationState


class SwitchLLMAction(Action):
    """Action for switching this conversation to a saved LLM profile."""

    profile_name: str = Field(
        description="Name of the saved LLM profile to use for future agent steps."
    )
    reason: str = Field(
        description="Brief reason why this profile is a better fit for the next step."
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Switch LLM profile: ", style="bold magenta")
        content.append(self.profile_name)
        if self.reason:
            content.append("\nReason: ", style="bold")
            content.append(self.reason)
        return content


class SwitchLLMObservation(Observation):
    """Observation returned after switching this conversation's LLM profile."""

    profile_name: str = Field(
        description="Name of the profile that the tool attempted to activate."
    )
    reason: str | None = Field(
        default=None,
        description="Reason the agent gave for attempting this LLM profile switch.",
    )
    active_model: str | None = Field(
        default=None,
        description="Model configured by the activated profile, when available.",
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Failed to switch LLM profile", style="bold red")
        else:
            content.append("Switched LLM profile", style="bold green")
        content.append(f": {self.profile_name}")
        if self.active_model:
            content.append(f" ({self.active_model})")
        if self.reason:
            content.append("\nReason: ", style="bold")
            content.append(self.reason)
        return content


_DESCRIPTION_TEMPLATE = (
    "Switch this conversation to a saved LLM profile.\n\n"
    "Use this when another available profile is better suited for the next step. "
    "The current tool call is still executed by the current model; the switch "
    "takes effect on the next LLM call.\n\n"
    "Available LLM profiles:\n"
    "{profiles}\n\n"
    "Provide the profile_name exactly as listed and include a concise reason "
    "for the switch."
)


def get_llm_profile_names() -> list[str]:
    """Return saved LLM profile names that can be shown to the agent."""
    return [summary["name"] for summary in LLMProfileStore().list_summaries()]


def _format_profiles(profile_names: Sequence[str]) -> str:
    if not profile_names:
        return "- No saved LLM profiles are currently available."
    return "\n".join(f"- {name}" for name in sorted(profile_names))


class SwitchLLMExecutor(ToolExecutor):
    def __call__(
        self,
        action: SwitchLLMAction,
        conversation: "LocalConversation | None" = None,
    ) -> SwitchLLMObservation:
        if conversation is None:
            return SwitchLLMObservation.from_text(
                text="Cannot switch LLM profile without an active conversation.",
                is_error=True,
                profile_name=action.profile_name,
                reason=action.reason,
            )

        try:
            conversation.switch_profile(action.profile_name)
        except FileNotFoundError:
            return SwitchLLMObservation.from_text(
                text=f"LLM profile '{action.profile_name}' was not found.",
                is_error=True,
                profile_name=action.profile_name,
                reason=action.reason,
            )
        except ValueError as exc:
            return SwitchLLMObservation.from_text(
                text=str(exc),
                is_error=True,
                profile_name=action.profile_name,
                reason=action.reason,
            )
        except Exception as exc:
            return SwitchLLMObservation.from_text(
                text=(
                    f"Failed to switch LLM profile '{action.profile_name}': "
                    f"{type(exc).__name__}: {exc}"
                ),
                is_error=True,
                profile_name=action.profile_name,
                reason=action.reason,
            )

        active_model = conversation.agent.llm.model
        return SwitchLLMObservation.from_text(
            text=(
                f"Switched LLM profile to '{action.profile_name}' "
                f"with active model '{active_model}'. Reason: {action.reason} "
                "Future agent steps will use this profile."
            ),
            profile_name=action.profile_name,
            reason=action.reason,
            active_model=active_model,
        )


class SwitchLLMTool(ToolDefinition[SwitchLLMAction, SwitchLLMObservation]):
    """Tool for switching a conversation to a saved LLM profile."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("SwitchLLMTool doesn't accept parameters")

        profile_names = get_llm_profile_names()
        return [
            cls(
                description=_DESCRIPTION_TEMPLATE.format(
                    profiles=_format_profiles(profile_names)
                ),
                action_type=SwitchLLMAction,
                observation_type=SwitchLLMObservation,
                executor=SwitchLLMExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]
