"""Tests for ExecuteBashObservation truncation functionality."""

from openhands.sdk.llm import TextContent
from openhands.tools.execute_bash.constants import MAX_CMD_OUTPUT_SIZE
from openhands.tools.execute_bash.definition import ExecuteBashObservation
from openhands.tools.execute_bash.metadata import CmdOutputMetadata


def test_execute_bash_observation_truncation_under_limit():
    """Test ExecuteBashObservation doesn't truncate when under limit."""
    metadata = CmdOutputMetadata(
        prefix="",
        suffix="",
        working_dir="/tmp",
        py_interpreter_path="/usr/bin/python",
        exit_code=0,
        pid=123,
    )

    observation = ExecuteBashObservation(
        output="Short output",
        metadata=metadata,
        error=False,
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


def test_execute_bash_observation_truncation_over_limit():
    """Test ExecuteBashObservation truncates when over limit."""
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

    observation = ExecuteBashObservation(
        output=long_output,
        metadata=metadata,
        error=False,
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


def test_execute_bash_observation_truncation_with_error():
    """Test ExecuteBashObservation truncates with error prefix."""
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

    observation = ExecuteBashObservation(
        output=long_output,
        metadata=metadata,
        error=True,
    )

    result = observation.to_llm_content
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    result = result[0].text

    # The result should be truncated and have error prefix
    assert result.startswith("[There was an error during command execution.]")
    assert len(result) < len(long_output) + 300  # Account for metadata and error prefix
    # With head-and-tail truncation, should end with original content + metadata
    expected_end = (
        "B\n[Current working directory: /tmp]\n[Python interpreter: /usr/bin/python]\n"
        "[Command finished with exit code 1]"
    )
    assert result.endswith(expected_end)
    assert "<response clipped>" in result  # Should contain truncation notice


def test_execute_bash_observation_truncation_exact_limit():
    """Test ExecuteBashObservation doesn't truncate when exactly at limit."""
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

    observation = ExecuteBashObservation(
        output=exact_output,
        metadata=metadata,
        error=False,
    )

    result = observation.to_llm_content
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    result = result[0].text

    # Should not be truncated
    assert len(result) == MAX_CMD_OUTPUT_SIZE
    assert not result.endswith("</NOTE>")


def test_execute_bash_observation_truncation_with_prefix_suffix():
    """Test ExecuteBashObservation truncates with prefix and suffix."""
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

    observation = ExecuteBashObservation(
        output=long_output,
        metadata=metadata,
        error=False,
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
