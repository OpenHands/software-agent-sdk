"""Tests for terminal escape sequence filtering.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

import tempfile

import pytest

from openhands.tools.terminal.definition import TerminalAction
from openhands.tools.terminal.terminal import create_terminal_session
from openhands.tools.terminal.utils.escape_filter import (
    TerminalQueryFilter,
    filter_terminal_queries,
)


class TestFilterTerminalQueries:
    """Tests for the filter_terminal_queries function (stateless API)."""

    @pytest.mark.parametrize(
        ("output", "expected"),
        [
            pytest.param("some text\x1b[6nmore text", "some textmore text", id="dsr"),
            pytest.param("start\x1b]11;?\x07end", "startend", id="osc_11_background"),
            pytest.param("start\x1b]10;?\x07end", "startend", id="osc_10_foreground"),
            pytest.param("start\x1b]4;?\x07end", "startend", id="osc_4_palette"),
            pytest.param(
                "start\x1b]4;5;?\x07end", "startend", id="osc_4_palette_with_index"
            ),
            pytest.param("start\x1b]12;?\x07end", "startend", id="osc_12_cursor_color"),
            pytest.param("start\x1b]17;?\x07end", "startend", id="osc_17_highlight"),
            pytest.param(
                "start\x1b]11;?\x1b\\end", "startend", id="osc_with_st_terminator"
            ),
            pytest.param("start\x1bP$qsetting\x1b\\end", "startend", id="decrqss"),
            pytest.param(
                "\x1b[6n\x1b]11;?\x07text\x1b[6n", "text", id="multiple_queries"
            ),
            pytest.param(
                "\x1b[32mgreen\x1b[6nmore\x1b]11;?\x07text\x1b[0m",
                "\x1b[32mgreenmoretext\x1b[0m",
                id="mixed_queries_and_formatting",
            ),
            pytest.param(
                "Hello 🌍 World \x1b[6n with emoji",
                "Hello 🌍 World  with emoji",
                id="unicode",
            ),
        ],
    )
    def test_queries_removed(self, output, expected):
        assert filter_terminal_queries(output) == expected

    @pytest.mark.parametrize(
        "output",
        [
            # OSC 0 sets the title; it is not a query.
            pytest.param("start\x1b]0;My Window Title\x07end", id="osc_set_title"),
            pytest.param(
                "start\x1b]8;;https://example.com\x07link\x1b]8;;\x07end",
                id="osc_hyperlink",
            ),
            pytest.param("normal \x1b[31mred text\x1b[0m normal", id="colors"),
            pytest.param("start\x1b[Hmiddle\x1b[5Aend", id="cursor_movement"),
        ],
    )
    def test_non_queries_preserved(self, output):
        assert filter_terminal_queries(output) == output

    def test_da_primary_query_removed(self):
        """DA (Device Attributes) primary queries should be removed."""
        # \x1b[c and \x1b[0c
        output = "start\x1b[cend"
        result = filter_terminal_queries(output)
        assert result == "startend"

        output2 = "start\x1b[0cend"
        result2 = filter_terminal_queries(output2)
        assert result2 == "startend"

    def test_da2_secondary_query_removed(self):
        """DA2 (Secondary Device Attributes) queries should be removed."""
        # \x1b[>c and \x1b[>0c
        output = "start\x1b[>cend"
        result = filter_terminal_queries(output)
        assert result == "startend"

        output2 = "start\x1b[>0cend"
        result2 = filter_terminal_queries(output2)
        assert result2 == "startend"

    def test_empty_string(self):
        """Empty string should return empty string."""
        assert filter_terminal_queries("") == ""

    def test_no_escape_sequences(self):
        """Plain text without escape sequences passes through."""
        output = "Hello, World!"
        assert filter_terminal_queries(output) == output


class TestTerminalQueryFilter:
    """Tests for the stateful TerminalQueryFilter class."""

    def test_single_chunk_complete_query(self):
        """Complete query in single chunk should be removed."""
        f = TerminalQueryFilter()
        result = f.filter("text\x1b[6nmore")
        result += f.flush()
        assert result == "textmore"

    @pytest.mark.parametrize(
        ("chunks", "expected"),
        [
            pytest.param(("prefix\x1b[", "6nsuffix"), "prefixsuffix", id="split_dsr"),
            pytest.param(("start\x1b]11;", "?\x07end"), "startend", id="split_osc"),
            pytest.param(
                ("Progress: 25%\x1b[", "6n50%\x1b]", "11;?\x0775%100%"),
                "Progress: 25%50%75%100%",
                id="incremental_output",
            ),
            pytest.param(
                ("text\x1bP$q", "setting\x1b\\more"), "textmore", id="decrqss_split"
            ),
            # Regression for https://github.com/OpenHands/software-agent-sdk/pull/2334:
            # the boundary falls between the ESC and backslash of the ST terminator.
            pytest.param(
                ("text\x1bP$qsetting\x1b", "\\more"),
                "textmore",
                id="decrqss_split_at_st_terminator",
            ),
            pytest.param(
                ("normal \x1b[3", "1mred text\x1b[0m"),
                "normal \x1b[31mred text\x1b[0m",
                id="formatting_preserved",
            ),
            pytest.param(
                ("\x1b[32mgreen\x1b[", "6nmore\x1b]11", ";?\x07text\x1b[0m"),
                "\x1b[32mgreenmoretext\x1b[0m",
                id="mixed_queries_and_formatting",
            ),
        ],
    )
    def test_sequence_split_across_chunks(self, chunks, expected):
        f = TerminalQueryFilter()
        result = "".join(f.filter(chunk) for chunk in chunks)
        result += f.flush()
        assert result == expected

    def test_split_esc_alone_at_end(self):
        """Lone ESC at end of chunk should be held for next chunk."""
        f = TerminalQueryFilter()
        # Chunk 1 ends with just ESC
        result1 = f.filter("text\x1b")
        # ESC should be held (not in result1 yet)
        assert result1 == "text"
        # Chunk 2 completes non-query sequence
        result2 = f.filter("[32mgreen")
        result2 += f.flush()
        # Color code preserved
        assert result2 == "\x1b[32mgreen"

    def test_incomplete_sequence_flushed_on_complete(self):
        """Incomplete sequence at end should be flushed if not a query."""
        f = TerminalQueryFilter()
        # Chunk with incomplete color code at end
        result1 = f.filter("text\x1b[32")
        assert result1 == "text"
        # Flush emits the non-query bytes
        flushed = f.flush()
        assert flushed == "\x1b[32"

    def test_reset_clears_pending(self):
        """Reset should clear any pending bytes."""
        f = TerminalQueryFilter()
        # Leave incomplete sequence
        _ = f.filter("text\x1b[")
        # Reset
        f.reset()
        # New filter call shouldn't see old pending
        result = f.filter("new text")
        result += f.flush()
        assert result == "new text"

    def test_multiple_commands_with_reset(self):
        """Simulates multiple command outputs with reset between them."""
        f = TerminalQueryFilter()
        # Command 1 output
        result1 = f.filter("cmd1 output\x1b[6n")
        result1 += f.flush()
        assert result1 == "cmd1 output"
        # Reset for next command
        f.reset()
        # Command 2 output
        result2 = f.filter("cmd2 output\x1b]11;?\x07")
        result2 += f.flush()
        assert result2 == "cmd2 output"


# ── Integration tests: filter wired into TerminalSession ──────────────
# These tests execute real commands through TerminalSession to verify
# that terminal query sequences are filtered from captured output.
# They exercise the full pipeline (PTY → output capture → filter)
# rather than just the TerminalQueryFilter class in isolation.
#
# On main (without the filter), these tests FAIL because the raw
# query sequences pass through to the observation text.

terminal_types = ["subprocess", "tmux"]
parametrize_terminal = pytest.mark.parametrize("terminal_type", terminal_types)


@parametrize_terminal
def test_session_filters_osc_background_query(terminal_type):
    """OSC 11 background-color query in command output is stripped.

    Tools like `gh` and `npm` emit OSC queries for terminal capability
    detection. Without filtering, these leak into the observation text
    and produce visible garbage when displayed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session = create_terminal_session(work_dir=tmp, terminal_type=terminal_type)
        session.initialize()
        try:
            obs = session.execute(
                TerminalAction(command="printf 'before\\x1b]11;?\\x07after\\n'")
            )
            assert "\x1b]11;?" not in obs.text
            assert "before" in obs.text
            assert "after" in obs.text
        finally:
            session.close()


