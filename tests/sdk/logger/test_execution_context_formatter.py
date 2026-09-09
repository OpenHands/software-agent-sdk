import asyncio
import json
import logging

from openhands.sdk.logger import ExecutionContextJsonFormatter


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        "test",
        logging.INFO,
        "test.py",
        1,
        message,
        (),
        None,
    )


def test_execution_context_formatter_adds_process_and_thread_fields():
    formatter = ExecutionContextJsonFormatter("%(message)s")

    data = json.loads(formatter.format(_record("sync")))

    assert isinstance(data["process_id"], int)
    assert data["process_name"] == "MainProcess"
    assert isinstance(data["thread_id"], int)
    assert data["thread_name"] == "MainThread"
    assert "task_id" not in data
    assert "task_name" not in data


async def test_execution_context_formatter_adds_asyncio_task_fields():
    formatter = ExecutionContextJsonFormatter("%(message)s")
    task = asyncio.current_task()
    assert task is not None

    data = json.loads(formatter.format(_record("async")))

    assert data["task_id"] == id(task)
    assert data["task_name"] == task.get_name()


async def test_execution_context_formatter_identifies_worker_thread():
    formatter = ExecutionContextJsonFormatter("%(message)s")

    data = await asyncio.to_thread(
        lambda: json.loads(formatter.format(_record("worker")))
    )

    assert data["thread_name"].startswith("asyncio_")
    assert "task_id" not in data
    assert "task_name" not in data
