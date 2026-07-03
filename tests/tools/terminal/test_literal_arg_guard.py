"""SDK-5: guard against Python/JSON literals passed to the terminal tool.

Motivation: in real eval runs (e.g. Nemotron 550B on SWE-Bench), the model
sometimes packs structured arguments — a settings dict, a list of installed
apps, a code blob — into the single ``command`` field. The shell then echoes
``bash: [{default:: command not found`` and the model burns turns retrying
similar garbage. This guard catches the literal pre-execution and replies
with an actionable hint instead.
"""

import pytest

from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
    looks_like_python_literal_argument,
)
from openhands.tools.terminal.impl import TerminalExecutor


# --------------------------------------------------------------------------
# Unit tests for the heuristic.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        # Real malformed examples extracted from a SWE-Bench Nemotron run.
        ("[['col1', 'col2'], ['col1', 'col2']]", "nested list literal"),
        (
            "[{'default': {'ENGINE': 'django.db.backends.sqlite3'}}, "
            "['django.contrib.auth']]",
            "list literal",
        ),
        ('["field.column]\\n\\nwith connection.schema_editor() ..."]', "list literal"),
        # Synthetic variants.
        ('{"key": "value"}', "dict literal"),
        ("{'key': 'value'}", "dict literal"),
        # Leading whitespace must be stripped before classification.
        ("   [{'a': 1}]", "list literal"),
        ('\t["x"]', "list literal"),
    ],
)
def test_python_literals_are_detected(command: str, expected: str) -> None:
    assert looks_like_python_literal_argument(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        # Legitimate bash tests must NOT trip the guard.
        "[ -f /tmp/foo ]",
        "[ -d /workspace ]",
        '[[ "$x" = "y" ]]',
        "[[ -z $VAR ]]",
        # Bash group commands.
        "{ ls; echo done; }",
        # Normal commands.
        "ls -la",
        "python -c 'print(1)'",
        # Even commands whose arguments happen to be JSON-shaped are fine,
        # because the COMMAND name is plain text.
        "echo '[1, 2, 3]'",
        "curl -d '{\"a\": 1}' http://x",
        # Edge: empty / one-char strings should never trip.
        "",
        "[",
        "{",
        " ",
    ],
)
def test_legitimate_commands_are_not_flagged(command: str) -> None:
    assert looks_like_python_literal_argument(command) is None


# --------------------------------------------------------------------------
# Executor-level integration: literal => structured error, no shell.
# --------------------------------------------------------------------------


_SHELL_SENTINEL = "Executor should not reach the shell when the command is rejected"


@pytest.fixture
def executor_without_shell() -> TerminalExecutor:
    """Build a TerminalExecutor without touching the real shell.

    We bypass ``__init__`` (which spins up a real tmux/subprocess session) and
    stub both shell-execution paths to raise. The literal guard runs *before*
    those paths, so an unrelated command must escape the guard and trigger the
    sentinel — that's how we prove the guard didn't fire.
    """
    exe = TerminalExecutor.__new__(TerminalExecutor)
    exe._pool = None

    def _reach_shell(*_args: object, **_kwargs: object) -> TerminalObservation:
        raise AssertionError(_SHELL_SENTINEL)

    exe._execute_pooled = _reach_shell  # type: ignore[method-assign]
    exe._execute_single_session = _reach_shell  # type: ignore[method-assign]
    return exe


def test_literal_command_returns_structured_error_without_shell(
    executor_without_shell: TerminalExecutor,
) -> None:
    action = TerminalAction(command="[{'default': {'ENGINE': 'sqlite3'}}]")
    obs = executor_without_shell(action)

    assert isinstance(obs, TerminalObservation)
    assert obs.is_error is True
    assert obs.exit_code is None
    assert obs.command == action.command
    text = obs.text
    assert "list literal" in text
    # The hint must teach a concrete recovery path.
    assert "file_editor" in text
    assert "heredoc" in text or "<<'EOF'" in text


def test_bash_test_expression_not_blocked(
    executor_without_shell: TerminalExecutor,
) -> None:
    """A real bash `[ -f ... ]` must reach the shell, not the literal guard."""
    action = TerminalAction(command="[ -f /tmp/foo ]")
    with pytest.raises(AssertionError, match=_SHELL_SENTINEL):
        executor_without_shell(action)


def test_is_input_path_skips_guard(
    executor_without_shell: TerminalExecutor,
) -> None:
    """``is_input=True`` forwards raw bytes to a running process. Keystrokes
    like ``C-c`` or arbitrary literals are valid in that context and must
    bypass the guard."""
    action = TerminalAction(command="[{'sent_as_keystrokes': True}]", is_input=True)
    with pytest.raises(AssertionError, match=_SHELL_SENTINEL):
        executor_without_shell(action)


def test_guard_warning_is_logged(
    executor_without_shell: TerminalExecutor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Operators should be able to grep for the guard firing in eval logs."""
    import logging

    caplog.set_level(logging.WARNING, logger="openhands.tools.terminal.impl")
    action = TerminalAction(command='["some code that should have been a script"]')
    executor_without_shell(action)
    assert any(
        "Rejected terminal call" in rec.message and "list literal" in rec.message
        for rec in caplog.records
    )
