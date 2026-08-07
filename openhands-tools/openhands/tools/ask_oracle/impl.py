"""Executor for the ask_oracle tool."""

from typing import TYPE_CHECKING

from openhands.sdk.agent.utils import make_llm_completion
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.llm_profile_store import LLMProfileStore
from openhands.sdk.tool.tool import ToolExecutor
from openhands.tools.ask_oracle.definition import (
    ORACLE_PROFILE_NAME,
    AskOracleAction,
    AskOracleObservation,
)


if TYPE_CHECKING:
    from openhands.sdk.conversation.impl.local_conversation import LocalConversation


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
    """Consult the Oracle: a saved LLM profile named ``oracle``.

    The Oracle is resolved by convention from the conversation's LLM profile
    store under the name ``oracle`` (``ORACLE_PROFILE_NAME``). The call is
    stateless: it sends only the Oracle system prompt plus the agent's question
    and optional context, with no conversation history and no tools, and returns
    the Oracle's text. The active conversation LLM is never switched.
    """

    def __call__(
        self,
        action: AskOracleAction,
        conversation: "LocalConversation | None" = None,
    ) -> AskOracleObservation:
        cipher = conversation._cipher if conversation is not None else None
        try:
            oracle_llm = LLMProfileStore().load(ORACLE_PROFILE_NAME, cipher=cipher)
        except FileNotFoundError:
            return AskOracleObservation.from_text(
                text=(
                    "The Oracle is not available because no profile named "
                    f"'{ORACLE_PROFILE_NAME}' was found. Save one to enable it."
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
