"""Structured diagnostics for the agent-server execution environment."""

import asyncio
import logging
import os
import platform
import sys
import threading
import traceback
from collections.abc import Callable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from openhands.sdk.logger import ExecutionContextJsonFormatter


_LOGGER_NAME = "openhands.execution_environment"
_TRACKER_LOCK = threading.Lock()
_active_tracker: "ExecutionEnvironmentTracker | None" = None


def _thread_details(thread: threading.Thread) -> dict[str, Any]:
    return {
        "thread_id": thread.ident,
        "native_thread_id": thread.native_id,
        "thread_name": thread.name,
        "thread_class": type(thread).__qualname__,
        "daemon": thread.daemon,
        "alive": thread.is_alive(),
    }


class ExecutionEnvironmentTracker:
    """Write process topology and newly started threads to a dedicated log."""

    def __init__(self, log_dir: str | Path) -> None:
        self.log_path = Path(log_dir) / "execution-env.log"
        self._logger = logging.getLogger(_LOGGER_NAME)
        self._handler: TimedRotatingFileHandler | None = None
        self._original_thread_start: Callable[..., Any] | None = None
        self._wrapped_thread_start: Callable[..., Any] | None = None

    def start(self) -> None:
        global _active_tracker

        with _TRACKER_LOCK:
            if _active_tracker is not None:
                raise RuntimeError("Execution environment tracking is already active")

            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = TimedRotatingFileHandler(
                self.log_path,
                when=os.getenv("LOG_ROTATE_WHEN", "midnight"),
                backupCount=int(os.getenv("LOG_BACKUP_COUNT", "7")),
                encoding="utf-8",
            )
            handler.setLevel(logging.INFO)
            handler.setFormatter(
                ExecutionContextJsonFormatter(
                    fmt="%(asctime)s %(levelname)s %(name)s %(message)s"
                )
            )

            self._logger.setLevel(logging.INFO)
            self._logger.propagate = False
            self._logger.addHandler(handler)
            self._handler = handler

            original_thread_start = threading.Thread.start

            def wrapped_thread_start(thread: threading.Thread) -> None:
                creation_stack = [
                    {
                        "filename": frame.filename,
                        "line_number": frame.lineno,
                        "function": frame.name,
                        "source": frame.line,
                    }
                    for frame in traceback.extract_stack(limit=12)[:-1]
                ]
                original_thread_start(thread)
                self._logger.info(
                    "Thread started: %s",
                    thread.name,
                    extra={
                        "event": "thread_started",
                        "created_thread": _thread_details(thread),
                        "creation_stack": creation_stack,
                        "active_thread_count": threading.active_count(),
                    },
                )

            self._original_thread_start = original_thread_start
            self._wrapped_thread_start = wrapped_thread_start
            setattr(threading.Thread, "start", wrapped_thread_start)
            _active_tracker = self

        self._logger.info(
            "Execution environment tracking started",
            extra={
                "event": "execution_environment_started",
                "parent_process_id": os.getppid(),
                "python_version": platform.python_version(),
                "python_implementation": platform.python_implementation(),
                "python_executable": sys.executable,
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "uvicorn_worker_count": 1,
                "active_threads": [
                    _thread_details(thread) for thread in threading.enumerate()
                ],
            },
        )

    def log_async_environment(self, *, max_concurrent_runs: int) -> None:
        """Record the live event loop and configured conversation executor."""
        loop = asyncio.get_running_loop()
        default_executor = getattr(loop, "_default_executor", None)
        self._logger.info(
            "Async execution environment initialized",
            extra={
                "event": "async_execution_environment_initialized",
                "event_loop_id": id(loop),
                "event_loop_class": type(loop).__qualname__,
                "asyncio_task_count": len(asyncio.all_tasks(loop)),
                "conversation_run_max_workers": max_concurrent_runs,
                "default_executor_initialized": default_executor is not None,
                "default_executor_max_workers": getattr(
                    default_executor, "_max_workers", None
                ),
            },
        )

    def stop(self) -> None:
        global _active_tracker

        with _TRACKER_LOCK:
            if _active_tracker is not self:
                return
            if (
                self._original_thread_start is not None
                and threading.Thread.start is self._wrapped_thread_start
            ):
                setattr(threading.Thread, "start", self._original_thread_start)
            _active_tracker = None

        self._logger.info(
            "Execution environment tracking stopped",
            extra={
                "event": "execution_environment_stopped",
                "active_threads": [
                    _thread_details(thread) for thread in threading.enumerate()
                ],
            },
        )

        if self._handler is not None:
            self._logger.removeHandler(self._handler)
            self._handler.close()
            self._handler = None


def log_async_execution_environment(*, max_concurrent_runs: int) -> None:
    """Add event-loop details when tracking was enabled by the server entry point."""
    tracker = _active_tracker
    if tracker is not None:
        tracker.log_async_environment(max_concurrent_runs=max_concurrent_runs)
