"""
Tests for bash session functionality across all terminal implementations.

This test suite uses pytest parametrization to run the same tests against all
available terminal implementations (subprocess, tmux, powershell) to ensure
consistent behavior across different backends.

The tests automatically detect which terminal types are available on the system
and run the parametrized tests for each one.
"""

import os
import tempfile
import time

import pytest

from openhands.sdk import TextContent
from openhands.sdk.logger import get_logger
from openhands.tools.terminal.definition import (
    TerminalAction,
    TerminalObservation,
)
from openhands.tools.terminal.terminal import (
    TerminalCommandStatus,
    create_terminal_session,
)

from .conftest import get_no_change_timeout_suffix


logger = get_logger(__name__)

# Parametrize tests to run on all available terminal types
terminal_types = ["tmux", "subprocess"]
parametrize_terminal_types = pytest.mark.parametrize("terminal_type", terminal_types)


@parametrize_terminal_types
def test_session_initialization(terminal_type):
    # Test with custom working directory
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        obs = session.execute(TerminalAction(command="pwd"))

        assert temp_dir in obs.text
        assert "[The command completed with exit code 0.]" in obs.metadata.suffix
        session.close()

    # Test with custom username
    session = create_terminal_session(
        work_dir=os.getcwd(), username="nobody", terminal_type=terminal_type
    )
    session.initialize()
    session.close()


@parametrize_terminal_types
def test_cwd_property(tmp_path, terminal_type):
    session = create_terminal_session(work_dir=tmp_path, terminal_type=terminal_type)
    session.initialize()
    # Change directory and verify pwd updates
    random_dir = tmp_path / "random"
    random_dir.mkdir()
    session.execute(TerminalAction(command=f"cd {random_dir}"))

    # For other implementations, just verify the command executed successfully
    obs = session.execute(TerminalAction(command="pwd"))
    assert str(random_dir) in obs.text

    # Note: CWD tracking may vary between terminal implementations
    # For tmux, it should track properly. For subprocess, it may not.
    # if terminal_type == "tmux":
    assert session.cwd == str(random_dir)
    # else:
    session.close()


