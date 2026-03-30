"""Tests for terminal escape sequence filtering.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

from openhands.tools.terminal.utils.escape_filter import (
    TerminalQueryFilter,
    filter_terminal_queries,
)


class TestFilterTerminalQueries:
    """Tests for the filter_terminal_queries function (stateless API)."""

    def test_dsr_query_removed(self):
        """DSR (Device Status Report) queries should be removed."""
        # \x1b[6n is the cursor position query
        output = "some text\x1b[6nmore text"
        result = filter_terminal_queries(output)
        assert result == "some textmore text"

    def test_osc_11_background_query_removed(self):
        """OSC 11 (background color query) should be removed."""
        # \x1b]11;?\x07 queries background color
        output = "start\x1b]11;?\x07end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_osc_10_foreground_query_removed(self):
        """OSC 10 (foreground color query) should be removed."""
        output = "start\x1b]10;?\x07end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_osc_4_palette_query_removed(self):
        """OSC 4 (palette color query) should be removed."""
        output = "start\x1b]4;?\x07end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_osc_4_palette_with_index_query_removed(self):
        """OSC 4 with palette index (e.g., color 5) should be removed."""
        output = "start\x1b]4;5;?\x07end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_osc_12_cursor_color_query_removed(self):
        """OSC 12 (cursor color query) should be removed."""
        output = "start\x1b]12;?\x07end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_osc_17_highlight_query_removed(self):
        """OSC 17 (highlight background query) should be removed."""
        output = "start\x1b]17;?\x07end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_osc_set_title_preserved(self):
        """OSC 0 (set window title) should NOT be removed - it's a SET, not query."""
        output = "start\x1b]0;My Window Title\x07end"
        result = filter_terminal_queries(output)
        assert result == output  # Preserved as-is

    def test_osc_hyperlink_preserved(self):
        """OSC 8 (hyperlink) should NOT be removed."""
        output = "start\x1b]8;;https://example.com\x07link\x1b]8;;\x07end"
        result = filter_terminal_queries(output)
        assert result == output  # Preserved as-is

    def test_osc_with_st_terminator_removed(self):
        """OSC queries with ST terminator should be removed."""
        # ST terminator is \x1b\\
        output = "start\x1b]11;?\x1b\\end"
        result = filter_terminal_queries(output)
        assert result == "startend"

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

    def test_decrqss_query_removed(self):
        """DECRQSS (Request Selection or Setting) queries should be removed."""
        # \x1bP$q...\x1b\\
        output = "start\x1bP$qsetting\x1b\\end"
        result = filter_terminal_queries(output)
        assert result == "startend"

    def test_colors_preserved(self):
        """ANSI color codes should NOT be removed."""
        # Red text: \x1b[31m
        output = "normal \x1b[31mred text\x1b[0m normal"
        result = filter_terminal_queries(output)
        assert result == output

    def test_cursor_movement_preserved(self):
        """Cursor movement codes should NOT be removed."""
        # Move cursor: \x1b[H (home), \x1b[5A (up 5)
        output = "start\x1b[Hmiddle\x1b[5Aend"
        result = filter_terminal_queries(output)
        assert result == output

    def test_multiple_queries_removed(self):
        """Multiple query sequences should all be removed."""
        output = "\x1b[6n\x1b]11;?\x07text\x1b[6n"
        result = filter_terminal_queries(output)
        assert result == "text"

    def test_mixed_queries_and_formatting(self):
        """Queries removed while formatting preserved."""
        # Color + query + more color
        output = "\x1b[32mgreen\x1b[6nmore\x1b]11;?\x07text\x1b[0m"
        result = filter_terminal_queries(output)
        assert result == "\x1b[32mgreenmoretext\x1b[0m"

    def test_empty_string(self):
        """Empty string should return empty string."""
        assert filter_terminal_queries("") == ""

    def test_no_escape_sequences(self):
        """Plain text without escape sequences passes through."""
        output = "Hello, World!"
        assert filter_terminal_queries(output) == output

    def test_unicode_preserved(self):
        """Unicode characters should be preserved."""
        output = "Hello 🌍 World \x1b[6n with emoji"
        result = filter_terminal_queries(output)
        assert result == "Hello 🌍 World  with emoji"


