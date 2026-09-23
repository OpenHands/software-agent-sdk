"""Tests for LLMCallContext: per-conversation state isolation on shared LLMs.

Covers the fix for #3443 — shared LLM/Agent objects across conversations
should not inherit stale per-conversation state (prompt_cache_key,
x-litellm-session-id).
"""

import tempfile

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.llm import LLM
from openhands.sdk.llm.llm import LLMCallContext
from openhands.sdk.llm.options.chat_options import select_chat_options
from openhands.sdk.llm.options.responses_options import select_responses_options


def _llm(**kwargs) -> LLM:
    return LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key"),
        usage_id="test",
        **kwargs,
    )


def _agent(llm: LLM | None = None) -> Agent:
    return Agent(llm=llm or _llm(), tools=[])


# ── LLMCallContext unit tests ──────────────────────────────────────────


def test_call_context_defaults_to_empty():
    llm = _llm()
    ctx = llm._call_context
    assert ctx.prompt_cache_key is None
    assert ctx.session_id is None


def test_call_context_is_assignable():
    llm = _llm()
    llm._call_context = LLMCallContext(prompt_cache_key="conv-1", session_id="sess-1")
    assert llm._call_context.prompt_cache_key == "conv-1"
    assert llm._call_context.session_id == "sess-1"


def test_call_context_dropped_on_json_round_trip():
    """PrivateAttr must not survive model_dump → model_validate."""
    llm = _llm()
    llm._call_context = LLMCallContext(prompt_cache_key="conv-1", session_id="sess-1")
    restored = LLM.model_validate(llm.model_dump(context={"expose_secrets": True}))
    assert restored._call_context.prompt_cache_key is None
    assert restored._call_context.session_id is None


def test_call_context_shallow_copied_by_model_copy():
    """model_copy(update=...) must carry the context forward (sub-agent path)."""
    llm = _llm()
    llm._call_context = LLMCallContext(
        prompt_cache_key="parent", session_id="parent-sess"
    )
    child = llm.model_copy(update={"usage_id": "child"})
    assert child._call_context.prompt_cache_key == "parent"
    assert child._call_context.session_id == "parent-sess"


# ── select_chat_options injection tests ────────────────────────────────


def test_chat_options_injects_session_id_from_context():
    llm = _llm()
    llm._call_context = LLMCallContext(session_id="conv-42")
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-42"


def test_chat_options_injects_prompt_cache_key_from_context():
    llm = _llm()
    llm._call_context = LLMCallContext(prompt_cache_key="conv-42")
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    assert out["prompt_cache_key"] == "conv-42"


def test_chat_options_session_id_survives_user_extra_headers():
    """Session ID must be injected even when user passes extra_headers."""
    llm = _llm(extra_headers={"X-Custom": "value"})
    llm._call_context = LLMCallContext(session_id="conv-99")
    out = select_chat_options(
        llm, user_kwargs={"extra_headers": {"X-User": "hi"}}, has_tools=False
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-99"
    assert out["extra_headers"]["X-User"] == "hi"


def test_chat_options_context_wins_over_user_session_id():
    """Context session_id must override a stale user-supplied value."""
    llm = _llm()
    llm._call_context = LLMCallContext(session_id="conv-99")
    out = select_chat_options(
        llm,
        user_kwargs={"extra_headers": {"x-litellm-session-id": "user-stale"}},
        has_tools=False,
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-99"


def test_responses_options_context_wins_over_user_session_id():
    """Context session_id must override a stale user-supplied value."""
    llm = _llm()
    llm._call_context = LLMCallContext(session_id="conv-99")
    out = select_responses_options(
        llm,
        user_kwargs={"extra_headers": {"x-litellm-session-id": "user-stale"}},
        include=None,
        store=None,
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-99"


def test_chat_options_no_session_header_when_context_empty():
    llm = _llm()
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    headers = out.get("extra_headers") or {}
    assert "x-litellm-session-id" not in headers


def test_chat_options_no_prompt_cache_key_when_context_empty():
    llm = _llm()
    out = select_chat_options(llm, user_kwargs={}, has_tools=False)
    assert "prompt_cache_key" not in out


# ── select_responses_options injection tests ───────────────────────────


def test_responses_options_injects_session_id_from_context():
    llm = _llm()
    llm._call_context = LLMCallContext(session_id="conv-42")
    out = select_responses_options(llm, user_kwargs={}, include=None, store=None)
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-42"


def test_responses_options_injects_prompt_cache_key_from_context():
    llm = _llm()
    llm._call_context = LLMCallContext(prompt_cache_key="conv-42")
    out = select_responses_options(llm, user_kwargs={}, include=None, store=None)
    assert out["prompt_cache_key"] == "conv-42"


# ── Conversation-level isolation tests ─────────────────────────────────


def test_conversation_binds_context_to_shared_agent():
    """Creating a conversation always binds context on the agent's LLM."""
    agent = _agent()
    conv = Conversation(agent=agent)

    ctx = agent.llm._call_context
    assert ctx.prompt_cache_key == str(conv.id)
    assert ctx.session_id == str(conv.id)


def test_sequential_conversations_rebind_both_fields():
    """Sequential conversations from the same agent rebind both fields.

    Both prompt_cache_key and session_id must reflect the most recent
    conversation (#3443).  Sub-agent inheritance is handled by
    ``model_copy()`` shallow-copying the PrivateAttr after binding,
    so no guard is needed here.
    """
    agent = _agent()
    conv1 = Conversation(agent=agent)
    conv1_id = str(conv1.id)
    assert agent.llm._call_context.prompt_cache_key == conv1_id
    assert agent.llm._call_context.session_id == conv1_id

    conv2 = Conversation(agent=agent)
    conv2_id = str(conv2.id)
    # PrivateAttr reflects the latest conversation — this is expected.
    # Agent.step() builds a fresh LLMCallContext from the calling
    # conversation and threads it explicitly, so the step path never
    # reads llm._call_context; it exists only as a fallback for callers
    # that don't thread context (e.g. condenser).
    assert agent.llm._call_context.prompt_cache_key == conv2_id
    assert agent.llm._call_context.session_id == conv2_id


def test_extra_headers_not_polluted_by_session_id():
    """Session ID should live in _call_context, not in extra_headers."""
    llm = _llm(extra_headers={"X-Custom": "keep-me"})
    conv = Conversation(agent=Agent(llm=llm, tools=[]))

    # extra_headers should only contain user-supplied values
    headers = conv.agent.llm.extra_headers or {}
    assert "x-litellm-session-id" not in headers
    assert headers["X-Custom"] == "keep-me"

    # session ID should be in context
    assert conv.agent.llm._call_context.session_id == str(conv.id)


def test_fork_gets_fresh_context():
    """fork() JSON round-trips the agent, so context should be re-bound."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        # Each gets its own context
        assert src.agent.llm._call_context.prompt_cache_key == str(src.id)
        assert fork.agent.llm._call_context.prompt_cache_key == str(fork.id)

        # Source not clobbered
        assert (
            src.agent.llm._call_context.prompt_cache_key
            != fork.agent.llm._call_context.prompt_cache_key
        )