@parametrize_terminal
def test_session_filters_dsr_cursor_query(terminal_type):
    """DSR cursor-position query (\\x1b[6n) is stripped from output.

    Spinner libraries send DSR to determine cursor position. The query
    must not appear in the returned observation.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session = create_terminal_session(work_dir=tmp, terminal_type=terminal_type)
        session.initialize()
        try:
            obs = session.execute(
                TerminalAction(command="printf 'hello\\x1b[6nworld\\n'")
            )
            assert "\x1b[6n" not in obs.text
            assert "hello" in obs.text
            assert "world" in obs.text
        finally:
            session.close()


@parametrize_terminal
def test_session_filters_multiple_query_types(terminal_type):
    """Multiple query types in a single command output are all stripped."""
    with tempfile.TemporaryDirectory() as tmp:
        session = create_terminal_session(work_dir=tmp, terminal_type=terminal_type)
        session.initialize()
        try:
            obs = session.execute(
                TerminalAction(command=("printf 'a\\x1b[6nb\\x1b]11;?\\x07c\\n'"))
            )
            assert "\x1b[6n" not in obs.text
            assert "\x1b]11;?" not in obs.text
            assert "a" in obs.text
            assert "b" in obs.text
            assert "c" in obs.text
        finally:
            session.close()


def test_session_preserves_ansi_colors():
    """ANSI color codes must survive filtering (not queries).

    Only tested with subprocess; tmux capture-pane strips ANSI attributes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session = create_terminal_session(work_dir=tmp, terminal_type="subprocess")
        session.initialize()
        try:
            obs = session.execute(
                TerminalAction(command=("printf '\\x1b[32mgreen text\\x1b[0m\\n'"))
            )
            assert "\x1b[32m" in obs.text
            assert "\x1b[0m" in obs.text
            assert "green text" in obs.text
        finally:
            session.close()


def test_session_filters_query_but_preserves_colors():
    """Mixed output: queries removed, formatting kept.

    Simulates real-world scenario where a tool emits both ANSI colors
    for display formatting and terminal queries for capability detection
    in the same output stream.

    Only tested with subprocess; tmux capture-pane strips ANSI attributes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        session = create_terminal_session(work_dir=tmp, terminal_type="subprocess")
        session.initialize()
        try:
            obs = session.execute(
                TerminalAction(
                    command=("printf '\\x1b[32mgreen\\x1b]11;?\\x07text\\x1b[0m\\n'")
                )
            )
            # Query removed
            assert "\x1b]11;?" not in obs.text
            # Colors preserved
            assert "\x1b[32m" in obs.text
            assert "\x1b[0m" in obs.text
            assert "green" in obs.text
            assert "text" in obs.text
        finally:
            session.close()
