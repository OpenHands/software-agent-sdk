import asyncio
import glob
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from openhands.agent_server.models import (
    BashCommand,
    BashEventBase,
    BashEventPage,
    BashEventSortOrder,
    BashOutput,
    ExecuteBashRequest,
)
from openhands.agent_server.pub_sub import PubSub, Subscriber
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)
MAX_CONTENT_CHAR_LENGTH = 1024 * 1024


@dataclass
class BashEventService:
    """Service for executing bash events which are not added to the event stream and
    will not be visible to the agent."""

    bash_events_dir: Path = field()
    _pub_sub: PubSub[BashEventBase] = field(
        default_factory=lambda: PubSub[BashEventBase](), init=False
    )

    def _ensure_bash_events_dir(self) -> None:
        """Ensure the bash events directory exists."""
        self.bash_events_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp_to_str(self, timestamp: datetime) -> str:
        result = timestamp.strftime("%Y%m%d%H%M%S")
        return result

    def _get_event_filename(self, event: BashEventBase) -> str:
        """Generate filename using YYYYMMDDHHMMSS_eventId_actionId format."""
        result = [self._timestamp_to_str(event.timestamp), event.kind]
        command_id = getattr(event, "command_id", None)
        if command_id:
            result.append(command_id.hex)
        result.append(event.id.hex)
        return "_".join(result)

    def _save_event_to_file(self, event: BashEventBase) -> None:
        """Save an event to a file."""
        self._ensure_bash_events_dir()
        filename = self._get_event_filename(event)
        filepath = self.bash_events_dir / filename

        with open(filepath, "w") as f:
            # Use model_dump with mode='json' to handle UUID serialization
            data = event.model_dump(mode="json")
            f.write(json.dumps(data, indent=2))

    def _load_event_from_file(self, filepath: Path) -> BashEventBase | None:
        """Load an event from a file."""
        try:
            json_data = filepath.read_text()
            return BashEventBase.model_validate_json(json_data)
        except Exception as e:
            logger.error(f"Error loading event from {filepath}: {e}")
            return None

    def _get_event_files_by_pattern(self, pattern: str) -> list[Path]:
        """Get event files matching a glob pattern, sorted by timestamp."""
        self._ensure_bash_events_dir()
        files = glob.glob(str(self.bash_events_dir / pattern))
        return sorted([Path(f) for f in files])

    async def get_bash_event(self, event_id: str) -> BashEventBase | None:
        """Get the event with the id given, or None if there was no such event."""
        # Use glob pattern to find files ending with the event_id
        pattern = f"*_{event_id}"
        files = self._get_event_files_by_pattern(pattern)

        if not files:
            return None

        # Load and return the first matching event
        return self._load_event_from_file(files[0])

    async def batch_get_bash_events(
        self, event_ids: list[str]
    ) -> list[BashEventBase | None]:
        """Given a list of ids, get bash events (Or none for any which were
        not found)"""
        results = await asyncio.gather(
            *[self.get_bash_event(event_id) for event_id in event_ids]
        )
        return results

    async def search_bash_events(
        self,
        kind__eq: str | None = None,
        command_id__eq: UUID | None = None,
        timestamp__gte: datetime | None = None,
        timestamp__lt: datetime | None = None,
        sort_order: BashEventSortOrder = BashEventSortOrder.TIMESTAMP,
        page_id: str | None = None,
        limit: int = 100,
    ) -> BashEventPage:
        """Search for events. If an command_id is given, only the observations for the
        action are returned."""

        # Build the search pattern based on filename format:
        # - BashCommand: <timestamp>_<kind>_<event_id>
        # - BashOutput: <timestamp>_<kind>_<command_id>_<event_id>
        search_parts = ["*"]  # Start with wildcard for timestamp

        if kind__eq:
            search_parts.append(kind__eq)
        else:
            search_parts.append("*")  # Wildcard for kind if not specified

        if command_id__eq:
            search_parts.append(command_id__eq.hex)

        # Always end with wildcard for event_id
        search_parts.append("*")

        search_pattern = "_".join(search_parts)
        files = self._get_event_files_by_pattern(search_pattern)
        files.sort(
            key=lambda f: f.name,
            reverse=(sort_order == BashEventSortOrder.TIMESTAMP_DESC),
        )

        # Timestamp filtering.
        if timestamp__gte:
            timestamp_gte_str = self._timestamp_to_str(timestamp__gte)
            files = [file for file in files if file.name >= timestamp_gte_str]
        if timestamp__lt:
            timestamp_lt_str = self._timestamp_to_str(timestamp__lt)
            files = [file for file in files if file.name < timestamp_lt_str]

        # Handle pagination
        page_files = []
        start_index = 0

        # Find the starting point if page_id is provided
        if page_id:
            for i, file in enumerate(files):
                if str(file.name) == page_id:
                    start_index = i
                    break

        # Collect items for this page
        next_page_id = None
        for i in range(start_index, len(files)):
            if len(page_files) >= limit:
                # We have collected enough items for this page
                # Set next_page_id to the current file for next page
                next_page_id = str(files[i].name)
                break
            page_files.append(files[i])

        # Load only the page files (not all files)
        page_events = []
        for file_path in page_files:
            event = self._load_event_from_file(file_path)
            if event is not None:
                page_events.append(event)

        return BashEventPage(items=page_events, next_page_id=next_page_id)

    async def start_bash_command(
        self, request: ExecuteBashRequest
    ) -> tuple[BashCommand, asyncio.Task]:
        """Execute a bash command. The output will be published separately."""
        command = BashCommand(**request.model_dump())
        self._save_event_to_file(command)
        await self._pub_sub(command)

        # Execute the bash command in a background task
        task = asyncio.create_task(self._execute_bash_command(command))

        return command, task

    async def _execute_bash_command(self, command: BashCommand) -> None:
        """Execute the bash event and create an observation event."""
        try:
            # Create subprocess
            process = await asyncio.create_subprocess_shell(
                command.command,
                cwd=command.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
            )

            # Track output order and buffers
            output_order = 0
            stdout_buffer = ""
            stderr_buffer = ""

            async def read_stream(stream, is_stderr=False):
                nonlocal output_order, stdout_buffer, stderr_buffer

                buffer = stderr_buffer if is_stderr else stdout_buffer

                while True:
                    try:
                        # Read data from stream
                        data = await stream.read(8192)  # Read in chunks
                        if not data:
                            break

                        text = data.decode("utf-8", errors="replace")
                        buffer += text

                        # Update the appropriate buffer
                        if is_stderr:
                            stderr_buffer = buffer
                        else:
                            stdout_buffer = buffer

                        # Check if we need to split the output
                        while len(buffer) > MAX_CONTENT_CHAR_LENGTH:
                            # Split at the max length
                            chunk = buffer[:MAX_CONTENT_CHAR_LENGTH]
                            buffer = buffer[MAX_CONTENT_CHAR_LENGTH:]

                            # Create and publish BashOutput event
                            output_event = BashOutput(
                                command_id=command.id,
                                order=output_order,
                                stdout=chunk if not is_stderr else None,
                                stderr=chunk if is_stderr else None,
                            )

                            self._save_event_to_file(output_event)
                            await self._pub_sub(output_event)
                            output_order += 1

                            # Update the appropriate buffer
                            if is_stderr:
                                stderr_buffer = buffer
                            else:
                                stdout_buffer = buffer

                    except Exception as e:
                        logger.error(f"Error reading from stream: {e}")
                        break

            # Execute the entire command with timeout
            try:
                # Run stream reading and process waiting concurrently with timeout
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout, is_stderr=False),
                        read_stream(process.stderr, is_stderr=True),
                        process.wait(),
                        return_exceptions=True,
                    ),
                    timeout=command.timeout,
                )
                exit_code = process.returncode
            except TimeoutError:
                # Kill the process if it times out
                process.kill()
                try:
                    # Give the process a short time to die gracefully
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except TimeoutError:
                    # If it still won't die, terminate it more forcefully
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1.0)
                    except TimeoutError:
                        logger.error(
                            f"Failed to kill process for command: {command.command}"
                        )
                exit_code = -1
                logger.warning(
                    f"Command timed out after {command.timeout} seconds: "
                    f"{command.command}"
                )

            # Create final output event with any remaining buffer content and exit code
            final_stdout = stdout_buffer if stdout_buffer else None
            final_stderr = stderr_buffer if stderr_buffer else None

            # Only create final event if there's remaining content or we need to report
            # exit code
            if final_stdout or final_stderr or exit_code is not None:
                final_output = BashOutput(
                    command_id=command.id,
                    order=output_order,
                    exit_code=exit_code,
                    stdout=final_stdout,
                    stderr=final_stderr,
                )

                self._save_event_to_file(final_output)
                await self._pub_sub(final_output)

        except Exception as e:
            logger.error(f"Error executing bash command '{command.command}': {e}")
            # Create error output event
            error_output = BashOutput(
                command_id=command.id,
                order=0,
                exit_code=-1,
                stderr=f"Error executing command: {str(e)}",
            )

            self._save_event_to_file(error_output)
            await self._pub_sub(error_output)

    async def subscribe_to_events(self, subscriber: Subscriber[BashEventBase]) -> UUID:
        """Subscribe to bash events.

        The subscriber will receive BashEventBase instances.
        """
        return self._pub_sub.subscribe(subscriber)

    async def unsubscribe_from_events(self, subscriber_id: UUID) -> bool:
        return self._pub_sub.unsubscribe(subscriber_id)

    async def clear_all_events(self) -> int:
        """Clear all bash events from storage.

        Returns:
            int: The number of events that were cleared.
        """
        self._ensure_bash_events_dir()

        # Get all event files
        files = self._get_event_files_by_pattern("*")

        # Count files before deletion
        count = len(files)

        # Remove all event files
        for file_path in files:
            try:
                file_path.unlink()
            except Exception as e:
                logger.error(f"Error deleting event file {file_path}: {e}")

        logger.info(f"Cleared {count} bash events from storage")
        return count

    async def close(self):
        """Close the bash event service and clean up resources."""
        await self._pub_sub.close()

    async def __aenter__(self):
        """Start using this task service"""
        # No special initialization needed for bash event service
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        """Finish using this task service"""
        await self.close()


_bash_event_service: BashEventService | None = None


def get_default_bash_event_service() -> BashEventService:
    """Get the default bash event service instance."""
    global _bash_event_service
    if _bash_event_service:
        return _bash_event_service

    from openhands.agent_server.config import get_default_config

    config = get_default_config()
    _bash_event_service = BashEventService(bash_events_dir=config.bash_events_dir)
    return _bash_event_service
