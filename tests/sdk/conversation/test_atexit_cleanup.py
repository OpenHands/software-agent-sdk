"""Tests for atexit handler cleanup to prevent memory leaks."""

import gc
import tempfile
import weakref
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk.agent import Agent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.llm import LLM


def _make_conversation(workspace: str) -> LocalConversation:
    llm = LLM(model="gpt-4o-mini", api_key=SecretStr("k"), usage_id="test")
    return LocalConversation(agent=Agent(llm=llm, tools=[]), workspace=workspace)


def test_close_unregisters_atexit_handler():
    """close() must remove the atexit handler so the object can be GC'd."""
    with tempfile.TemporaryDirectory() as tmp:
        workspace = str(Path(tmp) / "ws")
        Path(workspace).mkdir()
        conv = _make_conversation(workspace)

        conv.close()

        # If atexit still held a reference, the weak-ref would stay alive
        # after we drop the strong reference.
        ref = weakref.ref(conv)
        del conv
        gc.collect()
        assert ref() is None, "Conversation was not GC'd — atexit leak"


def test_close_is_idempotent_with_atexit():
    """Calling close() twice must not raise, even with atexit handling."""
    with tempfile.TemporaryDirectory() as tmp:
        workspace = str(Path(tmp) / "ws")
        Path(workspace).mkdir()
        conv = _make_conversation(workspace)

        conv.close()
        conv.close()  # second call is a no-op
