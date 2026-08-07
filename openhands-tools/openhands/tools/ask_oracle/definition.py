"""Action, observation, and tool definitions for the ask_oracle tool."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.tool.registry import register_tool
from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.state import ConversationState


# The Oracle model is a saved LLM profile resolved by convention under this
# name. Save a profile named "oracle" (e.g. via LLMProfileStore.save("oracle",
# llm)) and the tool will consult it. No agent setting or wiring is required.
ORACLE_PROFILE_NAME: Final[str] = "oracle"


class AskOracleAction(Action):
    """Action for asking the Oracle for advice."""

    question: str = Field(
        description=(
            "The specific question or dilemma to ask the Oracle about. Use this "
            "when you are stuck, uncertain, or need a second opinion."
        )
    )
    context: str | None = Field(
        default=None,
        description=(
            "Optional extra context, such as approaches already tried, constraints, "
            "or the recommendation you are considering."
        ),
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        content.append("Ask Oracle: ", style="bold cyan")
        content.append(self.question)
        if self.context:
            content.append("\nContext: ", style="bold")
            content.append(self.context)
        return content


class AskOracleObservation(Observation):
    """Observation returned by the Oracle consultation."""

    response: str = Field(
        default="",
        description="Text response returned by the Oracle.",
    )

    @property
    def visualize(self) -> Text:
        content = Text()
        if self.is_error:
            content.append("Oracle consultation failed", style="bold red")
        else:
            content.append("Oracle recommendation", style="bold green")
        if self.text:
            content.append("\n")
            content.append(self.text)
        return content


_DESCRIPTION = (
    "Ask the Oracle for a second opinion. The Oracle is a smart model intended "
    "to help with difficult reasoning.\n\n"
    "Use this when you are stuck, uncertain, comparing approaches, or need a "
    "higher-quality recommendation before proceeding.\n\n"
    "Treat the Oracle's response as strong guidance and follow its recommendation "
    "unless you have a clear reason not to."
)


class AskOracleTool(ToolDefinition[AskOracleAction, AskOracleObservation]):
    """Tool for consulting the Oracle (a saved LLM profile named "oracle")."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError("AskOracleTool does not accept parameters")

        # Import here to keep module import light and avoid any import cycles.
        from openhands.tools.ask_oracle.impl import AskOracleExecutor

        return [
            cls(
                description=_DESCRIPTION,
                action_type=AskOracleAction,
                observation_type=AskOracleObservation,
                executor=AskOracleExecutor(),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
            )
        ]


# Automatically register when this module is imported.
register_tool(AskOracleTool.name, AskOracleTool)
