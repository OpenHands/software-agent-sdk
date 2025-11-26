"""Comprehensive tests for BashEventService bash command execution."""

import asyncio
import tempfile
from pathlib import Path
from typing import Any

import pytest

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.models import BashCommand, BashOutput, ExecuteBashRequest
from openhands.agent_server.pub_sub import Subscriber


@pytest.fixture
def bash_service():
    """Create a BashEventService instance for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        yield BashEventService(
            bash_events_dir=temp_path / "bash_events",
        )


class EventCollector(Subscriber):
    """Test subscriber that collects all events."""

    def __init__(self):
        self.events: list[Any] = []
        self.commands: list[Any] = []
        self.outputs: list[Any] = []

    async def __call__(self, event):
        self.events.append(event)
        if isinstance(event, BashCommand):
            self.commands.append(event)
        elif isinstance(event, BashOutput):
            self.outputs.append(event)


@pytest.mark.asyncio
async def test_single_output_command(bash_service):
    """Test bash command that produces single output."""
    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Simple echo command - should produce single output
    request = ExecuteBashRequest(command='echo "Hello World"', cwd="/tmp")
    command, task = await bash_service.start_bash_command(request)

    # Wait for command to complete
    await task

    # Verify events were published
    assert len(collector.commands) == 1
    assert len(collector.outputs) == 1

    # Verify command event
    cmd_event = collector.commands[0]
    assert cmd_event.id == command.id
    assert cmd_event.command == 'echo "Hello World"'
    assert cmd_event.cwd == "/tmp"

    # Verify output event
    output_event = collector.outputs[0]
    assert output_event.command_id == command.id
    assert output_event.order == 0
    assert output_event.exit_code == 0
    assert output_event.stdout == "Hello World\n"
    assert output_event.stderr is None

    # Verify events can be retrieved from storage
    retrieved_cmd = await bash_service.get_bash_event(command.id.hex)
    assert retrieved_cmd is not None
    assert retrieved_cmd.id == command.id

    retrieved_output = await bash_service.get_bash_event(output_event.id.hex)
    assert retrieved_output is not None
    assert retrieved_output.id == output_event.id


@pytest.mark.asyncio
async def test_multiple_output_command(bash_service):
    """Test bash command that produces multiple pieces of output."""
    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Command that produces multiple lines of output
    request = ExecuteBashRequest(
        command='echo "Line 1"; echo "Line 2"; echo "Line 3"', cwd="/tmp"
    )
    command, task = await bash_service.start_bash_command(request)

    # Wait for command to complete
    await task

    # Verify events were published
    assert len(collector.commands) == 1
    assert len(collector.outputs) >= 1  # May be chunked into multiple outputs

    # Verify command event
    cmd_event = collector.commands[0]
    assert cmd_event.id == command.id
    assert "echo" in cmd_event.command

    # Verify all outputs belong to the same command
    for output in collector.outputs:
        assert output.command_id == command.id
        assert output.exit_code == 0
        assert output.stderr is None

    # Verify outputs are properly ordered
    orders = [output.order for output in collector.outputs]
    assert orders == sorted(orders)

    # Combine all stdout to verify complete output
    combined_stdout = "".join(
        output.stdout or ""
        for output in sorted(collector.outputs, key=lambda x: x.order)
    )
    assert "Line 1" in combined_stdout
    assert "Line 2" in combined_stdout
    assert "Line 3" in combined_stdout


@pytest.mark.asyncio
async def test_command_with_stderr(bash_service):
    """Test bash command that produces stderr output."""
    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Command that writes to stderr
    request = ExecuteBashRequest(
        command='echo "stdout message" && echo "stderr message" >&2', cwd="/tmp"
    )
    command, task = await bash_service.start_bash_command(request)

    # Wait for command to complete
    await task

    # Verify events were published
    assert len(collector.commands) == 1
    assert len(collector.outputs) >= 1

    # Find outputs with stdout and stderr
    stdout_outputs = [o for o in collector.outputs if o.stdout]
    stderr_outputs = [o for o in collector.outputs if o.stderr]

    # Should have both stdout and stderr
    assert len(stdout_outputs) >= 1
    assert len(stderr_outputs) >= 1

    # Verify content
    combined_stdout = "".join(o.stdout or "" for o in stdout_outputs)
    combined_stderr = "".join(o.stderr or "" for o in stderr_outputs)

    assert "stdout message" in combined_stdout
    assert "stderr message" in combined_stderr

    # All outputs should have exit code 0
    for output in collector.outputs:
        assert output.exit_code == 0


@pytest.mark.asyncio
async def test_command_with_error_exit_code(bash_service):
    """Test bash command that exits with error code."""
    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Command that exits with error
    request = ExecuteBashRequest(command="exit 42", cwd="/tmp")
    _, task = await bash_service.start_bash_command(request)

    # Wait for command to complete
    await task

    # Verify events were published
    assert len(collector.commands) == 1
    assert len(collector.outputs) >= 1

    # Verify exit code is propagated
    for output in collector.outputs:
        assert output.exit_code == 42


@pytest.mark.asyncio
async def test_command_timeout(bash_service):
    """Test bash command that times out."""
    import time

    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Command that should timeout (sleep longer than timeout)
    request = ExecuteBashRequest(command="sleep 10", cwd="/tmp", timeout=1)
    start_time = time.time()
    _, task = await bash_service.start_bash_command(request)

    # Wait for timeout to occur
    await task
    end_time = time.time()

    # Verify the command was terminated quickly (within 3 seconds to allow for overhead)
    execution_time = end_time - start_time
    assert execution_time < 3, f"Command took {execution_time:.2f}s, expected < 3s"

    # Verify events were published
    assert len(collector.commands) == 1
    assert len(collector.outputs) >= 1

    # Verify the command was started correctly
    cmd_event = collector.commands[0]
    assert cmd_event.command == "sleep 10"

    # Verify the timeout resulted in exit code -1
    final_output = collector.outputs[-1]  # Last output should have the exit code
    assert final_output.exit_code == -1, (
        f"Expected exit code -1, got {final_output.exit_code}"
    )


@pytest.mark.asyncio
async def test_large_output_chunking(bash_service):
    """Test that large output is properly chunked."""
    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Generate large output using a simple command that should work everywhere
    # Create a string larger than MAX_CONTENT_CHAR_LENGTH (1MB)
    large_size = 1024 * 1024 + 1000  # Slightly over 1MB
    request = ExecuteBashRequest(command=f'yes "x" | head -c {large_size}', cwd="/tmp")
    command, task = await bash_service.start_bash_command(request)

    # Wait for command to complete
    await task

    # Verify events were published
    assert len(collector.commands) == 1
    assert len(collector.outputs) >= 1  # Should be chunked if large enough

    # Verify all chunks belong to same command and are ordered
    for i, output in enumerate(collector.outputs):
        assert output.command_id == command.id
        assert output.order == i
        # Only the final output has exit_code set, intermediate ones may be None
        if i == len(collector.outputs) - 1:
            assert output.exit_code == 0

    # Verify total output size is substantial
    total_stdout = "".join(
        output.stdout or ""
        for output in sorted(collector.outputs, key=lambda x: x.order)
    )
    assert len(total_stdout) > 1000  # Should have substantial output


@pytest.mark.asyncio
async def test_concurrent_commands(bash_service):
    """Test multiple concurrent bash commands."""
    collector = EventCollector()
    await bash_service.subscribe_to_events(collector)

    # Start multiple commands concurrently
    requests = [
        ExecuteBashRequest(command=f'echo "Command {i}"', cwd="/tmp") for i in range(3)
    ]

    # Start all commands
    results = await asyncio.gather(
        *[bash_service.start_bash_command(req) for req in requests]
    )

    # Wait for all to complete
    await asyncio.gather(*[task for _, task in results])

    # Verify all commands were executed
    assert len(collector.commands) == 3
    assert len(collector.outputs) >= 3

    # Verify each command has corresponding outputs
    command_ids = {cmd.id for cmd, _ in results}
    output_command_ids = {output.command_id for output in collector.outputs}
    assert command_ids == output_command_ids


@pytest.mark.asyncio
async def test_event_persistence(bash_service):
    """Test that events are properly persisted to files."""
    # Execute a command
    request = ExecuteBashRequest(command='echo "persistence test"', cwd="/tmp")
    command, task = await bash_service.start_bash_command(request)

    # Wait for completion
    await task

    # Verify command can be retrieved
    retrieved_cmd = await bash_service.get_bash_event(command.id.hex)
    assert retrieved_cmd is not None
    assert retrieved_cmd.command == 'echo "persistence test"'

    # Verify batch retrieval works
    batch_results = await bash_service.batch_get_bash_events([command.id.hex])
    assert len(batch_results) == 1
    assert batch_results[0] is not None
    assert batch_results[0].id == command.id


@pytest.mark.asyncio
async def test_search_bash_events(bash_service):
    """Test searching for bash events."""
    # Execute multiple commands
    requests = [
        ExecuteBashRequest(command='echo "first"', cwd="/tmp"),
        ExecuteBashRequest(command='echo "second"', cwd="/tmp"),
    ]

    results = await asyncio.gather(
        *[bash_service.start_bash_command(req) for req in requests]
    )

    # Wait for completion
    await asyncio.gather(*[task for _, task in results])

    # Search for events
    page = await bash_service.search_bash_events()
    assert len(page.items) >= 4  # At least 2 commands + 2 outputs

    # Verify we can find both commands and outputs
    command_events = [e for e in page.items if isinstance(e, BashCommand)]
    output_events = [e for e in page.items if isinstance(e, BashOutput)]

    assert len(command_events) >= 2
    assert len(output_events) >= 2


@pytest.mark.asyncio
async def test_service_lifecycle(bash_service):
    """Test service lifecycle methods."""
    # Test context manager usage
    async with bash_service:
        request = ExecuteBashRequest(command='echo "lifecycle test"', cwd="/tmp")
        command, task = await bash_service.start_bash_command(request)
        await task

    # Service should be closed after context manager
    # Verify we can still retrieve persisted events
    retrieved = await bash_service.get_bash_event(command.id.hex)
    assert retrieved is not None


@pytest.mark.asyncio
async def test_clear_all_events_empty_storage(bash_service):
    """Test clearing events when storage is empty."""
    # Clear events from empty storage
    count = await bash_service.clear_all_events()
    assert count == 0


@pytest.mark.asyncio
async def test_clear_all_events_with_data(bash_service):
    """Test clearing events when storage contains data."""
    # Execute some commands to create events
    requests = [
        ExecuteBashRequest(command='echo "first"', cwd="/tmp"),
        ExecuteBashRequest(command='echo "second"', cwd="/tmp"),
    ]

    results = await asyncio.gather(
        *[bash_service.start_bash_command(req) for req in requests]
    )

    # Wait for completion
    await asyncio.gather(*[task for _, task in results])

    # Verify events exist before clearing
    page = await bash_service.search_bash_events()
    initial_count = len(page.items)
    assert initial_count > 0  # Should have at least some events

    # Clear all events
    cleared_count = await bash_service.clear_all_events()
    assert cleared_count == initial_count

    # Verify events are gone
    page_after = await bash_service.search_bash_events()
    assert len(page_after.items) == 0

    # Verify individual events cannot be retrieved
    for cmd, _ in results:
        retrieved = await bash_service.get_bash_event(cmd.id.hex)
        assert retrieved is None


@pytest.mark.asyncio
async def test_clear_all_events_partial_failure(bash_service):
    """Test clearing events when some files cannot be deleted."""
    # Execute a command to create an event
    request = ExecuteBashRequest(command='echo "test"', cwd="/tmp")
    command, task = await bash_service.start_bash_command(request)
    await task

    # Verify event exists
    retrieved = await bash_service.get_bash_event(command.id.hex)
    assert retrieved is not None

    # Clear events (should succeed even if some files are problematic)
    cleared_count = await bash_service.clear_all_events()
    assert cleared_count >= 1  # At least the command event should be cleared

    # Verify events are gone
    page = await bash_service.search_bash_events()
    assert len(page.items) == 0


@pytest.mark.asyncio
async def test_search_with_filtering(bash_service):
    """Test searching bash events with kind and command_id filtering."""
    # Execute two commands
    request1 = ExecuteBashRequest(command='echo "first"', cwd="/tmp")
    request2 = ExecuteBashRequest(command='echo "second"', cwd="/tmp")

    command1, task1 = await bash_service.start_bash_command(request1)
    command2, task2 = await bash_service.start_bash_command(request2)

    # Wait for both to complete
    await asyncio.gather(task1, task2)

    # Search for all events - should get 4: 2 commands + 2 outputs
    all_events = await bash_service.search_bash_events()
    assert len(all_events.items) >= 4

    # Filter by kind="BashCommand" - should get only 2 command events
    command_events = await bash_service.search_bash_events(kind__eq="BashCommand")
    assert len(command_events.items) == 2
    for event in command_events.items:
        assert isinstance(event, BashCommand)

    # Filter by kind="BashOutput" - should get only 2 output events
    output_events = await bash_service.search_bash_events(kind__eq="BashOutput")
    assert len(output_events.items) == 2
    for event in output_events.items:
        assert isinstance(event, BashOutput)

    # Filter by command_id - should get only outputs for command1
    command1_outputs = await bash_service.search_bash_events(command_id__eq=command1.id)
    # Should get at least 1 output (could be chunked into multiple)
    assert len(command1_outputs.items) >= 1
    for event in command1_outputs.items:
        if isinstance(event, BashOutput):
            assert event.command_id == command1.id

    # Combine filters: kind="BashOutput" AND command_id=command1.id
    command1_only_outputs = await bash_service.search_bash_events(
        kind__eq="BashOutput", command_id__eq=command1.id
    )
    assert len(command1_only_outputs.items) >= 1
    for event in command1_only_outputs.items:
        assert isinstance(event, BashOutput)
        assert event.command_id == command1.id


@pytest.mark.asyncio
async def test_search_pagination(bash_service):
    """Test pagination in bash event search."""
    # Execute multiple commands to generate enough events
    requests = [
        ExecuteBashRequest(command=f'echo "command{i}"', cwd="/tmp") for i in range(5)
    ]

    results = await asyncio.gather(
        *[bash_service.start_bash_command(req) for req in requests]
    )

    # Wait for all to complete
    await asyncio.gather(*[task for _, task in results])

    # Search with small limit to test pagination
    page1 = await bash_service.search_bash_events(limit=3)
    assert len(page1.items) == 3
    assert page1.next_page_id is not None

    # Get next page
    page2 = await bash_service.search_bash_events(limit=3, page_id=page1.next_page_id)
    assert len(page2.items) > 0

    # Verify items are different between pages
    page1_ids = {event.id for event in page1.items}
    page2_ids = {event.id for event in page2.items}
    assert len(page1_ids.intersection(page2_ids)) == 0  # No overlap
