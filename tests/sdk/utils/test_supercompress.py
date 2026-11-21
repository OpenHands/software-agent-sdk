"""Command output keeps the head/tail cut unless one compress call is shorter."""

import json

from openhands.sdk.event.base import LLMConvertibleEvent
from openhands.sdk.event.llm_convertible import MessageEvent, ObservationEvent
from openhands.sdk.llm import Message, TextContent
from openhands.sdk.utils.supercompress import (
    clear_supercompress_cache,
    compress_context,
    prepare_command_output,
    supercompress_query,
)
from openhands.sdk.utils.truncate import maybe_truncate
from openhands.tools.terminal.constants import MAX_CMD_OUTPUT_SIZE
from openhands.tools.terminal.definition import TerminalObservation


class _Body:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def setup_function():
    clear_supercompress_cache()


def test_without_a_query_the_head_tail_cut_is_unchanged():
    content = "A" * (MAX_CMD_OUTPUT_SIZE + 1000)
    prepared = prepare_command_output(content, truncate_after=MAX_CMD_OUTPUT_SIZE)
    expected = maybe_truncate(content, truncate_after=MAX_CMD_OUTPUT_SIZE)
    assert prepared == expected


def test_compress_context_rejects_a_result_that_is_not_shorter():
    context = "x" * 100

    def opener(_request, _timeout):
        return _Body(json.dumps({"compressed_text": context}).encode())

    assert compress_context(context, "task", "test-key", opener=opener) is None


def test_one_request_per_message_build_then_the_cache(monkeypatch):
    monkeypatch.setenv("SUPERCOMPRESS_API_KEY", "test-key")
    calls = {"n": 0}
    content = "noise\n" * 5000 + "error: disk full\n"

    def opener(request, timeout):
        calls["n"] += 1
        assert timeout == 5.0
        assert request.get_header("X-api-key") == "test-key"
        body = json.loads(request.data.decode())
        assert body["query"] == "find the disk full error"
        assert "error: disk full" in body["context"]
        return _Body(json.dumps({"compressed_text": "error: disk full"}).encode())

    with supercompress_query("find the disk full error"):
        first = prepare_command_output(
            content, truncate_after=MAX_CMD_OUTPUT_SIZE, opener=opener
        )
        second = prepare_command_output(
            content, truncate_after=MAX_CMD_OUTPUT_SIZE, opener=opener
        )
    assert first == "error: disk full"
    assert second == "error: disk full"
    assert calls["n"] == 1

    with supercompress_query("find the disk full error"):
        third = prepare_command_output(
            content, truncate_after=MAX_CMD_OUTPUT_SIZE, opener=opener
        )
    assert third == "error: disk full"
    assert calls["n"] == 1


def test_events_to_messages_uses_the_latest_user_text(monkeypatch):
    monkeypatch.setenv("SUPERCOMPRESS_API_KEY", "test-key")
    seen = {}

    def fake_compress(context, query, _api_key, **_kwargs):
        seen["query"] = query
        seen["has_error"] = "error: disk full" in context
        return "error: disk full"

    monkeypatch.setattr(
        "openhands.sdk.utils.supercompress.compress_context", fake_compress
    )
    user = MessageEvent(
        source="user",
        llm_message=Message(
            role="user", content=[TextContent(text="find the disk full error")]
        ),
    )
    observation = ObservationEvent(
        source="environment",
        observation=TerminalObservation(
            command="pytest -q",
            content=[TextContent(text=("noise\n" * 4000) + "error: disk full\n")],
        ),
        action_id="action-1",
        tool_name="terminal",
        tool_call_id="call-1",
    )
    messages = LLMConvertibleEvent.events_to_messages([user, observation])
    tool_messages = [message for message in messages if message.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content[0].text == "error: disk full"
    assert seen["query"] == "find the disk full error"
    assert seen["has_error"] is True
