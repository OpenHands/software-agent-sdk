"""Run a skill as an isolated subagent conversation.

When a Skill has ``context: fork`` in its frontmatter, its content is not
injected inline into the parent conversation. Instead it is handed to a fresh
subagent (same Agent/LLM, new Conversation with empty history) whose final
assistant message is returned as the skill's recalled knowledge.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger
from openhands.sdk.skills.execute import render_content_with_commands


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.skills.skill import Skill


logger = get_logger(__name__)


def run_skill_forked(
    skill: Skill,
    agent: AgentBase,
    working_dir: str | Path,
    persistence_dir: str | None = None,
) -> str:
    """Run ``skill`` as a subagent and return its final assistant text.

    The subagent starts with no parent history: its only input is the skill
    content (with inline ``!`command`` patterns rendered).

    If ``persistence_dir`` is provided (the parent conversation's persistence
    directory), the subconversation is saved under:
        ``<persistence_dir>/forks/<skill.name>/``
    Otherwise the subconversation is ephemeral (in-memory only).
    """
    from openhands.sdk.conversation.conversation import Conversation
    from openhands.sdk.conversation.response_utils import get_agent_final_response

    skill_prompt = render_content_with_commands(
        skill.content,
        working_dir=Path(working_dir) if working_dir else None,
    )

    # Strip skills from the subagent's context so forked skills cannot
    # re-trigger and cause infinite recursion. Everything else (system_message_suffix,
    # secrets, datetime) is preserved.
    sub_agent_context = (
        agent.agent_context.model_copy(update={"skills": []})
        if agent.agent_context is not None
        else None
    )
    sub_agent = agent.model_copy(update={"agent_context": sub_agent_context})

    fork_persistence_dir: str | None = None
    if persistence_dir is not None:
        fork_persistence_dir = str(Path(persistence_dir) / "forks" / skill.name)

    sub_conv = Conversation(
        agent=sub_agent,
        workspace=str(working_dir),
        persistence_dir=fork_persistence_dir,
        visualizer=None,
        stuck_detection=False,
        delete_on_close=True,
    )
    try:
        sub_conv.send_message(skill_prompt)
        sub_conv.run()
        return (
            get_agent_final_response(sub_conv.state.events)
            or "[forked skill produced no output]"
        )
    finally:
        try:
            sub_conv.close()
        except Exception:
            logger.debug("Ignoring error closing forked sub-conversation")
