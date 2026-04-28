"""Run a skill as an isolated subagent conversation.

When a Skill has ``context: fork`` in its frontmatter, its content is not
injected inline into the parent conversation. Instead it is handed to a fresh
subagent (same Agent/LLM, new Conversation with empty history) whose final
assistant message is returned as the skill's recalled knowledge.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from openhands.sdk.logger import get_logger
from openhands.sdk.skills.execute import render_content_with_commands


# Skill names are not guaranteed to be filesystem-safe:
# - Legacy skills derive their name from a relative path, which can contain
#   "/" (e.g. "subdir/my_skill") and would create unintended nested dirs.
# - Programmatic Skill(name=...) has no name validator; a crafted name like
#   "../../etc/passwd" would escape the forks directory.
# AgentSkills-format skills ARE validated (lowercase [a-z0-9-]) but we
# sanitize unconditionally to keep the fork persistence layout predictable.
_UNSAFE_PATH_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


if TYPE_CHECKING:
    from openhands.sdk.agent.base import AgentBase
    from openhands.sdk.context.agent_context import AgentContext
    from openhands.sdk.skills.skill import Skill


logger = get_logger(__name__)


def _build_sub_agent_context(
    parent_context: AgentContext | None,
) -> AgentContext | None:
    """Drop fork-context skills so the subagent can't re-trigger itself
    (direct recursion) or other forks (A → B → A loops). Inline skills stay —
    they only inject static content. All other context fields
    (system_message_suffix, secrets, datetime) are preserved."""
    if parent_context is None:
        return None
    safe_skills = [s for s in parent_context.skills if s.context != "fork"]
    return parent_context.model_copy(update={"skills": safe_skills})


def _build_sub_agent(
    agent: AgentBase,
    sub_agent_context: AgentContext | None,
) -> AgentBase:
    """Rebuild the agent from spec (not model_copy) so the sub-conv runs
    _initialize() fresh — otherwise the shallow-copied _tools would let
    sub_conv.close() tear down the parent's executors. Clone + reset_metrics
    the LLM so fork/parent token accounting stay separate (same pattern as
    DelegateTool, see delegate/impl.py)."""
    sub_llm = agent.llm.model_copy()
    sub_llm.reset_metrics()
    spec_fields = {name: getattr(agent, name) for name in type(agent).model_fields}
    spec_fields["agent_context"] = sub_agent_context
    spec_fields["llm"] = sub_llm
    return type(agent)(**spec_fields)


def _fork_persistence_dir(
    persistence_dir: str | None,
    skill_name: str,
) -> str | None:
    if persistence_dir is None:
        return None
    safe_name = _UNSAFE_PATH_CHARS.sub("_", skill_name)
    return str(Path(persistence_dir) / "forks" / safe_name)


def build_fork_resolver(
    agent: AgentBase,
    working_dir: str,
    persistence_dir: str | None,
):
    """Resolve triggered-skill content, forking a subagent for ``context='fork'``.

    Returned callable matches ``Callable[[Skill], str]`` — suitable for passing
    as ``resolve_skill_content`` to ``AgentContext.get_user_message_suffix``.
    """

    def resolve(skill: Skill) -> str:
        if skill.context != "fork":
            return skill.content
        logger.info("Skill ‘%s’ running as forked subagent", skill.name)
        return run_skill_forked(skill, agent, working_dir, persistence_dir)

    return resolve


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
    # Deferred to avoid a circular import: Conversation → AgentBase → skills.
    from openhands.sdk.conversation.conversation import Conversation
    from openhands.sdk.conversation.response_utils import get_agent_final_response

    skill_prompt = render_content_with_commands(
        skill.content,
        working_dir=Path(working_dir) if working_dir else None,
    )
    sub_agent = _build_sub_agent(
        agent,
        _build_sub_agent_context(agent.agent_context),
    )
    sub_conv = Conversation(
        agent=sub_agent,
        workspace=str(working_dir),
        persistence_dir=_fork_persistence_dir(persistence_dir, skill.name),
        visualizer=None,
        stuck_detection=True,
        delete_on_close=True,
    )
    try:
        sub_conv.send_message(skill_prompt)
        sub_conv.run()
        return (
            get_agent_final_response(sub_conv.state.events)
            or "[forked skill produced no output]"
        )
    except Exception as e:
        # A fork is best-effort context retrieval; don't take down the parent
        # conversation. Log the full traceback for operators and return an
        # inline marker so the parent LLM sees the failure instead of crashing.
        logger.exception("Forked skill %r crashed", skill.name)
        return f"[forked skill {skill.name!r} failed: {type(e).__name__}: {e}]"
    finally:
        try:
            sub_conv.close()
        except Exception as e:
            logger.debug("Ignoring error closing forked sub-conversation: %s", e)