class TestTerminalQueryFilter:
    """Tests for the stateful TerminalQueryFilter class."""

    def test_single_chunk_complete_query(self):
        """Complete query in single chunk should be removed."""
        f = TerminalQueryFilter()
        result = f.filter("text\x1b[6nmore")
        result += f.flush()
        assert result == "textmore"

    def test_split_dsr_query_across_chunks(self):
        """DSR query split across chunks should be removed."""
        f = TerminalQueryFilter()
        # Chunk 1 ends with ESC [
        result1 = f.filter("prefix\x1b[")
        # Chunk 2 starts with 6n
        result2 = f.filter("6nsuffix")
        result2 += f.flush()
        # Query should be removed when combined
        assert result1 + result2 == "prefixsuffix"

    def test_split_osc_query_across_chunks(self):
        """OSC query split across chunks should be removed."""
        f = TerminalQueryFilter()
        # Chunk 1: ESC ] 11 ;
        result1 = f.filter("start\x1b]11;")
        # Chunk 2: ? BEL
        result2 = f.filter("?\x07end")
        result2 += f.flush()
        assert result1 + result2 == "startend"

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

    def test_incremental_output_simulated(self):
        """Simulates incremental output from long-running command."""
        f = TerminalQueryFilter()
        # Simulating: "Progress: 25%\x1b[6n50%\x1b]11;?\x0775%100%"
        # Split into chunks at arbitrary points
        chunk1 = "Progress: 25%\x1b["  # DSR starts
        chunk2 = "6n50%\x1b]"  # DSR ends, OSC starts
        chunk3 = "11;?\x0775%100%"  # OSC ends

        r1 = f.filter(chunk1)
        r2 = f.filter(chunk2)
        r3 = f.filter(chunk3)
        r3 += f.flush()

        assert r1 + r2 + r3 == "Progress: 25%50%75%100%"

    def test_decrqss_split_across_chunks(self):
        """DECRQSS query split across chunks should be removed."""
        f = TerminalQueryFilter()
        # DCS P $ q ... ST where ST is ESC \
        result1 = f.filter("text\x1bP$q")
        result2 = f.filter("setting\x1b\\more")
        result2 += f.flush()
        assert result1 + result2 == "textmore"

    def test_decrqss_split_at_st_terminator(self):
        """DECRQSS query split exactly at ST terminator boundary should be removed.

        Regression test for: https://github.com/OpenHands/software-agent-sdk/pull/2334
        When the chunk boundary falls between the ESC and backslash of the ST
        terminator (\x1b\\), the entire DCS sequence must still be filtered.
        """
        f = TerminalQueryFilter()
        # Split exactly at the ST terminator: ESC is at end of chunk 1
        # chunk 1: "text\x1bP$qsetting\x1b" - ESC is start of ST terminator
        # chunk 2: "\\more" - backslash completes ST
        result1 = f.filter("text\x1bP$qsetting\x1b")
        result2 = f.filter("\\more")
        result2 += f.flush()
        assert result1 + result2 == "textmore"

    def test_formatting_preserved_across_chunks(self):
        """Color/formatting codes split across chunks should be preserved."""
        f = TerminalQueryFilter()
        # Color code split: ESC [ 3 | 1 m
        result1 = f.filter("normal \x1b[3")
        result2 = f.filter("1mred text\x1b[0m")
        result2 += f.flush()
        assert result1 + result2 == "normal \x1b[31mred text\x1b[0m"

    def test_mixed_queries_and_formatting_across_chunks(self):
        """Mixed queries and formatting split across chunks."""
        f = TerminalQueryFilter()
        # Input: "\x1b[32mgreen\x1b[6nmore\x1b]11;?\x07text\x1b[0m"
        # Split weirdly
        chunk1 = "\x1b[32mgreen\x1b["  # color + start of DSR
        chunk2 = "6nmore\x1b]11"  # DSR ends + start of OSC
        chunk3 = ";?\x07text\x1b[0m"  # OSC ends + reset

        r1 = f.filter(chunk1)
        r2 = f.filter(chunk2)
        r3 = f.filter(chunk3)
        r3 += f.flush()

        assert r1 + r2 + r3 == "\x1b[32mgreenmoretext\x1b[0m"
