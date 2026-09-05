"""Tests for LLMCallContext: per-conversation state isolation on shared LLMs.

Covers the fix for #3443 — shared LLM/Agent objects across conversations
should not inherit stale per-conversation state (prompt_cache_key,
x-litellm-session-id).
"""

import asyncio
import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from deprecation import DeprecatedWarning
from pydantic import SecretStr

import openhands.sdk.llm.llm as legacy_llm_module
from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.llm import LLM, LLMCallContext, Message, TextContent
from openhands.sdk.llm.call_context import llm_call_context_scope
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


def test_legacy_call_context_import_path_is_deprecated():
    with pytest.warns(DeprecatedWarning, match="openhands.sdk.llm.llm.LLMCallContext"):
        legacy_call_context = legacy_llm_module.LLMCallContext

    assert legacy_call_context is LLMCallContext


def test_scoped_context_applies_only_inside_scope():
    llm = _llm()

    with llm_call_context_scope(LLMCallContext(session_id="scoped")):
        scoped = select_chat_options(llm, user_kwargs={}, has_tools=False)

    unscoped = select_chat_options(llm, user_kwargs={}, has_tools=False)

    assert scoped["extra_headers"]["x-litellm-session-id"] == "scoped"
    assert "x-litellm-session-id" not in unscoped.get("extra_headers", {})


def test_explicit_context_precedes_scoped_context():
    llm = _llm()

    with llm_call_context_scope(LLMCallContext(session_id="scoped")):
        out = select_chat_options(
            llm,
            user_kwargs={},
            has_tools=False,
            call_context=LLMCallContext(session_id="explicit"),
        )

    assert out["extra_headers"]["x-litellm-session-id"] == "explicit"


@pytest.mark.asyncio
async def test_scoped_context_is_isolated_between_async_tasks():
    llm = _llm()

    async def select_in_scope(session_id: str) -> str:
        with llm_call_context_scope(LLMCallContext(session_id=session_id)):
            await asyncio.sleep(0)
            out = select_chat_options(llm, user_kwargs={}, has_tools=False)
            return out["extra_headers"]["x-litellm-session-id"]

    assert await asyncio.gather(
        select_in_scope("first"),
        select_in_scope("second"),
    ) == ["first", "second"]


# ── select_chat_options injection tests ────────────────────────────────


