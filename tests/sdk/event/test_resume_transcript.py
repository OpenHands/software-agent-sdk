"""Tests for openhands.sdk.event.resume_transcript."""

from __future__ import annotations

import json

import pytest

from openhands.sdk.event import (
    RESUME_CONTEXT_MARKER,
    ACPToolCallEvent,
    ActionEvent,
    MessageEvent,
    render_resume_transcript,
)
from openhands.sdk.event.resume_transcript import (
    DEFAULT_FOOTER,
    DEFAULT_HEADER_BODY,
)
from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.tool.builtins.finish import FinishAction


def _user(text: str) -> MessageEvent:
    return MessageEvent(
        source="user",
        llm_message=Message(role="user", content=[TextContent(text=text)]),
    )


def _assistant(text: str) -> MessageEvent:
    return MessageEvent(
        source="agent",
        llm_message=Message(role="assistant", content=[TextContent(text=text)]),
    )


def _finish(text: str) -> ActionEvent:
    return ActionEvent(
        source="agent",
        thought=[TextContent(text="")],
        action=FinishAction(message=text),
        tool_name="finish",
        tool_call_id="finish-1",
        tool_call=MessageToolCall(
            id="finish-1",
            name="finish",
            arguments=json.dumps({"message": text}),
            origin="completion",
        ),
        llm_response_id="resp-1",
    )


def _tool(
    call_id: str,
    *,
    title: str = "edit",
    status: str | None = "completed",
    raw_input: object | None = None,
    raw_output: object | None = None,
    is_error: bool = False,
    content: list | None = None,
    tool_kind: str | None = None,
) -> ACPToolCallEvent:
    return ACPToolCallEvent(
        tool_call_id=call_id,
        title=title,
        status=status,
        tool_kind=tool_kind,
        raw_input=raw_input,
        raw_output=raw_output,
        content=content,
        is_error=is_error,
    )


