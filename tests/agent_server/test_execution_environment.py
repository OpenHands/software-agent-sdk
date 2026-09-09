import asyncio
import json
import threading

from openhands.agent_server.execution_environment import (
    ExecutionEnvironmentTracker,
)


def _events(log_path):
    return [json.loads(line) for line in log_path.read_text().splitlines()]


async def test_execution_environment_logs_startup_async_context_and_threads(tmp_path):
    tracker = ExecutionEnvironmentTracker(tmp_path)
    original_thread_start = threading.Thread.start
    tracker.start()
    try:
        tracker.log_async_environment(max_concurrent_runs=3)
        await asyncio.to_thread(lambda: None)
    finally:
        tracker.stop()

    assert threading.Thread.start is original_thread_start
    events = _events(tmp_path / "execution-env.log")
    event_names = [event["event"] for event in events]
    assert event_names[0] == "execution_environment_started"
    assert "async_execution_environment_initialized" in event_names
    assert "thread_started" in event_names
    assert event_names[-1] == "execution_environment_stopped"

    async_event = next(
        event
        for event in events
        if event["event"] == "async_execution_environment_initialized"
    )
    assert async_event["conversation_run_max_workers"] == 3
    assert isinstance(async_event["event_loop_id"], int)

    thread_event = next(event for event in events if event["event"] == "thread_started")
    assert thread_event["created_thread"]["thread_name"].startswith("asyncio_")
    assert isinstance(thread_event["created_thread"]["thread_id"], int)
    assert thread_event["creation_stack"]
    assert all("filename" in frame for frame in thread_event["creation_stack"])
