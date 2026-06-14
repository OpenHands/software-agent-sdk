from collections.abc import Sequence
from typing import TYPE_CHECKING, Self

from pydantic import Field
from rich.text import Text

from openhands.sdk.llm.llm_profile_store import PROFILE_NAME_REGEX, LLMProfileStore
from openhands.sdk.tool.registry import register_tool
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


class AskOracleAction(Action):
    """Action for asking a configured Oracle LLM profile for advice."""

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

_ORACLE_SYSTEM_PROMPT = """\
You are the Oracle: a highly capable reviewer giving a second opinion to an \
OpenHands agent.

Answer the agent's question directly. Do not call tools. Do not perform work \
directly. Give a concrete recommendation the agent can follow, including important \
risks or caveats."""

_ORACLE_USER_PROMPT_TEMPLATE = """\
Question:
{question}
{context_section}"""


class AskOracleExecutor(ToolExecutor[AskOracleAction, AskOracleObservation]):
    def __init__(self, profile_name: str | None, profile_store_dir: str | None) -> None:
        self.profile_name = profile_name
        self.profile_store_dir = profile_store_dir

    def __call__(
        self,
        action: AskOracleAction,
        conversation: "LocalConversation | None" = None,
    ) -> AskOracleObservation:
        if not self.profile_name:
            return AskOracleObservation.from_text(
                text="The Oracle is not configured.",
                is_error=True,
            )

        cipher = conversation._cipher if conversation is not None else None
        try:
            oracle_llm = LLMProfileStore(self.profile_store_dir).load(
                self.profile_name, cipher=cipher
            )
        except FileNotFoundError:
            return AskOracleObservation.from_text(
                text=(
                    "The Oracle is not available because its configured profile "
                    "was not found."
                ),
                is_error=True,
            )
        except ValueError as exc:
            return AskOracleObservation.from_text(
                text=f"The Oracle is not available: {exc}",
                is_error=True,
            )
        except Exception as exc:
            return AskOracleObservation.from_text(
                text=f"The Oracle is not available: {type(exc).__name__}: {exc}",
                is_error=True,
            )

        from openhands.sdk.agent.utils import make_llm_completion
        from openhands.sdk.llm import Message, TextContent

        context_section = (
            f"\nAdditional context from the agent:\n{action.context}\n"
            if action.context
            else ""
        )
        user_prompt = _ORACLE_USER_PROMPT_TEMPLATE.format(
            question=action.question,
            context_section=context_section,
        )
        messages = [
            Message(
                role="system",
                content=[TextContent(text=_ORACLE_SYSTEM_PROMPT)],
            ),
            Message(role="user", content=[TextContent(text=user_prompt)]),
        ]

        try:
            llm_response = make_llm_completion(oracle_llm, messages)
        except Exception as exc:
            return AskOracleObservation.from_text(
                text=(
                    "The Oracle encountered an error and did not return a "
                    f"response: {type(exc).__name__}: {exc}"
                ),
                is_error=True,
            )

        oracle_text = "".join(
            content.text
            for content in llm_response.message.content
            if isinstance(content, TextContent)
        ).strip()
        if not oracle_text:
            return AskOracleObservation.from_text(
                text="The Oracle did not return a response.",
                is_error=True,
            )

        return AskOracleObservation.from_text(
            text=oracle_text,
            response=oracle_text,
        )


class AskOracleTool(ToolDefinition[AskOracleAction, AskOracleObservation]):
    """Tool for consulting a configured Oracle LLM profile."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        profile_name: str | None = None,
        profile_store_dir: str | None = None,
        **params,
    ) -> Sequence[Self]:
        if params:
            raise ValueError(
                "AskOracleTool only accepts profile_name and profile_store_dir"
            )
        if profile_name is not None and not PROFILE_NAME_REGEX.match(profile_name):
            raise ValueError(
                "Invalid Oracle profile name. Profile names must be 1-64 "
                "characters, start with a letter or digit, and contain only "
                "letters, digits, '.', '_', or '-'."
            )

        return [
            cls(
                description=_DESCRIPTION,
                action_type=AskOracleAction,
                observation_type=AskOracleObservation,
                executor=AskOracleExecutor(profile_name, profile_store_dir),
                annotations=ToolAnnotations(
                    readOnlyHint=True,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=False,
                ),
            )
        ]


register_tool(AskOracleTool.name, AskOracleTool)
