"""End-to-end: path rules fire for the tools the old ``getattr(action, "path")``
seam silently missed (Gemini file tools, apply_patch), and do NOT fire for a
directory that was only searched (grep).

These drive the REAL observations from openhands-tools through the actual
injection seam (``LocalConversation._maybe_inject_path_rules``).
"""

from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ObservationEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.skills import PathTrigger, Skill
from openhands.sdk.testing import TestLLM
from openhands.tools.apply_patch.core import ActionType, Commit, FileChange
from openhands.tools.apply_patch.definition import ApplyPatchObservation
from openhands.tools.gemini import WriteFileObservation
from openhands.tools.grep import GrepObservation


def _conversation(tmp_path: Path, *rules: Skill) -> LocalConversation:
    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="ok")])],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        agent_context=AgentContext(skills=list(rules)),
    )
    return LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )


def _obs_event(observation, tool_name: str) -> ObservationEvent:
    return ObservationEvent(
        observation=observation,
        action_id="a1",
        tool_name=tool_name,
        tool_call_id="tc1",
    )


def _api_rule() -> Skill:
    return Skill(
        name="api",
        content="Use zod for API validation.",
        trigger=PathTrigger(paths=["src/api/**/*.ts"]),
    )


def test_gemini_write_file_triggers_path_rule(tmp_path: Path) -> None:
    """A real Gemini WriteFileObservation (field ``file_path``) fires the rule
    the old ``getattr(action, "path")`` seam missed."""
    conv = _conversation(tmp_path, _api_rule())
    try:
        obs = _obs_event(
            WriteFileObservation(
                file_path=str(tmp_path / "src" / "api" / "users.ts"), is_new_file=True
            ),
            tool_name="write_file",
        )
        injected = conv._maybe_inject_path_rules(obs)
        assert isinstance(injected, ObservationEvent)
        assert any("Use zod" in c.text for c in injected.extended_content)
        assert conv._state.activated_path_rules == ["api"]
    finally:
        conv.close()


def test_apply_patch_triggers_rules_for_every_changed_file(tmp_path: Path) -> None:
    """A real ApplyPatchObservation touching files in two rule directories fires
    both rules from a single observation (multi-path)."""
    api = _api_rule()
    ui = Skill(
        name="ui",
        content="Use tailwind.",
        trigger=PathTrigger(paths=["src/ui/**/*.ts"]),
    )
    conv = _conversation(tmp_path, api, ui)
    try:
        commit = Commit(
            changes={
                "src/api/a.ts": FileChange(type=ActionType.UPDATE),
                "src/ui/b.ts": FileChange(type=ActionType.ADD),
            }
        )
        injected = conv._maybe_inject_path_rules(
            _obs_event(ApplyPatchObservation(commit=commit), tool_name="apply_patch")
        )
        assert isinstance(injected, ObservationEvent)
        texts = " ".join(c.text for c in injected.extended_content)
        assert "Use zod" in texts and "Use tailwind" in texts
        assert sorted(conv._state.activated_path_rules) == ["api", "ui"]
    finally:
        conv.close()


def test_grep_over_a_matching_directory_does_not_trigger(tmp_path: Path) -> None:
    """grep searches a directory but modifies nothing, so the rule must not
    fire even though the searched directory matches the rule's tree."""
    conv = _conversation(tmp_path, _api_rule())
    try:
        obs = _obs_event(
            GrepObservation(
                matches=[str(tmp_path / "src" / "api" / "users.ts")],
                pattern="zod",
                search_path=str(tmp_path / "src" / "api"),
            ),
            tool_name="grep",
        )
        injected = conv._maybe_inject_path_rules(obs)
        assert isinstance(injected, ObservationEvent)
        assert injected.extended_content == []
        assert conv._state.activated_path_rules == []
    finally:
        conv.close()
