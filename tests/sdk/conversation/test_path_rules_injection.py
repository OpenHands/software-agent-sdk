"""Tests for the path-rule injection seam in LocalConversation.

When the agent touches a file whose path matches a PathTrigger skill ("rule"),
the rule content is appended to the resulting ObservationEvent's
``extended_content`` and deduped via ``state.activated_path_rules``.
"""

from pathlib import Path

from openhands.sdk.agent import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.sdk.llm import Message, MessageToolCall, TextContent
from openhands.sdk.skills import PathTrigger, Skill
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool.builtins.finish import FinishObservation
from openhands.sdk.tool.builtins.invoke_skill import (
    InvokeSkillAction,
    InvokeSkillExecutor,
)
from openhands.sdk.tool.schema import Action


class _FileAction(Action):
    """Minimal path-bearing action (stands in for a file-editor action)."""

    path: str
    command: str = "view"


class _NoPathAction(Action):
    """An action without a ``path`` field (e.g. a terminal command)."""

    command: str = "ls"


def _conversation(tmp_path: Path, rule: Skill) -> LocalConversation:
    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="ok")])],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        agent_context=AgentContext(skills=[rule]),
    )
    return LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )


def _append_file_action(conv: LocalConversation, abs_path: str) -> ActionEvent:
    action_event = ActionEvent(
        thought=[TextContent(text="touch")],
        action=_FileAction(path=abs_path),
        tool_name="file_editor",
        tool_call_id="tc1",
        tool_call=MessageToolCall(
            id="tc1", name="file_editor", arguments="{}", origin="completion"
        ),
        llm_response_id="r1",
        source="agent",
    )
    with conv._state:
        conv._on_event(action_event)
    return action_event


def _observation_for(action_event: ActionEvent) -> ObservationEvent:
    return ObservationEvent(
        observation=FinishObservation(content=[TextContent(text="tool-result")]),
        action_id=action_event.id,
        tool_name=action_event.tool_name,
        tool_call_id=action_event.tool_call_id,
    )


def _inject(conv: LocalConversation, obs: ObservationEvent) -> ObservationEvent:
    result = conv._maybe_inject_path_rules(obs)
    assert isinstance(result, ObservationEvent)
    return result


def test_rule_injected_into_observation_on_matching_file_touch(tmp_path: Path) -> None:
    rule = Skill(
        name="api",
        content="Use zod for API validation.",
        trigger=PathTrigger(paths=["src/api/**/*.ts"]),
    )
    conv = _conversation(tmp_path, rule)
    try:
        action_event = _append_file_action(conv, str(tmp_path / "src" / "api" / "u.ts"))
        injected = _inject(conv, _observation_for(action_event))

        assert len(injected.extended_content) == 1
        assert "Use zod for API validation." in injected.extended_content[0].text
        # The rule content reaches the LLM message after the tool result.
        texts = [getattr(c, "text", "") for c in injected.to_llm_message().content]
        assert any("tool-result" in t for t in texts)
        assert any("Use zod" in t for t in texts)
        # Dedup state recorded the activation.
        assert conv._state.activated_path_rules == ["api"]
    finally:
        conv.close()


def test_rule_not_injected_for_nonmatching_path(tmp_path: Path) -> None:
    rule = Skill(
        name="api",
        content="Use zod.",
        trigger=PathTrigger(paths=["src/api/**/*.ts"]),
    )
    conv = _conversation(tmp_path, rule)
    try:
        action_event = _append_file_action(conv, str(tmp_path / "README.md"))
        injected = _inject(conv, _observation_for(action_event))
        assert injected.extended_content == []
        assert conv._state.activated_path_rules == []
    finally:
        conv.close()


def test_rule_injected_once_then_deduped(tmp_path: Path) -> None:
    rule = Skill(
        name="api",
        content="Use zod.",
        trigger=PathTrigger(paths=["src/api/**/*.ts"]),
    )
    conv = _conversation(tmp_path, rule)
    try:
        a1 = _append_file_action(conv, str(tmp_path / "src" / "api" / "a.ts"))
        first = _inject(conv, _observation_for(a1))
        assert len(first.extended_content) == 1

        a2 = _append_file_action(conv, str(tmp_path / "src" / "api" / "b.ts"))
        second = _inject(conv, _observation_for(a2))
        # Same rule already activated -> not injected again.
        assert second.extended_content == []
        assert conv._state.activated_path_rules == ["api"]
    finally:
        conv.close()


def test_injection_is_wired_through_on_event(tmp_path: Path) -> None:
    """The rule reaches the *persisted* observation via the real callback chain."""
    rule = Skill(
        name="api",
        content="Use zod.",
        trigger=PathTrigger(paths=["src/api/**/*.ts"]),
    )
    conv = _conversation(tmp_path, rule)
    try:
        action_event = _append_file_action(conv, str(tmp_path / "src" / "api" / "u.ts"))
        with conv._state:
            conv._on_event(_observation_for(action_event))

        persisted = [
            e
            for e in conv.state.events
            if isinstance(e, ObservationEvent) and e.action_id == action_event.id
        ]
        assert persisted, "observation was not persisted"
        assert any("Use zod." in c.text for c in persisted[-1].extended_content)
        assert conv._state.activated_path_rules == ["api"]
    finally:
        conv.close()


