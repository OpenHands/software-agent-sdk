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

    def test_cross_session_same_id_both_render(self) -> None:
        # ACP providers like Codex reset tool_call_id counters each session.
        # A user message between two events with the same id means they come
        # from different sessions — both must render, not just the latest.
        session_1 = _tool(
            "exec_1",
            title="bash",
            raw_input={"command": "pytest"},
            raw_output="5 passed",
        )
        boundary = _user("please continue")  # session boundary
        session_2 = _tool(
            "exec_1",  # same id, new session
            title="bash",
            raw_input={"command": "ls"},
            raw_output="file.txt",
        )
        out = render_resume_transcript([session_1, boundary, session_2])
        assert out is not None
        # Both sessions render (not just the last).
        assert out.count("[TOOL USE: bash]") == 2
        assert "5 passed" in out
        assert "file.txt" in out

    def test_cross_session_dedup_within_session_still_collapses(self) -> None:
        # Within one session, pending→completed for the same id still
        # collapses to the terminal event.
        pending = _tool("exec_1", title="bash", status="pending")
        completed = _tool("exec_1", title="bash", raw_output="ok", status="completed")
        # Note: no MessageEvent between them — same session.
        out = render_resume_transcript([pending, completed])
        assert out is not None
        assert out.count("[TOOL USE: bash]") == 1
        assert "(completed)" in out

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

    def test_max_chars_keeps_footer_when_tight(self) -> None:
        # ``max_chars`` so tight that even header+footer can't fit alone:
        # the tail-truncate fallback preserves the end of the transcript,
        # which is the footer (and the freshest event content just above it).
        events = [_user("x" * 5000)]
        out = render_resume_transcript(events, max_chars=200)
        assert out is not None
        assert len(out) == 200
        assert out.endswith(DEFAULT_FOOTER)

    def test_max_chars_preserves_header_footer_and_newest_event(self) -> None:
        # With a budget large enough to hold the marker, header body, footer,
        # and a chunk of body, the marker stays at the top, the footer at the
        # bottom, and the BODY is head-truncated so the latest events survive.
        events = [
            _user("OLD first turn " + "o" * 500),
            _assistant("OLD assistant reply " + "o" * 500),
            _user("NEW most recent turn"),
        ]
        out = render_resume_transcript(events, max_chars=500)
        assert out is not None
        assert len(out) <= 500
        assert out.startswith(RESUME_CONTEXT_MARKER)
        assert out.endswith(DEFAULT_FOOTER)
        # The latest event must survive intact.
        assert "NEW most recent turn" in out
        # The oldest turn should be dropped, with a "..." cut marker present.
        assert "OLD first turn" not in out
        assert "..." in out

    @pytest.mark.parametrize("max_chars", [50, 100, 500])
    def test_max_chars_strictly_bounds_output(self, max_chars: int) -> None:
        # For budgets >= len(marker), output must be non-None with length <= max_chars.
        events = [_user("hello"), _assistant("world")]
        out = render_resume_transcript(events, max_chars=max_chars)
        assert out is not None
        assert len(out) <= max_chars

    @pytest.mark.parametrize("max_chars", [0, 1, 10, 23])
    def test_max_chars_below_marker_length_returns_none(self, max_chars: int) -> None:
        # A budget below len(RESUME_CONTEXT_MARKER) (24 chars) cannot fit the
        # complete marker. Returning a partial marker would bypass the
        # double-resume guard (callers check out.startswith(RESUME_CONTEXT_MARKER)),
        # so None is returned instead so callers treat it as a fresh conversation.
        assert render_resume_transcript([_user("hello")], max_chars=max_chars) is None

    @pytest.mark.parametrize("max_chars", [24, 30, 200])
    def test_tight_budget_output_starts_with_complete_marker(
        self, max_chars: int
    ) -> None:
        # For any budget >= len(marker), even a very tight cap returns a
        # transcript that starts with the full, untruncated marker so callers
        # can reliably detect a resume and the double-resume guard works.
        events = [_user("x" * 5000)]
        out = render_resume_transcript(events, max_chars=max_chars)
        assert out is not None
        assert len(out) <= max_chars
        assert out.startswith(RESUME_CONTEXT_MARKER)

    @pytest.mark.parametrize("cap", [0, 1, 2, 3, 4, 50])
    def test_max_message_chars_strictly_bounds_text(self, cap: int) -> None:
        # The text portion of the message line (after "[USER]: ") must be
        # ≤ cap characters regardless of how small ``cap`` is.
        events = [_user("x" * 500)]
        out = render_resume_transcript(events, max_message_chars=cap)
        assert out is not None
        user_line = next(ln for ln in out.splitlines() if ln.startswith("[USER]"))
        text_portion = user_line[len("[USER]: ") :]
        assert len(text_portion) <= cap

    def test_max_tool_chars_zero_skips_tool_block(self) -> None:
        # With ``max_tool_chars=0`` the rendered tool block becomes "" and
        # is dropped — non-tool events still render.
        events = [
            _user("hi"),
            _tool("tc-1", title="bash", raw_input={"command": "ls"}),
        ]
        out = render_resume_transcript(events, max_tool_chars=0)
        assert out is not None
        assert "[USER]: hi" in out
        assert "[TOOL USE:" not in out

    def test_content_only_tool_event_is_rendered(self) -> None:
        # A tool event whose only payload is structured ``content`` (no
        # ``raw_input``/``raw_output``/``is_error``) must not be filtered
        # out as a placeholder. ACP servers such as Codex and Gemini emit
        # the diff or terminal data via ``content`` rather than raw I/O.
        event = _tool(
            "tc-1",
            title="edit",
            content=[
                {
                    "type": "diff",
                    "path": "/workspace/app.py",
                    "old_text": "old",
                    "new_text": "new",
                }
            ],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[TOOL USE: edit]" in out
        assert "content:" in out
        assert "[diff patch] /workspace/app.py" in out

    def test_content_only_tool_event_camelcase_diff(self) -> None:
        # Same as above with JSON wire keys — content-only rendering must
        # accept ``oldText`` / ``newText`` aliases.
        event = _tool(
            "tc-1",
            title="Edit",
            content=[
                {
                    "type": "diff",
                    "path": "/workspace/app.py",
                    "oldText": "old",
                    "newText": "new",
                }
            ],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[diff patch] /workspace/app.py" in out

    def test_content_only_write_block_labelled_as_write(self) -> None:
        # A diff block without ``old_text`` is a full-file write, not a
        # patch; the rendered line reflects that.
        event = _tool(
            "tc-1",
            title="write",
            content=[
                {
                    "type": "diff",
                    "path": "/workspace/new.py",
                    "old_text": None,
                    "new_text": "whole file",
                }
            ],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[diff write] /workspace/new.py" in out

    def test_direct_text_block_renders_verbatim(self) -> None:
        # ``_serialize_tool_content`` preserves direct ``{type: "text",
        # text: "..."}`` ContentBlocks at the top level (without the
        # ``{type: "content"}`` wrapper). Their text must be rendered, not
        # squashed to ``[text]``.
        event = _tool(
            "tc-1",
            title="search",
            content=[{"type": "text", "text": "matched 12 lines"}],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "matched 12 lines" in out
        assert "[text]" not in out

    def test_direct_image_block_renders_placeholder(self) -> None:
        event = _tool(
            "tc-1",
            title="screenshot",
            content=[
                {"type": "image", "data": "base64-bytes", "mimeType": "image/png"}
            ],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[Image]" in out

    @pytest.mark.parametrize("falsey_output", [0, False, ""])
    def test_falsey_raw_output_is_rendered(self, falsey_output: object) -> None:
        # Tools can legitimately return falsey scalars: ``0`` from a shell
        # exit code, ``False`` from a boolean predicate, ``""`` from a
        # silent command. None of these are placeholders.
        event = _tool(
            "tc-1",
            title="cmd",
            raw_input={"command": "true"},
            raw_output=falsey_output,
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "output:" in out
        assert str(falsey_output) in out

    def test_falsey_raw_output_only_is_not_a_placeholder(self) -> None:
        # ``raw_output=0`` is a valid completion; even without ``raw_input``
        # the event should render rather than being dropped.
        event = _tool("tc-1", title="cmd", raw_output=0)
        out = render_resume_transcript([event])
        assert out is not None
        assert "[TOOL USE: cmd]" in out
        assert "output:" in out

    def test_content_text_block_renders_inline(self) -> None:
        # ToolCallContent {type: "content", content: {type: "text", ...}}
        # is the ACP wrapper around generic ContentBlock variants. Text
        # content should appear inline under "content:".
        event = _tool(
            "tc-1",
            title="search",
            content=[
                {
                    "type": "content",
                    "content": {"type": "text", "text": "found 3 matches"},
                }
            ],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "found 3 matches" in out

    def test_content_terminal_block_renders_label(self) -> None:
        event = _tool(
            "tc-1",
            title="shell",
            content=[{"type": "terminal", "terminal_id": "term-abc"}],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "[terminal term-abc]" in out

    def test_placeholder_with_empty_content_still_skipped(self) -> None:
        # A pending event with no input, no output, no error, and an empty
        # content list is still a placeholder and should be dropped.
        event = _tool("tc-1", content=[])
        assert render_resume_transcript([event]) is None

    def test_content_rendered_alongside_raw_io(self) -> None:
        # When both content and raw I/O are present, both render (no
        # information lost).
        event = _tool(
            "tc-1",
            title="edit",
            raw_input={"file_path": "/foo"},
            raw_output="ok",
            content=[
                {"type": "diff", "path": "/foo", "old_text": "a", "new_text": "b"}
            ],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "input:" in out
        assert "content:" in out
        assert "output:" in out
        assert "[diff patch] /foo" in out

    def test_extended_content_is_included(self) -> None:
        # ``MessageEvent.extended_content`` (AgentContext / hook-provided
        # per-turn context) is folded in by ``to_llm_message()`` and ACP sees
        # it as part of the turn. The transcript must include it so the
        # resumed agent has the same context.
        event = MessageEvent(
            source="user",
            llm_message=Message(
                role="user", content=[TextContent(text="base content")]
            ),
            extended_content=[TextContent(text="extra context from hook")],
        )
        out = render_resume_transcript([event])
        assert out is not None
        assert "base content" in out
        assert "extra context from hook" in out

    def test_max_message_chars_truncates_long_turn(self) -> None:
        events = [_user("x" * 10_000)]
        out = render_resume_transcript(events, max_message_chars=100)
        assert out is not None
        user_line = next(ln for ln in out.splitlines() if ln.startswith("[USER]"))
        assert user_line.endswith("...")
        # text portion (after "[USER]: ") capped at max_message_chars.
        assert len(user_line) - len("[USER]: ") <= 100

    def test_max_tool_chars_truncates_tool_block(self) -> None:
        event = _tool(
            "tc-1",
            title="bash",
            raw_input={"command": "x" * 5000},
        )
        out = render_resume_transcript([event], max_tool_chars=120)
        assert out is not None
        # Recover the rendered tool block from the body (between header and
        # footer) and verify the whole block is bounded by max_tool_chars.
        body_start = out.index("[TOOL USE:")
        body_end = out.index(DEFAULT_FOOTER)
        tool_block = out[body_start:body_end].strip()
        assert "..." in tool_block
        assert len(tool_block) <= 120

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

    def test_dict_content_with_old_text_is_patch(self) -> None:
        # Persisted ACP events store content blocks as plain dicts (via
        # ``_serialize_tool_content`` → ``model_dump``). The property must
        # handle both attribute and dict access.
        ev = _tool(
            "tc-1",
            content=[{"type": "diff", "old_text": "before", "new_text": "after"}],
        )
        assert ev.is_patch_edit is True

    def test_dict_content_without_old_text_is_full_write(self) -> None:
        ev = _tool(
            "tc-1",
            content=[{"type": "diff", "old_text": None, "new_text": "whole file"}],
        )
        assert ev.is_patch_edit is False

    def test_model_validate_dict_content_classifies_correctly(self) -> None:
        # Regression for the QA reviewer's failure case: an event reconstructed
        # via ``model_validate`` from a JSON-shaped payload keeps ``content[0]``
        # as a dict, and ``is_patch_edit`` must still classify it correctly.
        ev = ACPToolCallEvent.model_validate(
            {
                "tool_call_id": "validated-diff",
                "title": "Edit",
                "content": [
                    {
                        "type": "diff",
                        "old_text": "before",
                        "new_text": "after",
                    }
                ],
            }
        )
        assert ev.content is not None
        assert isinstance(ev.content[0], dict)
        assert ev.is_patch_edit is True

    def test_raw_input_only_new_string_is_not_patch(self) -> None:
        # A Write/create payload may carry only ``new_string`` — that's not
        # a patch edit, it's a full-file write.
        ev = _tool("tc-1", raw_input={"new_string": "whole file", "file_path": "/a"})
        assert ev.is_patch_edit is False

    def test_raw_input_empty_old_string_is_not_patch(self) -> None:
        # An empty ``old_string`` has nothing to patch — it's effectively a
        # create.
        ev = _tool("tc-1", raw_input={"old_string": "", "new_string": "y"})
        assert ev.is_patch_edit is False

    def test_diff_content_takes_precedence_over_raw_input(self) -> None:
        # If both signals are present, the structured ACP content block
        # wins — it's the protocol-level source of truth.
        ev = _tool(
            "tc-1",
            content=[{"type": "diff", "old_text": None, "new_text": "x"}],
            raw_input={"old_string": "abc", "new_string": "def"},
        )
        assert ev.is_patch_edit is False

    def test_camel_case_oldText_alias_classifies_as_patch(self) -> None:
        # ACP's JSON wire format uses camelCase: ``oldText`` / ``newText``.
        # When events arrive from an external API or websocket frame the
        # dict keys keep that shape because ``content`` is ``list[Any]``.
        ev = _tool(
            "tc-1",
            content=[{"type": "diff", "oldText": "before", "newText": "after"}],
        )
        assert ev.is_patch_edit is True

    def test_camel_case_without_oldText_is_full_write(self) -> None:
        ev = _tool(
            "tc-1",
            content=[{"type": "diff", "oldText": None, "newText": "whole file"}],
        )
        assert ev.is_patch_edit is False

    def test_diff_block_after_other_blocks_is_found(self) -> None:
        # ACP content is a list of variants in any order — a text or
        # terminal block can precede the diff. ``is_patch_edit`` must scan
        # the whole list rather than only checking ``content[0]``.
        ev = _tool(
            "tc-1",
            content=[
                {"type": "content", "content": {"type": "text", "text": "Editing…"}},
                {"type": "diff", "old_text": "before", "new_text": "after"},
            ],
        )
        assert ev.is_patch_edit is True

    def test_any_diff_block_with_old_text_is_patch(self) -> None:
        # A multi-file tool call can include a write (``oldText=None``)
        # alongside a patch (``oldText`` set). If *any* diff is a patch,
        # the event is a patch edit; only an all-writes set classifies as
        # a write.
        ev_patch_then_write = _tool(
            "tc-1",
            content=[
                {"type": "diff", "old_text": "before", "new_text": "after"},
                {"type": "diff", "old_text": None, "new_text": "second"},
            ],
        )
        assert ev_patch_then_write.is_patch_edit is True

        ev_write_then_patch = _tool(
            "tc-2",
            content=[
                {"type": "diff", "old_text": None, "new_text": "create"},
                {"type": "diff", "old_text": "before", "new_text": "after"},
            ],
        )
        assert ev_write_then_patch.is_patch_edit is True

    def test_all_diff_blocks_writes_classify_as_write(self) -> None:
        ev = _tool(
            "tc-1",
            content=[
                {"type": "diff", "old_text": None, "new_text": "a"},
                {"type": "diff", "old_text": None, "new_text": "b"},
            ],
        )
        assert ev.is_patch_edit is False

    def test_diff_content_blocks_never_fall_back_to_raw_input(self) -> None:
        # Structured ACP content is authoritative. When all diff blocks are
        # writes the event must classify as a write even if ``raw_input``
        # would otherwise trigger the patch fallback.
        ev = _tool(
            "tc-1",
            content=[{"type": "diff", "old_text": None, "new_text": "create"}],
            raw_input={"old_string": "abc", "new_string": "def"},
        )
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
