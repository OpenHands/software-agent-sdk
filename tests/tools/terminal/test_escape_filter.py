"""Tests for terminal escape sequence filtering.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

from openhands.tools.terminal.utils.escape_filter import filter_terminal_queries


class TestFilterTerminalQueries:
    """Tests for the filter_terminal_queries function."""

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
