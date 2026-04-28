"""Tests for context: fork skill execution.

These tests cover:
- The ``context`` field serialization round-trip.
- That a forked skill calls ``run_skill_forked`` instead of inlining the content.
- That an inline skill (default) still injects content directly.
- That a forked skill without agent/working_dir falls back to inline with a warning.
- End-to-end: a fork-context skill really forks a subagent, returns its output
  to the parent, and the parent keeps going — using ``TestLLM`` for both sides.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk import Agent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import MessageEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.skills.fork import build_fork_resolver, run_skill_forked
from openhands.sdk.testing import TestLLM


def _make_context(skill: Skill) -> AgentContext:
    return AgentContext(skills=[skill])


def _user_message(text: str) -> Message:
    return Message(role="user", content=[TextContent(text=text)])


def _fork_skill(**kwargs) -> Skill:
    return Skill(
        name=kwargs.pop("name", "ddebug"),
        content=kwargs.pop("content", "run 50 queries"),
        trigger=kwargs.pop("trigger", KeywordTrigger(keywords=["datadog"])),
        context=kwargs.pop("context", "fork"),
        **kwargs,
    )


@pytest.mark.parametrize("context", ["inline", "fork"])
def test_context_field_valid(context):
    trigger = KeywordTrigger(keywords=["kw"]) if context == "fork" else None
    skill = Skill(name="s", content="body", context=context, trigger=trigger)
    assert skill.context == context


def test_context_field_default_is_inline():
    assert Skill(name="s", content="body").context == "inline"


def test_context_field_invalid_literal():
    with pytest.raises(Exception):
        Skill(name="s", content="body", context="invalid_value")  # type: ignore[arg-type]


def test_context_fork_requires_trigger():
    with pytest.raises(Exception, match="requires a trigger"):
        Skill(name="s", content="body", context="fork", trigger=None)


@pytest.mark.parametrize("context", ["inline", "fork"])
def test_context_field_roundtrip(context):
    skill = Skill(
        name="s",
        content="body",
        trigger=KeywordTrigger(keywords=["debug"]),
        context=context,
    )
    restored = Skill.model_validate(skill.model_dump())
    assert restored.context == context


@pytest.mark.parametrize(
    "frontmatter_snippet, expected",
    [
        ("context: fork\n", "fork"),
        ("context: inline\n", "inline"),
        ("", "inline"),  # absent → default
    ],
)
def test_context_field_from_frontmatter(frontmatter_snippet, expected):
    md = f"---\ntriggers: [debug]\n{frontmatter_snippet}---\nDo the thing\n"
    skill = Skill._load_legacy_openhands_skill(Path("skill.md"), md, None)
    assert skill.context == expected


@pytest.mark.parametrize(
    "context, expect_fork_called",
    [
        ("inline", False),
        ("fork", True),
    ],
)
def test_dispatch_calls_fork_only_for_fork_context(context, expect_fork_called):
    skill = Skill(
        name="ddebug",
        content="raw content",
        trigger=KeywordTrigger(keywords=["datadog"]),
        context=context,
    )
    ctx = _make_context(skill)

    with patch(
        "openhands.sdk.skills.fork.run_skill_forked",
        return_value="subagent result",
    ) as mock_fork:
        result = ctx.get_user_message_suffix(
            user_message=_user_message("datadog debug"),
            skip_skill_names=[],
            resolve_skill_content=build_fork_resolver(MagicMock(), "/workspace", None),
        )

    assert mock_fork.called == expect_fork_called
    assert result is not None
    content, names = result
    assert "ddebug" in names
    if expect_fork_called:
        assert "subagent result" in content.text
        assert "raw content" not in content.text
    else:
        assert "raw content" in content.text


@pytest.mark.parametrize(
    "persistence_dir",
    [None, "/state/abc123"],
)
def test_fork_skill_persistence_dir_forwarded(persistence_dir):
    """persistence_dir from the parent is forwarded verbatim to run_skill_forked."""
    skill = _fork_skill()
    ctx = _make_context(skill)

    with patch(
        "openhands.sdk.skills.fork.run_skill_forked",
        return_value="result",
    ) as mock_fork:
        ctx.get_user_message_suffix(
            user_message=_user_message("datadog debug"),
            skip_skill_names=[],
            resolve_skill_content=build_fork_resolver(
                MagicMock(), "/workspace", persistence_dir
            ),
        )

    args, _ = mock_fork.call_args
    # signature: run_skill_forked(skill, agent, working_dir, persistence_dir)
    assert args[3] == persistence_dir


@pytest.mark.parametrize(
    "persistence_dir, skill_name, expected",
    [
        (None, "ddebug", None),
        ("/state/abc123", "ddebug", "/state/abc123/forks/ddebug"),
        # Path-unsafe characters are sanitized to avoid nested dirs or traversal
        ("/state/abc123", "subdir/my_skill", "/state/abc123/forks/subdir_my_skill"),
        ("/state/abc123", "../evil", "/state/abc123/forks/___evil"),
    ],
)
def test_fork_persistence_dir_path_construction(persistence_dir, skill_name, expected):
    """builds <persistence_dir>/forks/<safe_name> before passing to Conversation."""
    skill = _fork_skill(name=skill_name)
    agent = Agent(
        llm=TestLLM.from_messages([]),
        tools=[],
        include_default_tools=[],
        agent_context=None,
    )

    with patch(
        "openhands.sdk.conversation.conversation.Conversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_conv.state.events = []
        MockConversation.return_value = mock_conv

        run_skill_forked(skill, agent, "/workspace", persistence_dir)

    _, conv_kwargs = MockConversation.call_args
    assert conv_kwargs.get("persistence_dir") == expected


def test_fork_skill_inlines_when_no_resolver_passed():
    """Without a resolve_skill_content callback, AgentContext doesn't know about
    forks and just inlines raw skill.content — even for context='fork' skills.
    Fork dispatch is the caller's responsibility (LocalConversation's resolver)."""
    skill = _fork_skill(content="inline fallback content")
    ctx = _make_context(skill)

    with patch("openhands.sdk.skills.fork.run_skill_forked") as mock_fork:
        result = ctx.get_user_message_suffix(
            user_message=_user_message("datadog debug"),
            skip_skill_names=[],
        )

    mock_fork.assert_not_called()
    assert result is not None
    content, _ = result
    assert "inline fallback content" in content.text


def test_subagent_context_keeps_inline_skills_drops_forks():
    """Forked subagent retains inline skills but not other fork skills,
    and preserves system_message_suffix."""
    fork_skill = _fork_skill(name="ddebug")
    other_fork = _fork_skill(name="other_fork")
    inline_skill = Skill(
        name="inline_helper",
        content="inline body",
        trigger=KeywordTrigger(keywords=["helper"]),
        context="inline",
    )
    parent_ctx = AgentContext(
        skills=[fork_skill, other_fork, inline_skill],
        system_message_suffix="preserve me",
    )
    agent = Agent(
        llm=TestLLM.from_messages([]),
        tools=[],
        include_default_tools=[],
        agent_context=parent_ctx,
    )

    with patch(
        "openhands.sdk.conversation.conversation.Conversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_conv.state.events = []
        MockConversation.return_value = mock_conv
        run_skill_forked(fork_skill, agent, "/workspace")

    _, conv_kwargs = MockConversation.call_args
    sub_ctx = conv_kwargs["agent"].agent_context
    assert [s.name for s in sub_ctx.skills] == ["inline_helper"]
    assert sub_ctx.system_message_suffix == "preserve me"


def test_fork_returns_error_marker_when_subagent_crashes(caplog):
    """A fork crash must not propagate to the parent — return a marker instead.

    The fork is best-effort context retrieval. If sub_conv.run() raises, the
    parent conversation should keep going with a readable error string rather
    than dying with the fork's traceback.
    """
    import logging

    fork_skill = _fork_skill(name="ddebug")
    agent = Agent(
        llm=TestLLM.from_messages([]),
        tools=[],
        include_default_tools=[],
        agent_context=None,
    )

    with patch(
        "openhands.sdk.conversation.conversation.Conversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_conv.run.side_effect = RuntimeError("simulated LLM failure")
        MockConversation.return_value = mock_conv

        with caplog.at_level(logging.ERROR, logger="openhands.sdk.skills.fork"):
            result = run_skill_forked(fork_skill, agent, "/workspace")

    assert "ddebug" in result
    assert "RuntimeError" in result
    assert "simulated LLM failure" in result
    # Operators still see the full traceback in logs (message + exc_info).
    crash_record = next(
        r for r in caplog.records if "ddebug" in r.getMessage() and r.exc_info
    )
    assert crash_record.exc_info[0] is RuntimeError
    # Sub-conversation was closed even on error.
    mock_conv.close.assert_called_once()


def test_fork_isolates_llm_metrics_from_parent():
    """Fork must not share the parent's Metrics object via the LLM reference.

    Both conversations read their own ConversationStats.usage_to_metrics, which
    holds a *reference* to llm.metrics captured at registration time. If the
    sub-agent used the parent's LLM object directly, fork completions would
    mutate the same Metrics instance and the parent's accounting would leak.
    """
    fork_skill = _fork_skill()
    parent_llm = TestLLM.from_messages([])
    agent = Agent(
        llm=parent_llm,
        tools=[],
        include_default_tools=[],
        agent_context=AgentContext(skills=[fork_skill]),
    )

    with patch(
        "openhands.sdk.conversation.conversation.Conversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_conv.state.events = []
        MockConversation.return_value = mock_conv
        run_skill_forked(fork_skill, agent, "/workspace")

    _, conv_kwargs = MockConversation.call_args
    sub_llm = conv_kwargs["agent"].llm
    assert sub_llm is not parent_llm, "fork must receive its own LLM instance"
    # Fresh metrics: reset_metrics() detaches so a new Metrics is lazily created
    # on next access, independent of the parent's.
    assert sub_llm.metrics is not parent_llm.metrics


def test_fork_end_to_end_with_test_llm(tmp_path):
    """End-to-end: trigger keyword → fork subagent runs → output reaches parent.

    This exercises the real pipeline (no mocks on ``run_skill_forked`` or
    ``Conversation``): ``LocalConversation`` + ``Agent`` + fork-context
    ``Skill`` + real ``run_skill_forked``.

    The fork gets its own ``TestLLM`` instance (copied + metrics reset), but
    ``model_copy`` shallow-copies PrivateAttrs so the ``_scripted_responses``
    deque is shared by reference: the fork consumes the first response, the
    parent consumes the second.
    """
    fork_output = "FORK_SUBAGENT_OUTPUT_SENTINEL"
    parent_reply = "parent acknowledges the fork result"

    llm = TestLLM.from_messages(
        [
            Message(role="assistant", content=[TextContent(text=fork_output)]),
            Message(role="assistant", content=[TextContent(text=parent_reply)]),
        ]
    )

    skill = Skill(
        name="ddebug",
        content="Investigate the datadog error and return a summary.",
        trigger=KeywordTrigger(keywords=["datadog"]),
        context="fork",
    )
    agent = Agent(
        llm=llm,
        tools=[],
        include_default_tools=[],
        agent_context=AgentContext(skills=[skill]),
    )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    persistence_dir = tmp_path / "persist"

    conversation = LocalConversation(
        agent=agent,
        workspace=workspace,
        persistence_dir=persistence_dir,
        visualizer=None,
    )
    try:
        conversation.send_message("please look into the datadog error")
        conversation.run()

        # Parent and fork each completed once on their own LLM instance
        # (combined: the shared response deque was fully consumed).
        assert llm.call_count == 1
        assert len(llm._scripted_responses) == 0

        # The user MessageEvent carries the augmented content (the fork's
        # final output) and records the skill as activated.
        user_events = [
            e
            for e in conversation.state.events
            if isinstance(e, MessageEvent) and e.source == "user"
        ]
        assert len(user_events) == 1
        user_event = user_events[0]
        assert "ddebug" in user_event.activated_skills
        extended_text = "".join(c.text for c in user_event.extended_content)
        assert fork_output in extended_text
        # The raw skill content must NOT be injected (fork replaces it).
        assert "Investigate the datadog error" not in extended_text

        # The parent produced its own final message after seeing the fork output.
        agent_events = [
            e
            for e in conversation.state.events
            if isinstance(e, MessageEvent) and e.source == "agent"
        ]
        assert agent_events, "parent conversation produced no agent messages"
        last_agent_text = "".join(
            c.text
            for c in agent_events[-1].llm_message.content
            if isinstance(c, TextContent)
        )
        assert parent_reply in last_agent_text
    finally:
        conversation.close()
