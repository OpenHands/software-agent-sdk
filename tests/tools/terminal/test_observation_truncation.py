"""Tests for TerminalObservation truncation functionality."""

from openhands.sdk.llm import TextContent
from openhands.tools.terminal.constants import MAX_CMD_OUTPUT_SIZE
from openhands.tools.terminal.definition import TerminalObservation
from openhands.tools.terminal.metadata import CmdOutputMetadata


def test_terminal_observation_truncation_under_limit():
    """Test TerminalObservation doesn't truncate when under limit."""
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    observation = TerminalObservation(
        command="echo test",
        content=[TextContent(text="Short output")],
        metadata=metadata,
    )

    result = observation.to_llm_content
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    result = result[0].text

    expected = (
        "Short output\n"
        "[Current working directory: /tmp]\n"
        "[Python interpreter: /usr/bin/python]\n"
        "[Command finished with exit code 0]"
    )
    assert result == expected


def test_terminal_observation_truncation_over_limit():
    """Test TerminalObservation truncates when over limit."""
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    # Create output that exceeds the limit
    long_output = "A" * (MAX_CMD_OUTPUT_SIZE + 1000)

    observation = TerminalObservation(
        command="echo test",
        content=[TextContent(text=long_output)],
        metadata=metadata,
    )

    result = observation.to_llm_content
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    result = result[0].text

    # The result should be truncated
    assert len(result) < len(long_output) + 200  # Account for metadata
    # With head-and-tail truncation, should start and end with original content
    assert result.startswith("A")  # Should start with original content
    expected_end = (
        "A\n[Current working directory: /tmp]\n[Python interpreter: /usr/bin/python]\n"
        "[Command finished with exit code 0]"
    )
    assert result.endswith(expected_end)  # Should end with original content + metadata
    assert "<response clipped>" in result  # Should contain truncation notice


def test_terminal_observation_truncates_content_on_init():
    """Ensure TerminalObservation content itself is truncated before persistence.

    This is distinct from to_llm_content truncation: we want to avoid persisting
    huge command outputs in the event stream at all.
    """
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    long_output = "X" * (MAX_CMD_OUTPUT_SIZE + 1000)

    observation = TerminalObservation(
        command="echo test",
        content=[TextContent(text=long_output)],
        metadata=metadata,
    )

    # Stored content should be truncated.
    assert len(observation.text) <= MAX_CMD_OUTPUT_SIZE
    assert "<response clipped>" in observation.text


def test_terminal_observation_saves_full_output_once(tmp_path):
    """Full output should be saved once when truncation happens at storage layer."""
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    long_output = "Y" * (MAX_CMD_OUTPUT_SIZE + 1000)

    observation = TerminalObservation(
        command="echo test",
        content=[TextContent(text=long_output)],
        metadata=metadata,
        full_output_save_dir=str(tmp_path),
    )

    # Stored content should be truncated.
    assert len(observation.text) <= MAX_CMD_OUTPUT_SIZE
    assert "<response clipped>" in observation.text

    saved_files = list(tmp_path.glob("bash_output_*.txt"))
    assert len(saved_files) == 1
    assert saved_files[0].read_text() == long_output

    # Calling to_llm_content should not create another file.
    _ = observation.to_llm_content
    saved_files_after = list(tmp_path.glob("bash_output_*.txt"))
    assert len(saved_files_after) == 1


def test_terminal_observation_truncation_with_error():
    """Test TerminalObservation truncates with error prefix."""
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=1,
        pid=123,
    )

    # Create output that exceeds the limit
    long_output = "B" * (MAX_CMD_OUTPUT_SIZE + 500)

    observation = TerminalObservation(
        command="false",
        content=[TextContent(text=long_output)],
        metadata=metadata,
        is_error=True,
    )

    result = observation.to_llm_content
    assert len(result) == 2
    assert isinstance(result[0], TextContent)
    assert result[0].text == TerminalObservation.ERROR_MESSAGE_HEADER

    assert isinstance(result[1], TextContent)
    result = result[1].text

    # The result should be truncated
    assert len(result) < len(long_output) + 300  # Account for metadata and error prefix
    # With head-and-tail truncation, should end with original content + metadata
    expected_end = (
        "B\n[Current working directory: /tmp]\n[Python interpreter: /usr/bin/python]\n"
        "[Command finished with exit code 1]"
    )
    assert result.endswith(expected_end)
    assert "<response clipped>" in result  # Should contain truncation notice


def test_terminal_observation_truncation_exact_limit():
    """Test TerminalObservation doesn't truncate when exactly at limit."""
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    # Calculate exact size to hit the limit after adding metadata
    metadata_text = (
        "\n[Current working directory: /tmp]\n"
        "[Python interpreter: /usr/bin/python]\n"
        "[Command finished with exit code 0]"
    )
    exact_output_size = MAX_CMD_OUTPUT_SIZE - len(metadata_text)
    exact_output = "C" * exact_output_size

    observation = TerminalObservation(
        command="echo test",
        content=[TextContent(text=exact_output)],
        metadata=metadata,
    )

    result = observation.to_llm_content
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    result = result[0].text

    # Should not be truncated
    assert len(result) == MAX_CMD_OUTPUT_SIZE
    assert not result.endswith("</NOTE>")


def test_terminal_observation_truncation_with_prefix_suffix():
    """Test TerminalObservation truncates with prefix and suffix."""
    metadata = CmdOutputMetadata(
        prefix="[PREFIX] ",
        suffix=" [SUFFIX]",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    # Create output that exceeds the limit
    long_output = "D" * (MAX_CMD_OUTPUT_SIZE + 200)

    observation = TerminalObservation(
        command="echo test",
        content=[TextContent(text=long_output)],
        metadata=metadata,
    )

    result = observation.to_llm_content
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    result = result[0].text

    # The result should be truncated and include prefix/suffix
    assert result.startswith("[PREFIX] ")
    assert (
        len(result) < len(long_output) + 300
    )  # Account for metadata and prefix/suffix
    # With head-and-tail truncation, should end with original content + metadata
    expected_end = (
        "D [SUFFIX]\n[Current working directory: /tmp]\n"
        "[Python interpreter: /usr/bin/python]\n[Command finished with exit code 0]"
    )
    assert result.endswith(expected_end)
    assert "<response clipped>" in result  # Should contain truncation notice