def test_chat_options_injects_session_id_from_context():
    llm = _llm()
    out = select_chat_options(
        llm,
        user_kwargs={},
        has_tools=False,
        call_context=LLMCallContext(session_id="conv-42"),
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-42"


def test_chat_options_injects_prompt_cache_key_from_context():
    llm = _llm()
    out = select_chat_options(
        llm,
        user_kwargs={},
        has_tools=False,
        call_context=LLMCallContext(prompt_cache_key="conv-42"),
    )
    assert out["prompt_cache_key"] == "conv-42"


def test_chat_options_session_id_survives_user_extra_headers():
    """Session ID must be injected even when user passes extra_headers."""
    llm = _llm(extra_headers={"X-Custom": "value"})
    out = select_chat_options(
        llm,
        user_kwargs={"extra_headers": {"X-User": "hi"}},
        has_tools=False,
        call_context=LLMCallContext(session_id="conv-99"),
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-99"
    assert out["extra_headers"]["X-User"] == "hi"


def test_chat_options_context_wins_over_user_session_id():
    """Context session_id must override a stale user-supplied value."""
    llm = _llm()
    out = select_chat_options(
        llm,
        user_kwargs={"extra_headers": {"x-litellm-session-id": "user-stale"}},
        has_tools=False,
        call_context=LLMCallContext(session_id="conv-99"),
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-99"


def test_responses_options_context_wins_over_user_session_id():
    """Context session_id must override a stale user-supplied value."""
    llm = _llm()
    out = select_responses_options(
        llm,
        user_kwargs={"extra_headers": {"x-litellm-session-id": "user-stale"}},
        include=None,
        store=None,
        call_context=LLMCallContext(session_id="conv-99"),
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
    out = select_responses_options(
        llm,
        user_kwargs={},
        include=None,
        store=None,
        call_context=LLMCallContext(session_id="conv-42"),
    )
    assert out["extra_headers"]["x-litellm-session-id"] == "conv-42"


def test_responses_options_injects_prompt_cache_key_from_context():
    llm = _llm()
    out = select_responses_options(
        llm,
        user_kwargs={},
        include=None,
        store=None,
        call_context=LLMCallContext(prompt_cache_key="conv-42"),
    )
    assert out["prompt_cache_key"] == "conv-42"


# ── Conversation-level isolation tests ─────────────────────────────────


def test_conversation_owns_one_immutable_context():
    conv = Conversation(agent=_agent())

    first = conv.get_llm_call_context()
    second = conv.get_llm_call_context()

    assert first is second
    assert first.session_id == str(conv.id)


def test_shared_agent_conversations_own_independent_contexts():
    agent = _agent()
    conv1 = Conversation(agent=agent)
    conv2 = Conversation(agent=agent)

    assert not hasattr(agent.llm, "_call_context")
    assert conv1.agent.llm is conv2.agent.llm
    assert conv1.get_llm_call_context().session_id == str(conv1.id)
    assert conv2.get_llm_call_context().session_id == str(conv2.id)
    assert conv1.get_llm_call_context() is not conv2.get_llm_call_context()


def test_run_scopes_unthreaded_llm_calls_on_shared_agent(monkeypatch):
    seen_session_ids: list[str] = []

    def step(self, conversation, on_event, on_token=None):
        options = select_chat_options(self.llm, user_kwargs={}, has_tools=False)
        seen_session_ids.append(options["extra_headers"]["x-litellm-session-id"])
        conversation.state.execution_status = ConversationExecutionStatus.FINISHED

    agent = _agent()
    conv1 = Conversation(agent=agent)
    conv1.send_message("first")
    conv2 = Conversation(agent=agent)
    conv2.send_message("second")

    monkeypatch.setattr(Agent, "step", step)
    conv1.run()
    conv2.run()

    assert seen_session_ids == [str(conv1.id), str(conv2.id)]


def test_shared_agent_context_reaches_live_llm_transport():
    seen_session_ids: list[str | None] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            content_length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(content_length)
            seen_session_ids.append(self.headers.get("x-litellm-session-id"))
            payload = json.dumps(
                {
                    "id": "chatcmpl-context-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_finish",
                                        "type": "function",
                                        "function": {
                                            "name": "finish",
                                            "arguments": json.dumps(
                                                {"message": "done"}
                                            ),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    conversations = []
    try:
        llm = LLM(
            model="openai/gpt-4o-mini",
            api_key=SecretStr("test-key"),
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            usage_id="live-context-test",
            stream=False,
        )
        agent = _agent(llm)
        first = Conversation(agent=agent, visualizer=None)
        second = Conversation(agent=agent, visualizer=None)
        conversations.extend([first, second])
        first.send_message("first")
        second.send_message("second")

        llm.completion([Message(role="user", content=[TextContent(text="direct")])])

        first.run()
        second.run()

        assert seen_session_ids == [None, str(first.id), str(second.id)]
        assert first.state.execution_status == ConversationExecutionStatus.FINISHED
        assert second.state.execution_status == ConversationExecutionStatus.FINISHED
    finally:
        for conversation in conversations:
            conversation.close()
        server.shutdown()
        server_thread.join()
        server.server_close()


def test_extra_headers_not_polluted_by_session_id():
    """Applying conversation context must not mutate configured headers."""
    llm = _llm(extra_headers={"X-Custom": "keep-me"})
    conv = Conversation(agent=Agent(llm=llm, tools=[]))

    # extra_headers should only contain user-supplied values
    headers = conv.agent.llm.extra_headers or {}
    assert "x-litellm-session-id" not in headers
    assert headers["X-Custom"] == "keep-me"

    assert conv.get_llm_call_context().session_id == str(conv.id)


def test_fork_gets_fresh_context():
    """A fork owns fresh conversation identity without mutating its source."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Conversation(agent=_agent(), persistence_dir=tmpdir, workspace=tmpdir)
        fork = src.fork()

        # Each gets its own context
        assert src.get_llm_call_context().prompt_cache_key == str(src.id)
        assert fork.get_llm_call_context().prompt_cache_key == str(fork.id)

        # Source not clobbered
        assert (
            src.get_llm_call_context().prompt_cache_key
            != fork.get_llm_call_context().prompt_cache_key
        )