def test_file_outside_workspace_is_not_matched(tmp_path: Path) -> None:
    rule = Skill(
        name="any",
        content="rule",
        trigger=PathTrigger(paths=["**/*.ts"]),
    )
    conv = _conversation(tmp_path, rule)
    try:
        # Absolute path outside the workspace root.
        action_event = _append_file_action(conv, "/somewhere/else/x.ts")
        injected = _inject(conv, _observation_for(action_event))
        assert injected.extended_content == []
        assert conv._state.activated_path_rules == []
    finally:
        conv.close()


def test_non_path_action_does_not_inject(tmp_path: Path) -> None:
    """A tool action with no ``path`` field never triggers a rule."""
    rule = Skill(name="any", content="rule", trigger=PathTrigger(paths=["**/*"]))
    conv = _conversation(tmp_path, rule)
    try:
        action_event = ActionEvent(
            thought=[TextContent(text="run")],
            action=_NoPathAction(command="ls"),
            tool_name="terminal",
            tool_call_id="tc1",
            tool_call=MessageToolCall(
                id="tc1", name="terminal", arguments="{}", origin="completion"
            ),
            llm_response_id="r1",
            source="agent",
        )
        with conv._state:
            conv._on_event(action_event)
        injected = _inject(conv, _observation_for(action_event))
        assert injected.extended_content == []
        assert conv._state.activated_path_rules == []
    finally:
        conv.close()


def test_relative_path_action_matched_against_workspace(tmp_path: Path) -> None:
    """A workspace-relative action path is matched as-is."""
    rule = Skill(
        name="api", content="Use zod.", trigger=PathTrigger(paths=["src/api/**/*.ts"])
    )
    conv = _conversation(tmp_path, rule)
    try:
        action_event = _append_file_action(conv, "src/api/users.ts")  # relative
        injected = _inject(conv, _observation_for(action_event))
        assert any("Use zod." in c.text for c in injected.extended_content)
    finally:
        conv.close()


def test_observation_extended_content_serialization_round_trip() -> None:
    """The injected rule survives event-log persistence + reload, and stays
    ordered after the tool result in the reconstructed LLM message."""
    obs = ObservationEvent(
        observation=FinishObservation(content=[TextContent(text="result")]),
        action_id="a",
        tool_name="t",
        tool_call_id="c",
        extended_content=[TextContent(text="RULE")],
    )
    back = ObservationEvent.model_validate_json(obs.model_dump_json())
    assert [c.text for c in back.extended_content] == ["RULE"]
    texts = [getattr(c, "text", "") for c in back.to_llm_message().content]
    assert "result" in texts and "RULE" in texts
    assert texts.index("RULE") > texts.index("result")  # rule appended after result


def test_fork_copies_activated_path_rules(tmp_path: Path) -> None:
    rule = Skill(
        name="api", content="Use zod.", trigger=PathTrigger(paths=["src/api/**/*.ts"])
    )
    conv = _conversation(tmp_path, rule)
    try:
        a = _append_file_action(conv, str(tmp_path / "src" / "api" / "u.ts"))
        with conv._state:
            conv._on_event(_observation_for(a))
        assert conv._state.activated_path_rules == ["api"]

        forked = conv.fork()
        try:
            assert forked._state.activated_path_rules == ["api"]
        finally:
            forked.close()
    finally:
        conv.close()


def test_path_rule_not_invocable_via_invoke_skill(tmp_path: Path) -> None:
    """A path rule can only be activated by file-touch, never invoke_skill."""
    rule = Skill(name="api", content="Use zod.", trigger=PathTrigger(paths=["**/*.ts"]))
    conv = _conversation(tmp_path, rule)
    try:
        obs = InvokeSkillExecutor()(InvokeSkillAction(name="api"), conv)
        assert obs.is_error
        assert "cannot be invoked directly" in obs.text
    finally:
        conv.close()


def test_no_injection_when_agent_has_no_context(tmp_path: Path) -> None:
    """The seam is a safe no-op when the agent has no agent_context."""
    agent = Agent(
        llm=TestLLM.from_messages(
            [Message(role="assistant", content=[TextContent(text="ok")])],
            model="test-model",
        ),
        tools=[],
        include_default_tools=[],
        agent_context=None,
    )
    conv = LocalConversation(
        agent=agent,
        workspace=tmp_path,
        persistence_dir=tmp_path / "conversation",
        delete_on_close=True,
    )
    try:
        action_event = _append_file_action(conv, str(tmp_path / "src" / "api" / "u.ts"))
        injected = _inject(conv, _observation_for(action_event))
        assert injected.extended_content == []
    finally:
        conv.close()


def test_no_injection_when_action_not_correlated(tmp_path: Path) -> None:
    """An observation whose action_id isn't in the event log yields no path."""
    rule = Skill(name="any", content="rule", trigger=PathTrigger(paths=["**/*"]))
    conv = _conversation(tmp_path, rule)
    try:
        orphan = ObservationEvent(
            observation=FinishObservation(content=[TextContent(text="x")]),
            action_id="nonexistent-action-id",
            tool_name="file_editor",
            tool_call_id="tc9",
        )
        injected = _inject(conv, orphan)
        assert injected.extended_content == []
        assert conv._state.activated_path_rules == []
    finally:
        conv.close()