class TestRenderResumeTranscript:
    def test_empty_events_returns_none(self) -> None:
        assert render_resume_transcript([]) is None

    def test_only_irrelevant_events_returns_none(self) -> None:
        # Placeholder tool events with no input/output/error are skipped.
        placeholder = _tool("tc-1", raw_input=None, raw_output=None)
        assert render_resume_transcript([placeholder]) is None

    def test_renders_user_and_assistant_messages(self) -> None:
        out = render_resume_transcript([_user("Hi"), _assistant("Hello!")])
        assert out is not None
        assert out.startswith(RESUME_CONTEXT_MARKER)
        assert DEFAULT_HEADER_BODY in out
        assert "[USER]: Hi" in out
        assert "[ASSISTANT]: Hello!" in out
        assert out.endswith(DEFAULT_FOOTER)

    def test_renders_tool_use_with_status_and_io(self) -> None:
        event = _tool(
            "tc-1",
            title="bash",
            status="completed",
            raw_input={"command": "ls"},
            raw_output="file.txt",
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[TOOL USE: bash] (completed)" in out
        assert "input:" in out
        assert "command" in out
        assert "output:" in out
        assert "file.txt" in out

    def test_failed_tool_uses_failed_status(self) -> None:
        event = _tool(
            "tc-1",
            title="bash",
            status="completed",
            raw_output="boom",
            is_error=True,
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[TOOL USE: bash] (failed)" in out

    def test_renders_finish_action_as_agent_summary(self) -> None:
        out = render_resume_transcript([_finish("All done.")])
        assert out is not None
        assert "[AGENT]: All done." in out

    def test_deduplicates_tool_events_by_id(self) -> None:
        # Three streamed events for the same tool_call_id — only the terminal
        # event (with raw_output) should be rendered.
        pending = _tool("tc-1", title="bash", status="pending", raw_input={"c": 1})
        progress = _tool("tc-1", title="bash", status="in_progress", raw_input={"c": 1})
        completed = _tool(
            "tc-1",
            title="bash",
            status="completed",
            raw_input={"c": 1},
            raw_output="ok",
        )
        out = render_resume_transcript([pending, progress, completed])
        assert out is not None
        assert out.count("[TOOL USE: bash]") == 1
        assert "(completed)" in out
        assert "ok" in out

    def test_preserves_order_across_event_types(self) -> None:
        events = [
            _user("first user"),
            _tool("tc-1", title="bash", raw_input={"c": 1}, raw_output="hi"),
            _assistant("second assistant"),
            _finish("third agent"),
        ]
        out = render_resume_transcript(events)
        assert out is not None
        idx_user = out.index("[USER]: first user")
        idx_tool = out.index("[TOOL USE: bash]")
        idx_assistant = out.index("[ASSISTANT]: second assistant")
        idx_agent = out.index("[AGENT]: third agent")
        assert idx_user < idx_tool < idx_assistant < idx_agent

    def test_max_chars_truncates_with_ellipsis(self) -> None:
        events = [_user("x" * 5000)]
        out = render_resume_transcript(events, max_chars=200)
        assert out is not None
        assert len(out) == 200
        assert out.endswith("...")

    def test_max_message_chars_truncates_long_turn(self) -> None:
        events = [_user("x" * 10_000)]
        out = render_resume_transcript(events, max_message_chars=100)
        assert out is not None
        # The user line should be capped; the marker/header bytes still fit.
        user_line = next(ln for ln in out.splitlines() if ln.startswith("[USER]"))
        assert user_line.endswith("...")
        assert len(user_line) <= len("[USER]: ") + 100

    def test_max_tool_chars_truncates_tool_block(self) -> None:
        event = _tool(
            "tc-1",
            title="bash",
            raw_input={"command": "x" * 5000},
        )
        out = render_resume_transcript([event], max_tool_chars=120)
        assert out is not None
        tool_lines = [ln for ln in out.splitlines() if "x" in ln]
        # The whole tool block (joined lines) was capped; rendered text length
        # for the block is ≤ max_tool_chars.
        joined = "\n".join(out.splitlines())
        assert "..." in joined
        assert len(tool_lines) > 0

    def test_image_content_renders_as_placeholder(self) -> None:
        # ImageContent → "[Image: N URLs]" via content_to_str. Test that the
        # renderer doesn't crash and produces a labelled line.
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user",
                content=[
                    TextContent(text="see this:"),
                    ImageContent(image_urls=["http://example.com/a.png"]),
                ],
            ),
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "see this:" in out
        assert "[Image:" in out

    def test_empty_message_content_is_skipped(self) -> None:
        # A MessageEvent whose content renders to empty text should not
        # produce a stray "[USER]: " line.
        event = MessageEvent(
            source="user",
            llm_message=Message(role="user", content=[TextContent(text="   ")]),
        )
        assert render_resume_transcript([event]) is None

    def test_unknown_event_types_are_ignored(self) -> None:
        # Mix a MessageEvent with a "non-renderable" event — here, an
        # ActionEvent whose action is None (no message field).
        action_event = ActionEvent(
            source="agent",
            thought=[TextContent(text="thinking")],
            action=None,
            tool_name="x",
            tool_call_id="x-1",
            tool_call=MessageToolCall(
                id="x-1", name="x", arguments="{}", origin="completion"
            ),
            llm_response_id="r-1",
        )
        out = render_resume_transcript([action_event, _user("hi")])
        assert out is not None
        assert "[USER]: hi" in out
        assert "[AGENT]" not in out

    def test_custom_marker_and_header(self) -> None:
        out = render_resume_transcript(
            [_user("hi")],
            marker="<<CUSTOM MARKER>>",
            header_body="custom body",
            footer="--- end ---",
        )
        assert out is not None
        assert out.startswith("<<CUSTOM MARKER>>")
        assert "custom body" in out
        assert out.endswith("--- end ---")

    def test_empty_header_body_omits_header_paragraph(self) -> None:
        out = render_resume_transcript([_user("hi")], header_body="")
        assert out is not None
        lines = out.splitlines()
        assert lines[0] == RESUME_CONTEXT_MARKER
        assert lines[1] == ""
        # Next non-blank line is the user turn, not header body text.
        assert lines[2].startswith("[USER]")


class TestIsPatchEdit:
    def test_diff_content_with_old_text_is_patch(self) -> None:
        ev = _tool(
            "tc-1",
            content=[
                type(
                    "DiffBlock",
                    (),
                    {"type": "diff", "old_text": "before", "new_text": "after"},
                )()
            ],
        )
        assert ev.is_patch_edit is True

    def test_diff_content_without_old_text_is_full_write(self) -> None:
        ev = _tool(
            "tc-1",
            content=[
                type(
                    "DiffBlock",
                    (),
                    {"type": "diff", "old_text": None, "new_text": "whole file"},
                )()
            ],
        )
        assert ev.is_patch_edit is False

    def test_raw_input_old_string_fallback(self) -> None:
        ev = _tool(
            "tc-1",
            raw_input={"old_string": "x", "new_string": "y", "file_path": "/a"},
        )
        assert ev.is_patch_edit is True

    def test_raw_input_without_diff_keys_is_not_patch(self) -> None:
        ev = _tool("tc-1", raw_input={"command": "ls"})
        assert ev.is_patch_edit is False

    def test_no_content_no_raw_input_is_not_patch(self) -> None:
        ev = _tool("tc-1")
        assert ev.is_patch_edit is False


@pytest.mark.parametrize(
    "role,label",
    [("user", "[USER]"), ("assistant", "[ASSISTANT]")],
)
def test_role_labelling(role: str, label: str) -> None:
    event = MessageEvent(
        source="user" if role == "user" else "agent",
        llm_message=Message(role=role, content=[TextContent(text="x")]),  # type: ignore[arg-type]
    )
    out = render_resume_transcript([event])
    assert out is not None
    assert f"{label}: x" in out
