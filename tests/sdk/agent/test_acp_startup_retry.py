"""ACP startup retry and timeout reporting against a real stdio ACP server."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openhands.sdk import Conversation
from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event.conversation_error import ConversationErrorEvent


FAKE_SERVER = Path(__file__).with_name("fake_acp_stalling_server.py")


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stall_method: str,
    *,
    always: bool = False,
) -> tuple[LocalConversation, list[ConversationErrorEvent], Path]:
    log = tmp_path / "requests.log"
    monkeypatch.setenv("FAKE_ACP_MARKER", str(tmp_path / "launches"))
    monkeypatch.setenv("FAKE_ACP_LOG", str(log))
    monkeypatch.setenv("FAKE_ACP_STALL_METHOD", stall_method)
    if always:
        monkeypatch.setenv("FAKE_ACP_STALL_ALWAYS", "1")
    else:
        monkeypatch.delenv("FAKE_ACP_STALL_ALWAYS", raising=False)
    errors: list[ConversationErrorEvent] = []
    conversation = Conversation(
        agent=ACPAgent(
            acp_command=[sys.executable, str(FAKE_SERVER)], acp_startup_timeout=2
        ),
        workspace=str(tmp_path),
        persistence_dir=str(tmp_path / "conversation"),
        callbacks=[
            lambda event: (
                errors.append(event)
                if isinstance(event, ConversationErrorEvent)
                else None
            )
        ],
        visualizer=None,
    )
    assert isinstance(conversation, LocalConversation)
    conversation.send_message("say hello")
    return conversation, errors, log


def test_stall_in_initialize_is_retried_and_the_turn_runs(tmp_path, monkeypatch):
    conversation, errors, log = _run(tmp_path, monkeypatch, "initialize")
    try:
        conversation.run()
    finally:
        conversation.close()

    assert log.read_text().splitlines() == [
        "1 initialize",
        "2 initialize",
        "2 session/new",
        "2 session/prompt",
    ]
    assert errors == []
    assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED


def test_permanent_stall_in_initialize_fails_after_one_retry(tmp_path, monkeypatch):
    conversation, errors, log = _run(tmp_path, monkeypatch, "initialize", always=True)
    try:
        with pytest.raises(TimeoutError, match="during initialize"):
            conversation.run()
    finally:
        conversation.close()

    assert log.read_text().splitlines() == ["1 initialize", "2 initialize"]
    assert [e.code for e in errors] == ["ACPStartupTimeout"]
    assert "during initialize" in errors[0].detail
    assert f"(server: {Path(sys.executable).name})" in errors[0].detail


def test_stall_after_session_request_is_not_retried(tmp_path, monkeypatch):
    conversation, errors, log = _run(tmp_path, monkeypatch, "session/new")
    try:
        with pytest.raises(TimeoutError, match="during session/new"):
            conversation.run()
    finally:
        conversation.close()

    assert log.read_text().splitlines() == ["1 initialize", "1 session/new"]
    assert [e.code for e in errors] == ["ACPStartupTimeout"]
    assert "during session/new" in errors[0].detail
