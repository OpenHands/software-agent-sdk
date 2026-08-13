import asyncio
import glob
import json
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
from openhands.sdk.utils import sanitized_env, utc_now


logger = get_logger(__name__)
MAX_CONTENT_CHAR_LENGTH = 1024 * 1024


@dataclass
class BashEventService:
    """Service for executing bash events which are not added to the event stream and
    will not be visible to the agent."""

    bash_events_dir: Path = field()
    _pub_sub: PubSub[BashEventBase] = field(
        default_factory=lambda: PubSub[BashEventBase](max_subscribers=50),
        init=False,
    )
    # Multiple open terminal tabs poll bash events concurrently. Moving each
    # directory scan to the default thread pool keeps it off the event loop, but
    # unbounded concurrent scans can still occupy every worker and starve other
    # endpoints that use asyncio.to_thread (notably conversations/search).
    # Serialize only the expensive bash-event filesystem work: queued callers
    # remain asynchronous while one worker performs the shared-directory scan.
    # 1 (full serialization) is an intentional strict bound: a single scan is
    # cheap (~0.2s on 100k files), so overlapping even a few scans saves little
    # wall time but, at scale, risks monopolizing the shared worker pool for the
    # sake of marginal poll latency; queued callers still proceed asynchronously.
    _filesystem_search_semaphore: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(1),
        init=False,
        repr=False,
    )

    def _ensure_bash_events_dir(self) -> None:
        """Ensure the bash events directory exists."""
        self.bash_events_dir.mkdir(parents=True, exist_ok=True)

    def _timestamp_to_str(self, timestamp: datetime) -> str:
        # Include microseconds so filename-based ordering reflects emission
        # order for sub-second bursts (e.g. fast `yes`-style floods that
        # emit several BashOutput chunks in the same wall-clock second).
        return timestamp.strftime("%Y%m%d%H%M%S%f")

    def _get_event_filename(self, event: BashEventBase) -> str:
        """Generate filename using YYYYMMDDHHMMSSffffff_eventId_actionId format."""
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
        # Blocking I/O — dispatched off the event loop.
        async with self._filesystem_search_semaphore:
            return await asyncio.to_thread(self._get_bash_event_sync, event_id)

    def _get_bash_event_sync(self, event_id: str) -> BashEventBase | None:
        """Sync: find the event file whose name ends with ``_<event_id>``.

        Scans the events directory in a single ``os.scandir`` pass (one readdir,
        no per-entry stat or regex) and returns the first matching file.
        """
        self._ensure_bash_events_dir()
        suffix = f"_{event_id}"
        with os.scandir(self.bash_events_dir) as it:
            for entry in it:
                if entry.name.endswith(suffix):
                    return self._load_event_from_file(Path(entry.path))
        return None

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
        order__gt: int | None = None,
        sort_order: BashEventSortOrder = BashEventSortOrder.TIMESTAMP,
        page_id: str | None = None,
        limit: int = 100,
    ) -> BashEventPage:
        """Search for events. If a command_id is given, only the observations for
        the action are returned.

        The directory scan, sort, filter and file reads are all blocking I/O,
        so the whole search runs off the asyncio event loop in a worker thread
        (``asyncio.to_thread``) to avoid stalling concurrent requests.
        """
        # Blocking I/O — dispatched off the event loop.
        async with self._filesystem_search_semaphore:
            return await asyncio.to_thread(
                self._search_bash_events_sync,
                kind__eq,
                command_id__eq,
                timestamp__gte,
                timestamp__lt,
                order__gt,
                sort_order,
                page_id,
                limit,
            )

    def _search_bash_events_sync(
        self,
        kind__eq: str | None,
        command_id__eq: UUID | None,
        timestamp__gte: datetime | None,
        timestamp__lt: datetime | None,
        order__gt: int | None,
        sort_order: BashEventSortOrder,
        page_id: str | None,
        limit: int,
    ) -> BashEventPage:
        """Sync search: one ``os.scandir`` pass + cheap segment filter.

        Splits each filename on ``_`` and compares the fixed segments
        (kind, command_id) directly rather than matching with regexes. Filenames
        are ``<timestamp>_<kind>[_<command_id>]_<event_id>`` with a 20-digit
        timestamp prefix, so a lexicographic name comparison is a timestamp
        comparison and is used both to pre-filter by the time window and to sort.
        """
        self._ensure_bash_events_dir()
        gte_str = self._timestamp_to_str(timestamp__gte) if timestamp__gte else None
        lt_str = self._timestamp_to_str(timestamp__lt) if timestamp__lt else None
        kind_filter = kind__eq
        cmd_filter = command_id__eq.hex if command_id__eq else None
        reverse = sort_order == BashEventSortOrder.TIMESTAMP_DESC

        matched: list[str] = []
        with os.scandir(self.bash_events_dir) as it:
            for entry in it:
                name = entry.name
                # Timestamp-prefix pre-filter: filenames sort lexicographically
                # by timestamp, so a string compare on the whole name bounds
                # the time window without parsing the timestamp.
                if gte_str is not None and name < gte_str:
                    continue
                if lt_str is not None and name >= lt_str:
                    continue
                # Segment filter: [timestamp, kind, (command_id), event_id].
                # Cheaper than fnmatch and avoids matching unrelated files.
                if kind_filter is not None or cmd_filter is not None:
                    parts = name.split("_")
                    if kind_filter is not None:
                        if len(parts) < 2 or parts[1] != kind_filter:
                            continue
                    if cmd_filter is not None:
                        # Only BashOutput (4 segments) carries a command_id.
                        if len(parts) < 4 or parts[2] != cmd_filter:
                            continue
                matched.append(name)

        matched.sort(reverse=reverse)

        # Resolve page_id to a starting index.
        start_index = 0
        if page_id:
            for i, name in enumerate(matched):
                if name == page_id:
                    start_index = i
                    break

        page_slice = matched[start_index : start_index + limit]
        next_page_id = None
        if start_index + limit < len(matched):
            next_page_id = matched[start_index + limit]

        page_events: list[BashEventBase] = []
        for name in page_slice:
            event = self._load_event_from_file(self.bash_events_dir / name)
            if event is None:
                continue
            # Filter by order if specified (only applies to BashOutput events)
            if order__gt is not None:
                event_order = getattr(event, "order", None)
                if event_order is not None and event_order <= order__gt:
                    continue
            page_events.append(event)

        return BashEventPage(items=page_events, next_page_id=next_page_id)

    def _signal_process_group(
        self,
        process: asyncio.subprocess.Process,
        sig: signal.Signals,
    ) -> None:
        try:
            os.killpg(os.getpgid(process.pid), sig)
        except ProcessLookupError:
            pass
        except OSError as e:
            logger.debug(
                "Failed to send %s to process group (error_type=%s)",
                sig.name,
                type(e).__name__,
            )

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
            # Create subprocess in a new session so we can signal the whole
            # process group on teardown (the shell's children, e.g. sleep, must
            # die before the shell can run user-installed traps).
            process = await asyncio.create_subprocess_shell(
                command.command,
                cwd=command.cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                shell=True,
                env=sanitized_env(),
                start_new_session=True,
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
                # Send SIGTERM to the whole process group so user-installed
                # cleanup traps can run, then escalate to SIGKILL if needed.
                self._signal_process_group(process, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except TimeoutError:
                    self._signal_process_group(process, signal.SIGKILL)
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1.0)
                    except TimeoutError:
                        logger.error(
                            "Failed to kill process (command_id=%s)", command.id
                        )
                exit_code = -1
                logger.warning(
                    "Command timed out after %s seconds (command_id=%s)",
                    command.timeout,
                    command.id,
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
            logger.error(
                "Error executing bash command (command_id=%s, error_type=%s)",
                command.id,
                type(e).__name__,
            )
            # Create error output event
            error_output = BashOutput(
                command_id=command.id,
                order=0,
                exit_code=-1,
                stderr=f"Error executing command: {str(e)}",
            )

            self._save_event_to_file(error_output)
            await self._pub_sub(error_output)

    def delete_events_older_than(self, cutoff: datetime) -> int:
        """Delete bash event files with a recorded timestamp older than ``cutoff``.

        This is a synchronous method — all operations are blocking filesystem
        I/O. Callers on the asyncio event loop should use
        ``await asyncio.to_thread(service.delete_events_older_than, cutoff)``
        to avoid stalling the loop.

        File names are prefixed with ``YYYYMMDDHHMMSS`` in ascending sort order,
        so scanning stops as soon as a file at or after the cutoff is reached.

        Returns:
            int: The number of event files deleted.
        """
        cutoff_str = self._timestamp_to_str(cutoff)
        files = self._get_event_files_by_pattern("*")  # ascending chronological order
        count = 0
        for path in files:
            if path.name >= cutoff_str:
                break  # remaining files are at or newer than cutoff
            try:
                path.unlink(missing_ok=True)
                count += 1
            except Exception as e:
                logger.warning("Failed to delete bash event file %s: %s", path, e)
        if count:
            logger.info(
                "Deleted %d bash event file(s) older than %s", count, cutoff_str
            )
        return count

    async def run_retention_cleanup_loop(
        self,
        retention_seconds: int,
        interval_seconds: float | None = None,
    ) -> None:
        """Periodically purge bash event files older than ``retention_seconds``.

        Runs until cancelled (e.g. during application shutdown). Cleanup runs
        immediately on entry so that files accumulated across a server restart
        are purged without waiting for the first interval to elapse.

        Blocking filesystem work is dispatched to a thread via
        ``asyncio.to_thread`` to keep the event loop free.

        Args:
            retention_seconds: Age threshold in seconds; older files are deleted.
            interval_seconds: How often to run the cleanup. Defaults to
                ``max(60, retention_seconds / 2)``. Pass a smaller value in
                tests to avoid long waits.
        """
        interval = (
            interval_seconds
            if interval_seconds is not None
            else max(60.0, retention_seconds / 2)
        )
        while True:
            try:
                cutoff = utc_now() - timedelta(seconds=retention_seconds)
                await asyncio.to_thread(self.delete_events_older_than, cutoff)
            except Exception as e:
                logger.warning("Bash events retention cleanup error: %s", e)
                # Brief back-off to prevent log flooding if the failure is persistent
                # (e.g. permission error, full disk). Cap at the normal interval so
                # we don't over-delay in low-retention configurations.
                await asyncio.sleep(min(interval, 60.0))
            # Always sleep the full interval after the error back-off, so total
            # wait on error = min(interval, 60) + interval ≈ 2× normal cadence.
            await asyncio.sleep(interval)

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
