"""Tests that LLMAuthenticationError surfaces a user-friendly message.

Regression tests for:
  https://github.com/OpenHands/software-agent-sdk/issues/3411

When a user has an invalid/expired API key the raw litellm error (e.g. the
full AnthropicException JSON) must NOT appear in the ConversationErrorEvent
detail that is sent to the UI.  Instead, a clear, actionable message should
be emitted, while the ConversationRunError is still raised so server logs
remain unaffected.
"""

import asyncio
import tempfile

import pytest

from openhands.sdk.agent import Agent
from openhands.sdk.conversation import Conversation, LocalConversation
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.llm.exceptions import LLMAuthenticationError
from openhands.sdk.testing import TestLLM

_RAW_LITELLM_ERROR = (
    "litellm.AuthenticationError: AnthropicException - "
    '{"type":"error","error":{"type":"authentication_error",'
    '"message":"invalid x-api-key"},'
    '"request_id":"req_011CbTfF4jtKVAB95FSH6ESb"}'
)
_FRIENDLY_SUBSTRING = "invalid or has expired"


def _make_auth_failing_conversation(tmpdir: str) -> LocalConversation:
    llm = TestLLM.from_messages([LLMAuthenticationError(_RAW_LITELLM_ERROR)])
    agent = Agent(llm=llm, tools=[])
    conv = Conversation(agent=agent, persistence_dir=tmpdir, workspace=tmpdir)
    assert isinstance(conv, LocalConversation)
    conv.send_message(Message(role="user", content=[TextContent(text="hello")]))
    return conv


# ---------------------------------------------------------------------------
# Sync path (run)
# ---------------------------------------------------------------------------


def test_auth_error_run_raises_conversation_run_error():
    """ConversationRunError is still raised so server logs are unaffected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = _make_auth_failing_conversation(tmpdir)
        with pytest.raises(ConversationRunError) as exc_info:
            conv.run()
        assert isinstance(exc_info.value.__cause__, LLMAuthenticationError)


def test_auth_error_run_emits_friendly_detail():
    """ConversationErrorEvent.detail is user-readable, not the raw litellm string."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = _make_auth_failing_conversation(tmpdir)
        with pytest.raises(ConversationRunError):
            conv.run()

        error_events = [
            e for e in conv.state.events if isinstance(e, ConversationErrorEvent)
        ]
        assert error_events, "Expected at least one ConversationErrorEvent"

        auth_error_event = next(
            (e for e in error_events if e.code == "LLMAuthenticationError"), None
        )
        assert auth_error_event is not None, (
            "Expected a ConversationErrorEvent with code='LLMAuthenticationError'"
        )
        assert _FRIENDLY_SUBSTRING in auth_error_event.detail, (
            f"Expected friendly message in detail, got: {auth_error_event.detail!r}"
        )
        assert _RAW_LITELLM_ERROR not in auth_error_event.detail, (
            "Raw litellm error string must not appear in the UI-facing detail"
        )


# ---------------------------------------------------------------------------
# Async path (arun)
# ---------------------------------------------------------------------------


def test_auth_error_arun_raises_conversation_run_error():
    """Async path: ConversationRunError is still raised."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = _make_auth_failing_conversation(tmpdir)
        with pytest.raises(ConversationRunError) as exc_info:
            asyncio.run(conv.arun())
        assert isinstance(exc_info.value.__cause__, LLMAuthenticationError)


def test_auth_error_arun_emits_friendly_detail():
    """Async path: ConversationErrorEvent.detail is user-readable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conv = _make_auth_failing_conversation(tmpdir)
        with pytest.raises(ConversationRunError):
            asyncio.run(conv.arun())

        error_events = [
            e for e in conv.state.events if isinstance(e, ConversationErrorEvent)
        ]
        assert error_events, "Expected at least one ConversationErrorEvent"

        auth_error_event = next(
            (e for e in error_events if e.code == "LLMAuthenticationError"), None
        )
        assert auth_error_event is not None, (
            "Expected a ConversationErrorEvent with code='LLMAuthenticationError'"
        )
        assert _FRIENDLY_SUBSTRING in auth_error_event.detail
        assert _RAW_LITELLM_ERROR not in auth_error_event.detail
