"""Filter terminal query sequences from captured output.

When CLI tools (like `gh`, `npm`, etc.) run inside a PTY, they may send
terminal query sequences as part of their progress/spinner UI. These queries
get captured as output. When displayed, the terminal processes them and
responds, causing visible escape code garbage.

This module provides filtering to remove these query sequences while
preserving legitimate formatting escape codes (colors, bold, etc.).

NOTE: This module only handles queries captured from PTY output (commands
run via the terminal tool). SDK-side queries (e.g., Rich library capability
detection) are not addressed here and would require filtering at the
conversation/visualizer boundary.

See: https://github.com/OpenHands/software-agent-sdk/issues/2244
"""

import re


# Terminal query sequences that trigger responses (and cause visible garbage)
# These should be stripped from captured output before display.
#
# Reference: ECMA-48, XTerm Control Sequences
# https://invisible-island.net/xterm/ctlseqs/ctlseqs.html

# DSR (Device Status Report) - cursor position query
# Format: ESC [ 6 n  ->  Response: ESC [ row ; col R
_DSR_PATTERN = re.compile(rb"\x1b\[6n")

# OSC (Operating System Command) queries
# Format: ESC ] Ps ; ? (BEL | ST)
# The ";?" pattern indicates a QUERY (vs SET which has actual values)
# Examples:
#   OSC 10 ; ? - foreground color query
#   OSC 11 ; ? - background color query
#   OSC 4 ; index ; ? - palette color query
#   OSC 12 ; ? - cursor color query
#   OSC 17 ; ? - highlight background query
# Terminators: BEL (\x07) or ST (ESC \)
#
# This pattern matches ANY OSC query (ending with ;?) rather than
# specific codes, making it future-proof for other query types.
_OSC_QUERY_PATTERN = re.compile(
    rb"\x1b\]"  # OSC introducer
    rb"\d+"  # Parameter number (10, 11, 4, 12, etc.)
    rb"(?:;[^;\x07\x1b]*)?"  # Optional sub-parameter (e.g., palette index)
    rb";\?"  # Query marker - the key indicator this is a query
    rb"(?:\x07|\x1b\\)"  # BEL or ST terminator
)

# DA (Device Attributes) primary query
# Format: ESC [ c  or  ESC [ 0 c
_DA_PATTERN = re.compile(rb"\x1b\[0?c")

# DA2 (Secondary Device Attributes) query
# Format: ESC [ > c  or  ESC [ > 0 c
_DA2_PATTERN = re.compile(rb"\x1b\[>0?c")

# DECRQSS (Request Selection or Setting) - various terminal state queries
# Format: ESC P $ q <setting> ST
_DECRQSS_PATTERN = re.compile(
    rb"\x1bP\$q"  # DCS introducer + DECRQSS
    rb"[^\x1b]*"  # Setting identifier
    rb"\x1b\\"  # ST terminator
)

# Pattern to detect incomplete escape sequences at end of a chunk.
# These are potential query sequence prefixes that may complete in next chunk.
# We look for:
#   - \x1b alone (CSI/OSC/DCS start)
#   - \x1b[ followed by optional digits/params but no command char
#   - \x1b] followed by digits but no terminator
#   - \x1bP followed by content but no ST terminator
_INCOMPLETE_ESC_PATTERN = re.compile(
    rb"(?:"
    rb"\x1b$|"  # ESC at end (might be start of any sequence)
    rb"\x1b\[[0-9;>]*$|"  # CSI without command char
    rb"\x1b\][^\x07]*$|"  # OSC without BEL terminator (ST needs \x1b\)
    rb"\x1bP[^\x1b]*$"  # DCS without ST terminator
    rb")"
)


def _filter_complete_queries(output_bytes: bytes) -> bytes:
    """Filter complete terminal query sequences from output bytes."""
    output_bytes = _DSR_PATTERN.sub(b"", output_bytes)
    output_bytes = _OSC_QUERY_PATTERN.sub(b"", output_bytes)
    output_bytes = _DA_PATTERN.sub(b"", output_bytes)
    output_bytes = _DA2_PATTERN.sub(b"", output_bytes)
    output_bytes = _DECRQSS_PATTERN.sub(b"", output_bytes)
    return output_bytes


class TerminalQueryFilter:
    """Stateful filter for terminal query sequences.

    This filter maintains state across calls to handle escape sequences that
    may be split across multiple output chunks (which happens with long-running
    commands surfaced incrementally).

    Usage:
        filter = TerminalQueryFilter()
        filtered1 = filter.filter(chunk1)
        filtered2 = filter.filter(chunk2)
        # ... and so on

        # When command completes, reset for the next command:
        filter.reset()
    """

    def __init__(self) -> None:
        self._pending: bytes = b""

    def reset(self) -> None:
        """Reset filter state between commands."""
        self._pending = b""

    def filter(self, output: str) -> str:
        """Filter terminal query sequences from captured terminal output.

        Removes escape sequences that would cause the terminal to respond
        when the output is displayed, while preserving legitimate formatting
        sequences (colors, cursor movement, etc.).

        This method is stateful: incomplete escape sequences at the end of
        a chunk are held until the next chunk arrives, so split sequences
        are properly detected and filtered.

        Args:
            output: Raw terminal output that may contain query sequences.

        Returns:
            Filtered output with query sequences removed.
        """
        # Convert to bytes for regex matching (escape sequences are byte-level)
        output_bytes = output.encode("utf-8", errors="surrogateescape")

        # Prepend any pending bytes from previous call
        if self._pending:
            output_bytes = self._pending + output_bytes
            self._pending = b""

        # Check for incomplete escape sequence at end
        match = _INCOMPLETE_ESC_PATTERN.search(output_bytes)
        if match:
            # Hold the incomplete sequence for the next chunk
            self._pending = output_bytes[match.start() :]
            output_bytes = output_bytes[: match.start()]

        # Filter complete query sequences
        output_bytes = _filter_complete_queries(output_bytes)

        # Convert back to string
        return output_bytes.decode("utf-8", errors="surrogateescape")

    def flush(self) -> str:
        """Flush any pending bytes that weren't part of a query.

        Call this when output is complete to emit any trailing bytes that
        turned out not to be query sequences.

        Returns:
            Any pending bytes as a string, filtered for queries.
        """
        if not self._pending:
            return ""
        pending = self._pending
        self._pending = b""
        # Filter the pending bytes in case they form a complete query
        filtered = _filter_complete_queries(pending)
        return filtered.decode("utf-8", errors="surrogateescape")


# Singleton instance for simple stateless usage (backward compatibility)
_default_filter = TerminalQueryFilter()


def filter_terminal_queries(output: str) -> str:
    """Filter terminal query sequences from captured terminal output.

    This is a stateless convenience function. For handling incremental output
    where sequences may be split across chunks, use TerminalQueryFilter class.

    Removes escape sequences that would cause the terminal to respond
    when the output is displayed, while preserving legitimate formatting
    sequences (colors, cursor movement, etc.).

    Args:
        output: Raw terminal output that may contain query sequences.

    Returns:
        Filtered output with query sequences removed.
    """
    # Use a fresh filter for stateless behavior
    temp_filter = TerminalQueryFilter()
    result = temp_filter.filter(output)
    # Flush any pending (shouldn't happen for complete input, but be safe)
    result += temp_filter.flush()
    return result
