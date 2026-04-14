"""Tests for context: fork skill execution.

These tests cover:
- The ``context`` field serialization round-trip.
- That a forked skill calls ``run_skill_forked`` instead of inlining the content.
- That an inline skill (default) still injects content directly.
- That a forked skill without agent/working_dir falls back to inline with a warning.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.skills import KeywordTrigger, Skill
from openhands.sdk.skills.fork import run_skill_forked


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
        "openhands.sdk.context.agent_context.run_skill_forked",
        return_value="subagent result",
    ) as mock_fork:
        result = ctx.get_user_message_suffix(
            user_message=_user_message("datadog debug"),
            skip_skill_names=[],
            agent=MagicMock(),
            working_dir="/workspace",
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


@pytest.fixture
def mock_agent():
    return MagicMock()


@pytest.mark.parametrize(
    "persistence_dir",
    [None, "/state/abc123"],
)
def test_fork_skill_persistence_dir_forwarded(persistence_dir):
    """persistence_dir from the parent is forwarded verbatim to run_skill_forked."""
    skill = _fork_skill()
    ctx = _make_context(skill)

    with patch(
        "openhands.sdk.context.agent_context.run_skill_forked",
        return_value="result",
    ) as mock_fork:
        ctx.get_user_message_suffix(
            user_message=_user_message("datadog debug"),
            skip_skill_names=[],
            agent=MagicMock(),
            working_dir="/workspace",
            persistence_dir=persistence_dir,
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
    mock_agent = MagicMock()
    mock_agent.agent_context = None

    with patch(
        "openhands.sdk.conversation.conversation.Conversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_conv.state.events = []
        MockConversation.return_value = mock_conv

        run_skill_forked(skill, mock_agent, "/workspace", persistence_dir)

    _, conv_kwargs = MockConversation.call_args
    assert conv_kwargs.get("persistence_dir") == expected


@pytest.mark.parametrize(
    "working_dir, use_agent",
    [
        (None, False),  # no agent, no working_dir
        ("/workspace", False),  # working_dir but no agent
        (None, True),  # agent but no working_dir
    ],
)
def test_fork_skill_fallback_when_agent_or_working_dir_missing(
    mock_agent, working_dir, use_agent
):
    skill = _fork_skill(content="inline fallback content")
    ctx = _make_context(skill)

    with patch("openhands.sdk.context.agent_context.run_skill_forked") as mock_fork:
        result = ctx.get_user_message_suffix(
            user_message=_user_message("datadog debug"),
            skip_skill_names=[],
            agent=mock_agent if use_agent else None,
            working_dir=working_dir,
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
    mock_agent = MagicMock()
    mock_agent.agent_context = parent_ctx
    # model_copy on the agent itself: capture what gets passed
    mock_agent.model_copy.return_value = MagicMock()

    with patch(
        "openhands.sdk.conversation.conversation.Conversation"
    ) as MockConversation:
        mock_conv = MagicMock()
        mock_conv.state.events = []
        MockConversation.return_value = mock_conv
        run_skill_forked(fork_skill, mock_agent, "/workspace")

    _, agent_copy_kwargs = mock_agent.model_copy.call_args
    sub_ctx = agent_copy_kwargs["update"]["agent_context"]
    assert [s.name for s in sub_ctx.skills] == ["inline_helper"]
    assert sub_ctx.system_message_suffix == "preserve me"
