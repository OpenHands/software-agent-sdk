"""Round-trip tests for the shared tree-sitter-bash parse entry point.

These tests pin the contract that downstream consumers
(``openhands-tools.terminal.utils.command`` and the planned
security analyzers) rely on: ``parse`` returns a ``ParseResult``
whose ``tree`` can be walked with the standard tree-sitter API and
whose ``has_error`` flag faithfully reflects ``tree.root_node.has_error``.
"""

from dataclasses import FrozenInstanceError

import pytest

from openhands.sdk.security.shell_parser import ParseResult, parse


class TestParseSuccessShape:
    """A successful parse produces a usable Tree and ``has_error=False``."""

    def test_returns_parse_result(self):
        result = parse("ls -l")
        assert isinstance(result, ParseResult)

    def test_root_node_is_walkable(self):
        result = parse("echo hello && ls -l")
        # Standard tree-sitter API surface must be reachable.
        assert result.tree.root_node.type == "program"
        assert len(result.tree.root_node.named_children) >= 1

    def test_has_error_false_on_valid_input(self):
        result = parse("for i in 1 2 3; do echo $i; done")
        assert result.has_error is False


class TestParseErrorRecovery:
    """tree-sitter recovers via ERROR nodes; it must never raise."""

    @pytest.mark.parametrize(
        "command",
        [
            'echo "unclosed quote',
            "echo 'unclosed quote",
            "cat <<EOF\nunclosed heredoc",
            "echo `unclosed backtick",
        ],
        ids=["double_quote", "single_quote", "heredoc", "backtick"],
    )
    def test_malformed_input_does_not_raise(self, command: str):
        result = parse(command)
        assert isinstance(result, ParseResult)

    def test_has_error_true_on_malformed_input(self):
        result = parse('echo "unclosed quote')
        assert result.has_error is True


class TestParseEdgeCases:
    """Empty and unicode inputs round-trip without surprise."""

    def test_empty_string_parses(self):
        result = parse("")
        assert isinstance(result, ParseResult)
        assert result.has_error is False

    def test_utf8_input_parses(self):
        # Multi-byte chars in argument position should not derail parsing.
        result = parse("echo 'héllo wörld'")
        assert result.has_error is False


class TestParseResultIsImmutable:
    """``ParseResult`` is frozen so consumers cannot mutate shared state."""

    def test_frozen(self):
        result = parse("ls")
        with pytest.raises(FrozenInstanceError):
            result.has_error = True  # type: ignore[misc]


class TestParseAdversarialBytes:
    """Pin the contract for unusual byte sequences.

    These tests document the boundary between encoding (which can raise)
    and parsing (which never raises and reports errors via ERROR nodes).
    """

    def test_emoji_argument_parses(self):
        # 4-byte UTF-8 in argument position must not derail the parser.
        result = parse("echo 🚀")
        assert result.has_error is False

    def test_embedded_null_byte_parses_with_error_flag(self):
        # NUL bytes parse (no exception) but the grammar flags them.
        result = parse("echo \x00 hello")
        assert isinstance(result, ParseResult)
        assert result.has_error is True

    def test_lone_surrogate_raises_unicode_encode_error(self):
        # Strict UTF-8 rejects lone surrogates at encode time, before
        # tree-sitter sees the input. The docstring documents this.
        with pytest.raises(UnicodeEncodeError):
            parse("echo \ud800 hello")


class TestParseByteSemantics:
    """Pin byte-offset semantics so a future library upgrade can't drift."""

    def test_root_node_spans_full_input(self):
        command = "echo hello"
        result = parse(command)
        assert result.tree.root_node.start_byte == 0
        assert result.tree.root_node.end_byte == len(command.encode())

    def test_root_node_spans_multibyte_input(self):
        # Multi-byte chars: end_byte counts bytes, not code points.
        command = "echo 'héllo'"
        result = parse(command)
        assert result.tree.root_node.end_byte == len(command.encode())


class TestParseDeterminism:
    """Repeated parses of the same input return equal results.

    Guards against any global mutable state slipping in via parser
    construction or the shared ``_BASH_LANGUAGE`` singleton.
    """

    def test_repeated_parse_has_equal_error_flag(self):
        first = parse("echo hello && ls")
        second = parse("echo hello && ls")
        assert first.has_error == second.has_error

    def test_repeated_parse_of_malformed_input_is_stable(self):
        first = parse('echo "unclosed')
        second = parse('echo "unclosed')
        assert first.has_error is True
        assert second.has_error is True