@parametrize_terminal_types
def test_basic_command(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Test simple command
    obs = session.execute(TerminalAction(command="echo 'hello world'"))

    assert "hello world" in obs.text
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"
    # Note: prefix may vary between terminal implementations
    assert obs.metadata.exit_code == 0
    assert session.prev_status == TerminalCommandStatus.COMPLETED

    # Test command with error
    obs = session.execute(TerminalAction(command="nonexistent_command"))

    # Note: Exit code handling may vary between terminal implementations
    # The important thing is that the error message is captured
    assert "nonexistent_command: command not found" in obs.text
    assert session.prev_status == TerminalCommandStatus.COMPLETED

    # Test multiple commands in sequence
    obs = session.execute(
        TerminalAction(command='echo "first" && echo "second" && echo "third"')
    )
    assert "first" in obs.text
    assert "second" in obs.text
    assert "third" in obs.text
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"
    # Note: prefix may vary between terminal implementations
    assert obs.metadata.exit_code == 0
    assert session.prev_status == TerminalCommandStatus.COMPLETED

    session.close()


@parametrize_terminal_types
def test_session_truncates_large_command_output(monkeypatch, terminal_type):
    # Keep this test fast by temporarily lowering the max truncation size.
    # (Avoid generating 30k+ output in unit tests.)
    small_max = 600

    from openhands.tools.terminal.terminal import (
        terminal_session as terminal_session_mod,
    )

    monkeypatch.setattr(terminal_session_mod, "MAX_CMD_OUTPUT_SIZE", small_max)

    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Single-line output that exceeds our patched MAX.
    obs = session.execute(TerminalAction(command="python3 -c 'print(\"A\" * 5000)'"))

    assert "<response clipped>" in obs.text
    assert len(obs.text) <= small_max

    session.close()


@parametrize_terminal_types
def test_session_truncates_multiline_output(monkeypatch, terminal_type):
    """Ensure session-level truncation handles large multi-line outputs safely.

    This specifically exercises newline-heavy output to catch regressions where
    truncation might split/strip lines unexpectedly or behave differently than
    single-line output.
    """

    small_max = 600

    from openhands.tools.terminal.terminal import (
        terminal_session as terminal_session_mod,
    )

    monkeypatch.setattr(terminal_session_mod, "MAX_CMD_OUTPUT_SIZE", small_max)

    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Multi-line output that exceeds our patched MAX.
    # Use printf to generate many short lines, exercising newline boundaries.
    obs = session.execute(
        TerminalAction(command="bash -lc \"printf 'A\\n%.0s' {1..5000}\"")
    )

    assert "<response clipped>" in obs.text
    assert len(obs.text) <= small_max

    # Some backends may include terminal control sequences (e.g. bracketed paste).
    # Ensure we still get newline-separated output and truncation doesn't break it.
    assert "A\n" in obs.text
    assert obs.text.count("\n") > 10

    session.close()


@parametrize_terminal_types
def test_truncation_preserves_metadata_in_llm_content(monkeypatch, terminal_type):
    # Ensure that when we truncate the final formatted text for the LLM,
    # the metadata suffix remains visible.
    from openhands.sdk.utils.truncate import DEFAULT_TRUNCATE_NOTICE
    from openhands.tools.terminal import definition as terminal_definition_mod

    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    obs = session.execute(TerminalAction(command="python3 -c 'print(\"A\" * 5000)'"))

    assert "exit code 0" in obs.metadata.suffix

    trailing = obs.metadata.suffix
    if obs.metadata.working_dir:
        trailing += f"\n[Current working directory: {obs.metadata.working_dir}]"
    if obs.metadata.py_interpreter_path:
        trailing += f"\n[Python interpreter: {obs.metadata.py_interpreter_path}]"
    if obs.metadata.exit_code != -1:
        trailing += f"\n[Command finished with exit code {obs.metadata.exit_code}]"

    # Pick a small truncation budget but ensure the tail is large enough to include
    # the full suffix + trailing lines across environments (path lengths vary).
    min_tail = len(trailing) + 10
    small_max = len(DEFAULT_TRUNCATE_NOTICE) + 2 * min_tail

    monkeypatch.setattr(terminal_definition_mod, "MAX_CMD_OUTPUT_SIZE", small_max)

    llm_content = obs.to_llm_content
    assert isinstance(llm_content[0], TextContent)
    llm_text = llm_content[0].text

    assert "<response clipped>" in llm_text
    assert "[The command completed with exit code 0.]" in llm_text

    session.close()


@parametrize_terminal_types
def test_environment_variable_persistence(terminal_type):
    """Test that environment variables persist across commands (stateful terminal)."""
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Set an environment variable
    obs = session.execute(TerminalAction(command="export TEST_VAR='hello world'"))
    assert obs.metadata.exit_code == 0

    # Use the environment variable in a subsequent command
    obs = session.execute(TerminalAction(command="echo $TEST_VAR"))
    assert "hello world" in obs.text
    assert obs.metadata.exit_code == 0

    session.close()


@parametrize_terminal_types
def test_environment_variable_inheritance_from_parent(terminal_type):
    """Test that environment variables from parent process are inherited."""
    # Set an environment variable in the current process
    test_var_name = "OPENHANDS_TEST_INHERITANCE_VAR"
    test_var_value = "inherited_from_parent_12345"
    original_value = os.environ.get(test_var_name)

    try:
        # Set the environment variable in the parent process
        os.environ[test_var_name] = test_var_value

        # Create a new terminal session
        session = create_terminal_session(
            work_dir=os.getcwd(), terminal_type=terminal_type
        )
        session.initialize()

        # Check if the environment variable is available in the terminal
        obs = session.execute(TerminalAction(command=f"echo ${test_var_name}"))
        assert test_var_value in obs.text, (
            f"Expected '{test_var_value}' in output, but got: {obs.text}"
        )
        assert obs.metadata.exit_code == 0

        session.close()

    finally:
        # Clean up: restore original environment variable value
        if original_value is not None:
            os.environ[test_var_name] = original_value
        else:
            os.environ.pop(test_var_name, None)


@pytest.mark.timeout(60)  # Add 60 second timeout to prevent hanging in CI
def test_long_running_command_follow_by_execute():
    session = create_terminal_session(work_dir=os.getcwd(), no_change_timeout_seconds=2)
    session.initialize()

    # Test command that produces output slowly
    obs = session.execute(
        TerminalAction(command="echo 1; sleep 3; echo 2; sleep 3; echo 3")
    )

    assert "1" in obs.text  # First number should appear before timeout
    assert obs.metadata.exit_code == -1  # -1 indicates command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
    assert obs.metadata.suffix == get_no_change_timeout_suffix(2)
    assert obs.metadata.prefix == ""

    # Continue watching output
    obs = session.execute(TerminalAction(command="", is_input=True))

    assert "2" in obs.text
    assert obs.metadata.prefix == "[Below is the output of the previous command.]\n"
    assert obs.metadata.suffix == get_no_change_timeout_suffix(2)
    assert obs.metadata.exit_code == -1  # -1 indicates command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

    # Test command that produces no output
    obs = session.execute(TerminalAction(command="sleep 15"))

    assert "3" not in obs.text
    assert obs.metadata.prefix == "[Below is the output of the previous command.]\n"
    assert "The previous command is still running" in obs.metadata.suffix
    assert obs.metadata.exit_code == -1  # -1 indicates command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

    time.sleep(3)

    # Run it again, this time it should produce output and then start a new command
    obs = session.execute(TerminalAction(command="sleep 15"))

    assert "3" in obs.text  # Should see the final output from the previous command
    assert obs.metadata.exit_code == -1  # -1 indicates new command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

    session.close()


@parametrize_terminal_types
@pytest.mark.timeout(60)  # Add 60 second timeout to prevent hanging in CI
def test_interactive_command(terminal_type):
    session = create_terminal_session(
        work_dir=os.getcwd(), no_change_timeout_seconds=3, terminal_type=terminal_type
    )
    session.initialize()

    # Test interactive command with blocking=True
    obs = session.execute(
        TerminalAction(
            command="read -p 'Enter name: ' name && echo \"Hello $name\"",
        )
    )

    assert "Enter name:" in obs.text
    assert obs.metadata.exit_code == -1  # -1 indicates command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
    assert obs.metadata.suffix == get_no_change_timeout_suffix(3)
    assert obs.metadata.prefix == ""

    # Send input
    obs = session.execute(TerminalAction(command="John", is_input=True))

    assert "Hello John" in obs.text
    assert obs.metadata.exit_code == 0
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"
    assert obs.metadata.prefix == ""
    assert session.prev_status == TerminalCommandStatus.COMPLETED

    # Test multiline command input
    obs = session.execute(TerminalAction(command="cat << EOF"))

    assert obs.metadata.exit_code == -1
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
    assert obs.metadata.suffix == get_no_change_timeout_suffix(3)
    assert obs.metadata.prefix == ""

    obs = session.execute(TerminalAction(command="line 1", is_input=True))

    assert obs.metadata.exit_code == -1
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
    assert obs.metadata.suffix == get_no_change_timeout_suffix(3)
    assert obs.metadata.prefix == "[Below is the output of the previous command.]\n"

    obs = session.execute(TerminalAction(command="line 2", is_input=True))

    assert obs.metadata.exit_code == -1
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
    assert obs.metadata.suffix == get_no_change_timeout_suffix(3)
    assert obs.metadata.prefix == "[Below is the output of the previous command.]\n"

    obs = session.execute(TerminalAction(command="EOF", is_input=True))

    assert "line 1" in obs.text and "line 2" in obs.text
    assert obs.metadata.exit_code == 0
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"
    assert obs.metadata.prefix == ""

    session.close()


@parametrize_terminal_types
@pytest.mark.timeout(60)  # Add 60 second timeout to prevent hanging in CI
def test_ctrl_c(terminal_type):
    session = create_terminal_session(
        work_dir=os.getcwd(), no_change_timeout_seconds=2, terminal_type=terminal_type
    )
    session.initialize()

    # Start infinite loop
    obs = session.execute(
        TerminalAction(command="while true; do echo 'looping'; sleep 3; done"),
    )

    assert "looping" in obs.text
    assert obs.metadata.suffix == get_no_change_timeout_suffix(2)
    assert obs.metadata.prefix == ""
    assert obs.metadata.exit_code == -1  # -1 indicates command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

    # Send Ctrl+C
    obs = session.execute(TerminalAction(command="C-c", is_input=True))

    # Check that the process was interrupted (exit code can be 1 or 130
    # depending on the shell/OS)
    assert obs.metadata.exit_code in (
        1,
        130,
    )  # Accept both common exit codes for interrupted processes
    assert "CTRL+C was sent" in obs.metadata.suffix
    assert obs.metadata.prefix == ""
    assert session.prev_status == TerminalCommandStatus.COMPLETED

    session.close()


@parametrize_terminal_types
def test_empty_command_error(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Test empty command without previous command
    obs = session.execute(TerminalAction(command=""))

    assert obs.is_error is True
    assert obs.text == "No previous running command to retrieve logs from."
    assert len(obs.to_llm_content) == 2
    assert isinstance(obs.to_llm_content[0], TextContent)
    assert obs.to_llm_content[0].text == TerminalObservation.ERROR_MESSAGE_HEADER
    assert isinstance(obs.to_llm_content[1], TextContent)
    assert (
        "No previous running command to retrieve logs from."
        == obs.to_llm_content[1].text
    )
    assert obs.metadata.exit_code == -1
    assert obs.metadata.prefix == ""
    assert obs.metadata.suffix == ""
    assert session.prev_status is None

    session.close()


@parametrize_terminal_types
@pytest.mark.timeout(60)  # Add 60 second timeout to prevent hanging in CI
def test_command_output_continuation(terminal_type):
    """Test that we can continue to get output from a long-running command.

    This test has been modified to be more robust against timing issues.
    """
    session = create_terminal_session(
        work_dir=os.getcwd(), no_change_timeout_seconds=1, terminal_type=terminal_type
    )
    session.initialize()

    # Start a command that produces output slowly but with longer sleep time
    # to ensure we hit the timeout
    obs = session.execute(
        TerminalAction(command="for i in {1..5}; do echo $i; sleep 2; done")
    )

    # Check if the command completed immediately or timed out
    if session.prev_status == TerminalCommandStatus.COMPLETED:
        # If the command completed immediately, verify we got all the output
        logger.info("Command completed immediately", extra={"msg_type": "TEST_INFO"})
        assert "1" in obs.text
        assert "2" in obs.text
        assert "3" in obs.text
        assert "4" in obs.text
        assert "5" in obs.text
        assert "[The command completed with exit code 0.]" in obs.metadata.suffix
    else:
        # If the command timed out, verify we got the timeout message
        assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT
        assert "1" in obs.text
        assert "[The command has no new output after 1 seconds." in obs.metadata.suffix

        # Continue getting output until we see all numbers
        numbers_seen = set()
        for i in range(1, 6):
            if str(i) in obs.text:
                numbers_seen.add(i)

        # We need to see numbers 2-5 and then the command completion
        while (
            len(numbers_seen) < 5
            or session.prev_status != TerminalCommandStatus.COMPLETED
        ):
            obs = session.execute(TerminalAction(command="", is_input=True))

            # Check for numbers in the output
            for i in range(1, 6):
                if str(i) in obs.text and i not in numbers_seen:
                    numbers_seen.add(i)
                    logger.info(
                        f"Found number {i} in output", extra={"msg_type": "TEST_INFO"}
                    )

            # Check if the command has completed
            if session.prev_status == TerminalCommandStatus.COMPLETED:
                assert (
                    "[The command completed with exit code 0.]" in obs.metadata.suffix
                )
                break
            else:
                assert (
                    "[The command has no new output after 1 seconds."
                    in obs.metadata.suffix
                )
                assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

        # Verify we've seen all numbers
        assert numbers_seen == {1, 2, 3, 4, 5}, (
            f"Expected to see numbers 1-5, but saw {numbers_seen}"
        )

        # Verify the command completed
        assert session.prev_status == TerminalCommandStatus.COMPLETED

    session.close()


@parametrize_terminal_types
def test_history_expansion_disabled(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    obs = session.execute(TerminalAction(command="echo A!B"))
    assert "event not found" not in obs.text
    assert "A!B" in obs.text

    session.close()


@parametrize_terminal_types
def test_long_output(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Generate a long output that may exceed buffer size
    obs = session.execute(
        TerminalAction(command='for i in {1..5000}; do echo "Line $i"; done')
    )

    assert "Line 1" in obs.text
    assert "Line 5000" in obs.text
    assert obs.metadata.exit_code == 0
    assert obs.metadata.prefix == ""
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"

    session.close()


@parametrize_terminal_types
def test_long_output_exceed_history_limit(terminal_type):
    session = create_terminal_session(work_dir=os.getcwd(), terminal_type=terminal_type)
    session.initialize()

    # Generate a long output that may exceed buffer size
    obs = session.execute(
        TerminalAction(command='for i in {1..50000}; do echo "Line $i"; done')
    )

    assert "Previous command outputs are truncated" in obs.metadata.prefix
    assert "Line 40000" in obs.text
    assert "Line 50000" in obs.text
    assert obs.metadata.exit_code == 0
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"

    session.close()


def test_multiline_command():
    session = create_terminal_session(work_dir=os.getcwd())
    session.initialize()

    # Test multiline command with PS2 prompt disabled
    obs = session.execute(
        TerminalAction(
            command="""if true; then
echo "inside if"
fi""",
        )
    )

    assert "inside if" in obs.text
    assert obs.metadata.exit_code == 0
    assert obs.metadata.prefix == ""
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"

    session.close()


@parametrize_terminal_types
def test_python_interactive_input(terminal_type):
    session = create_terminal_session(
        work_dir=os.getcwd(), no_change_timeout_seconds=2, terminal_type=terminal_type
    )
    session.initialize()

    # Test Python program that asks for input - properly escaped for bash
    python_script = (
        "name = input('Enter your name: '); age = input('Enter your age: '); "
        "print(f'Hello {name}, you are {age} years old')"
    )

    # Start Python with the interactive script
    obs = session.execute(TerminalAction(command=f'python3 -c "{python_script}"'))

    assert "Enter your name:" in obs.text
    assert obs.metadata.exit_code == -1  # -1 indicates command is still running
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

    # Send first input (name)
    obs = session.execute(TerminalAction(command="Alice", is_input=True))

    assert "Enter your age:" in obs.text
    assert obs.metadata.exit_code == -1
    assert session.prev_status == TerminalCommandStatus.NO_CHANGE_TIMEOUT

    # Send second input (age)
    obs = session.execute(TerminalAction(command="25", is_input=True))

    assert "Hello Alice, you are 25 years old" in obs.text
    assert obs.metadata.exit_code == 0
    assert obs.metadata.suffix == "\n[The command completed with exit code 0.]"
    assert session.prev_status == TerminalCommandStatus.COMPLETED

    session.close()


def _run_bash_action(session, command: str, **kwargs):
    """Helper function to execute a bash command and return the observation."""
    action = TerminalAction(command=command, **kwargs)
    obs = session.execute(action)
    logger.info(f"Command: {command}")
    output_text = obs.text if obs.content else ""
    logger.info(f"Output: {output_text}")
    logger.info(f"Exit code: {obs.metadata.exit_code}")
    return obs


@parametrize_terminal_types
def test_bash_server(terminal_type):
    """Test running a server with timeout and interrupt."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Use python -u for unbuffered output, potentially helping
            # capture initial output on Windows
            obs = _run_bash_action(
                session, "python -u -m http.server 8081", timeout=1.0
            )
            assert obs.metadata.exit_code == -1
            assert "Serving HTTP on" in obs.text

            # Send Ctrl+C to interrupt
            obs = _run_bash_action(session, "C-c", is_input=True)
            assert "CTRL+C was sent" in obs.metadata.suffix
            assert "Keyboard interrupt received, exiting." in obs.text

            # Verify we can run commands after interrupt
            obs = _run_bash_action(session, "ls")
            assert obs.metadata.exit_code == 0

            # Run server again to verify it works
            obs = _run_bash_action(
                session, "python -u -m http.server 8081", timeout=1.0
            )
            assert obs.metadata.exit_code == -1
            assert "Serving HTTP on" in obs.text

        finally:
            session.close()


@parametrize_terminal_types
def test_bash_background_server(terminal_type):
    """Test running a server in background."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        server_port = 8081
        try:
            # Start the server in background
            obs = _run_bash_action(session, f"python3 -m http.server {server_port} &")
            assert obs.metadata.exit_code == 0

            # Give the server a moment to be ready
            time.sleep(1)

            # Verify the server is running by curling it
            obs = _run_bash_action(session, f"curl http://localhost:{server_port}")
            assert obs.metadata.exit_code == 0
            # Check for content typical of python http.server directory listing
            assert "Directory listing for" in obs.text

            # Kill the server
            obs = _run_bash_action(session, 'pkill -f "http.server"')
            assert obs.metadata.exit_code == 0

        finally:
            session.close()


@parametrize_terminal_types
def test_multiline_commands(terminal_type):
    """Test multiline command execution."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Original Linux bash version
            # single multiline command
            obs = _run_bash_action(session, 'echo \\\n -e "foo"')
            assert obs.metadata.exit_code == 0
            assert "foo" in obs.text

            # test multiline echo
            obs = _run_bash_action(session, 'echo -e "hello\nworld"')
            assert obs.metadata.exit_code == 0
            assert "hello\nworld" in obs.text

            # test whitespace
            obs = _run_bash_action(session, 'echo -e "a\\n\\n\\nz"')
            assert obs.metadata.exit_code == 0
            assert "\n\n\n" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_complex_commands(terminal_type):
    """Test complex bash command execution."""
    cmd = (
        'count=0; tries=0; while [ $count -lt 3 ]; do result=$(echo "Heads"); '
        'tries=$((tries+1)); echo "Flip $tries: $result"; '
        'if [ "$result" = "Heads" ]; then count=$((count+1)); else count=0; fi; '
        'done; echo "Got 3 heads in a row after $tries flips!";'
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            obs = _run_bash_action(session, cmd)
            assert obs.metadata.exit_code == 0
            assert "Got 3 heads in a row after 3 flips!" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_no_ps2_in_output(terminal_type):
    """Test that the PS2 sign is not added to the output of a multiline command."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            obs = _run_bash_action(session, 'echo -e "hello\nworld"')
            assert obs.metadata.exit_code == 0

            assert "hello\nworld" in obs.text
            assert ">" not in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_multiline_command_loop(terminal_type):
    """Test multiline command with loops."""
    # https://github.com/OpenHands/OpenHands/issues/3143
    init_cmd = """mkdir -p _modules && \\
for month in {01..04}; do
    for day in {01..05}; do
        touch "_modules/2024-${month}-${day}-sample.md"
    done
done && echo "created files"
"""
    follow_up_cmd = """for file in _modules/*.md; do
    new_date=$(echo $file | sed -E \\
        's/2024-(01|02|03|04)-/2024-/;s/2024-01/2024-08/;s/2024-02/2024-09/;s/2024-03/2024-10/;s/2024-04/2024-11/')
    mv "$file" "$new_date"
done && echo "success"
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            obs = _run_bash_action(session, init_cmd)
            assert obs.metadata.exit_code == 0
            assert "created files" in obs.text

            obs = _run_bash_action(session, follow_up_cmd)
            assert obs.metadata.exit_code == 0
            assert "success" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_multiple_multiline_commands(terminal_type):
    """Test that multiple commands separated by newlines are rejected."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            cmds = [
                "ls -l",
                'echo -e "hello\nworld"',
                """echo -e "hello it's me\"""",
                """echo \\
-e 'hello' \\
world""",
                """echo -e 'hello\\nworld\\nare\\nyou\\nthere?'""",
                """echo -e 'hello\nworld\nare\nyou\n\nthere?'""",
                """echo -e 'hello\nworld "'""",
            ]
            joined_cmds = "\n".join(cmds)

            # First test that running multiple commands at once fails
            obs = _run_bash_action(session, joined_cmds)
            assert obs.is_error is True
            assert "Cannot execute multiple commands at once" in obs.text

            # Now run each command individually and verify they work
            results = []
            for cmd in cmds:
                obs = _run_bash_action(session, cmd)
                assert obs.metadata.exit_code == 0
                results.append(obs.text)

            # Verify all expected outputs are present
            assert "total 0" in results[0]  # ls -l
            assert "hello\nworld" in results[1]  # echo -e "hello\nworld"
            assert "hello it's me" in results[2]  # echo -e "hello it\'s me"
            assert "hello world" in results[3]  # echo -e 'hello' world
            assert (
                "hello\nworld\nare\nyou\nthere?" in results[4]
            )  # echo -e 'hello\nworld\nare\nyou\nthere?'
            assert (
                "hello\nworld\nare\nyou\n\nthere?" in results[5]
            )  # echo -e with literal newlines
            assert 'hello\nworld "' in results[6]  # echo -e with quote
        finally:
            session.close()


@parametrize_terminal_types
def test_cmd_run(terminal_type):
    """Test basic command execution."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Unix version
            obs = _run_bash_action(session, f"ls -l {temp_dir}")
            assert obs.metadata.exit_code == 0

            obs = _run_bash_action(session, "ls -l")
            assert obs.metadata.exit_code == 0
            assert "total 0" in obs.text

            obs = _run_bash_action(session, "mkdir test")
            assert obs.metadata.exit_code == 0

            obs = _run_bash_action(session, "ls -l")
            assert obs.metadata.exit_code == 0
            assert "test" in obs.text

            obs = _run_bash_action(session, "touch test/foo.txt")
            assert obs.metadata.exit_code == 0

            obs = _run_bash_action(session, "ls -l test")
            assert obs.metadata.exit_code == 0
            assert "foo.txt" in obs.text

            # clean up
            _run_bash_action(session, "rm -rf test")
            assert obs.metadata.exit_code == 0
        finally:
            session.close()


@parametrize_terminal_types
def test_run_as_user_correct_home_dir(terminal_type):
    """Test that home directory is correct."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Original Linux version
            obs = _run_bash_action(session, "cd ~ && pwd")
            assert obs.metadata.exit_code == 0
            home = os.getenv("HOME")
            assert home and home in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_multi_cmd_run_in_single_line(terminal_type):
    """Test multiple commands in a single line."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(work_dir=temp_dir)
        session.initialize()
        try:
            # Original Linux version using &&
            obs = _run_bash_action(session, "pwd && ls -l")
            assert obs.metadata.exit_code == 0
            assert temp_dir in obs.text
            assert "total 0" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_stateful_cmd(terminal_type):
    """Test that commands maintain state across executions."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Original Linux version
            obs = _run_bash_action(session, "mkdir -p test")
            assert obs.metadata.exit_code == 0

            obs = _run_bash_action(session, "cd test")
            assert obs.metadata.exit_code == 0

            obs = _run_bash_action(session, "pwd")
            assert obs.metadata.exit_code == 0
            assert f"{temp_dir}/test" in obs.text.strip()
        finally:
            session.close()


@parametrize_terminal_types
def test_failed_cmd(terminal_type):
    """Test failed command execution."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            obs = _run_bash_action(session, "non_existing_command")
            assert obs.metadata.exit_code != 0
        finally:
            session.close()


@parametrize_terminal_types
def test_python_version(terminal_type):
    """Test Python version command."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            obs = _run_bash_action(session, "python --version")
            assert obs.metadata.exit_code == 0
            assert "Python 3" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_pwd_property(terminal_type):
    """Test pwd property updates."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Create a subdirectory and verify pwd updates
            obs = _run_bash_action(session, "mkdir -p random_dir")
            assert obs.metadata.exit_code == 0

            obs = _run_bash_action(session, "cd random_dir && pwd")
            assert obs.metadata.exit_code == 0
            assert "random_dir" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
@pytest.mark.timeout(180)  # Add 3 minute timeout for this intensive test
def test_long_output_from_nested_directories(terminal_type):
    """Test long output from nested directory operations."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Create nested directories with many files
            setup_cmd = (
                "mkdir -p /tmp/test_dir && cd /tmp/test_dir && "
                'for i in $(seq 1 100); do mkdir -p "folder_$i"; '
                'for j in $(seq 1 100); do touch "folder_$i/file_$j.txt"; done; done'
            )
            obs = _run_bash_action(session, setup_cmd.strip(), timeout=60)
            assert obs.metadata.exit_code == 0

            # List the directory structure recursively
            obs = _run_bash_action(session, "ls -R /tmp/test_dir", timeout=60)
            assert obs.metadata.exit_code == 0

            # Verify output contains expected files
            assert "folder_1" in obs.text
            assert "file_1.txt" in obs.text
            assert "folder_100" in obs.text
            assert "file_100.txt" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_command_backslash(terminal_type):
    """Test command with backslash escaping."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # Create a file with the content "implemented_function"
            cmd = (
                "mkdir -p /tmp/test_dir && "
                'echo "implemented_function" > /tmp/test_dir/file_1.txt'
            )
            obs = _run_bash_action(session, cmd)
            assert obs.metadata.exit_code == 0

            # Different escaping for different terminal types
            if terminal_type == "subprocess":
                semicolon = '";"'  # No escaping needed for subprocess
            else:
                semicolon = "\\;"  # Escape for tmux

            cmd = (
                "find /tmp/test_dir -type f -exec grep"
                + f' -l "implemented_function" {{}} {semicolon}'
            )
            obs = _run_bash_action(session, cmd)
            assert obs.metadata.exit_code == 0
            assert "/tmp/test_dir/file_1.txt" in obs.text
        finally:
            session.close()


@parametrize_terminal_types
def test_bash_remove_prefix(terminal_type):
    """Test bash command prefix removal."""
    with tempfile.TemporaryDirectory() as temp_dir:
        session = create_terminal_session(
            work_dir=temp_dir, terminal_type=terminal_type
        )
        session.initialize()
        try:
            # create a git repo - same for both platforms
            obs = _run_bash_action(
                session,
                "git init && git remote add origin https://github.com/OpenHands/OpenHands",
            )
            assert obs.metadata.exit_code == 0

            # Check git remote - same for both platforms
            obs = _run_bash_action(session, "git remote -v")
            assert obs.metadata.exit_code == 0
            assert "https://github.com/OpenHands/OpenHands" in obs.text
            assert "git remote -v" not in obs.text
        finally:
            session.close()
