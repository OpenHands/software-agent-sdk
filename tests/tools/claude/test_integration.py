"""Integration tests for DelegationManager with real LocalConversation.

These tests use real LocalConversation instances (with real persistence)
and only mock the LLM calls via ``litellm_completion``.  They verify that
conversations are fully persisted to disk (base_state.json, events) and
that the DelegationManager can retrieve task results after eviction.
"""

import json
import threading
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from litellm import ChatCompletionMessageToolCall
from litellm.types.utils import (
    Choices,
    Function,
    Message as LiteLLMMessage,
    ModelResponse,
    Usage,
)
from pydantic import SecretStr

from openhands.sdk import LocalConversation
from openhands.sdk.agent import Agent
from openhands.sdk.llm import LLM
from openhands.tools.claude.impl import (
    DelegationManager,
    TaskState,
    TaskStatus,
)
from openhands.tools.delegate.registration import AgentFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_finish_response(
    message: str = "Task completed!",
    response_id: str = "resp-finish",
) -> ModelResponse:
    """Build a ``ModelResponse`` that contains a single finish tool call."""
    tool_call = ChatCompletionMessageToolCall(
        id="finish_1",
        type="function",
        function=Function(
            name="finish",
            arguments=json.dumps({"message": message}),
        ),
    )
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                message=LiteLLMMessage(
                    role="assistant",
                    content=f"Finishing: {message}",
                    tool_calls=[tool_call],
                ),
                finish_reason="tool_calls",
                index=0,
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _make_text_response(
    text: str = "Done.",
    response_id: str = "resp-text",
) -> ModelResponse:
    """Build a plain text ``ModelResponse`` (no tool calls)."""
    return ModelResponse(
        id=response_id,
        choices=[
            Choices(
                message=LiteLLMMessage(role="assistant", content=text),
                finish_reason="stop",
                index=0,
            )
        ],
        created=0,
        model="test-model",
        object="chat.completion",
        usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _simple_agent_factory(llm: LLM) -> Agent:
    """Create a lightweight agent with no tools (only built-in finish)."""
    return Agent(llm=llm, tools=[])


_TEST_FACTORY = AgentFactory(
    factory_func=_simple_agent_factory,
    description="test agent",
)


@pytest.fixture()
def parent_conversation(tmp_path: Path) -> LocalConversation:
    """Create a real parent ``LocalConversation`` with a mocked LLM."""
    llm = LLM(
        model="gpt-4o-mini",
        api_key=SecretStr("test-key"),
        usage_id="test-parent-llm",
    )
    agent = Agent(llm=llm, tools=[])
    return LocalConversation(
        agent=agent,
        workspace=str(tmp_path / "workspace"),
        persistence_dir=str(tmp_path / "parent_state"),
        visualizer=None,
    )


@pytest.fixture()
def manager() -> Generator[DelegationManager, None, None]:
    mgr = DelegationManager(max_tasks=5)
    yield mgr
    mgr.close()


class TestRealConversationPersistence:
    """Integration tests: real conversations, mocked LLM, real disk I/O."""

    def test_sync_task_persists_conversation_state(
        self,
        parent_conversation: LocalConversation,
        manager: DelegationManager,
        tmp_path: Path,
    ):
        """A synchronous task using a real child conversation should
        persist both task_state.json and the conversation's base_state.json
        + events to disk."""
        manager._ensure_parent(parent_conversation)

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=_make_finish_response("sync answer"),
            ),
        ):
            task = manager.start_task(
                prompt="Do something useful",
                conversation=parent_conversation,
                run_in_background=False,
            )

        # -- Task completed successfully ---------------------------------
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result == "sync answer"

        # -- Task was evicted from active to inactive --------------------
        assert task.id not in manager._active_tasks
        assert task.id in manager._inactive_tasks

        # -- task_state.json written by the manager ----------------------
        task_dir = manager._get_task_directory(task.id)
        task_state_path = task_dir / "task_state.json"
        assert task_state_path.exists(), f"Expected {task_state_path}"

        loaded_task = TaskState.load_from_disk(task_dir / "task_state")
        assert loaded_task is not None
        assert loaded_task.id == task.id
        assert loaded_task.status == TaskStatus.SUCCEEDED
        assert loaded_task.result == "sync answer"

        # -- base_state.json written by the real LocalConversation -------
        # The conversation persistence lives under manager._tmp_dir/<uuid.hex>/
        # Find it by scanning for base_state.json.
        base_state_files = list(manager._tmp_dir.rglob("base_state.json"))
        assert len(base_state_files) >= 1, (
            f"Expected base_state.json under {manager._tmp_dir}, "
            f"found: {list(manager._tmp_dir.rglob('*'))}"
        )

        # Verify the base_state.json is valid JSON with workspace info
        base_data = json.loads(base_state_files[0].read_text())
        assert "workspace" in base_data
        assert "working_dir" in base_data["workspace"]

        # -- Event files persisted by the real conversation ---------------
        event_files = list(manager._tmp_dir.rglob("event-*.json"))
        assert len(event_files) >= 2, (
            f"Expected at least 2 events (user message + agent action), "
            f"found {len(event_files)}"
        )

        # -- get_task_output loads from disk transparently ---------------
        output = manager.get_task_output(task.id)
        assert output.status == TaskStatus.SUCCEEDED
        assert output.result == "sync answer"

    def test_sync_task_events_contain_user_message_and_finish(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """After a sync task finishes, the persisted events should contain
        the user message, the agent's finish action, and the finish observation."""
        manager._ensure_parent(parent_conversation)

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=_make_finish_response("integration answer"),
            ),
        ):
            _ = manager.start_task(
                prompt="Check events",
                conversation=parent_conversation,
                run_in_background=False,
            )

        # The real conversation stored events; the child conversation
        # is no longer reachable from Python (evicted), but event files
        # are on disk.  Let's verify the types via the event json files.
        event_files = sorted(manager._tmp_dir.rglob("event-*.json"))
        assert len(event_files) >= 2

        event_kinds = set()
        for ef in event_files:
            data = json.loads(ef.read_text())
            event_kinds.add(data.get("kind"))

        # Must have a user message and at least one action event
        assert "MessageEvent" in event_kinds
        assert "ActionEvent" in event_kinds

    def test_sync_task_plain_text_response(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """When the LLM returns a plain text message (no tool calls),
        the conversation finishes and the text is extracted as the result."""
        manager._ensure_parent(parent_conversation)

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=_make_text_response("plain text answer"),
            ),
        ):
            task = manager.start_task(
                prompt="Just answer",
                conversation=parent_conversation,
                run_in_background=False,
            )

        assert task.status == TaskStatus.SUCCEEDED
        assert task.result == "plain text answer"

        # Verify persistence
        output = manager.get_task_output(task.id)
        assert output.result == "plain text answer"

    def test_background_task_persists_after_thread_finishes(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """A background task should persist its conversation to disk
        once the background thread completes."""
        manager._ensure_parent(parent_conversation)

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=_make_finish_response("bg answer"),
            ),
        ):
            task = manager.start_task(
                prompt="background work",
                conversation=parent_conversation,
                run_in_background=True,
            )

            # Immediately after start, task is still running
            assert task.status == TaskStatus.RUNNING
            assert task.thread is not None

            # Wait for the background thread to finish
            task.thread.join(timeout=10)

        # -- Task completed and was evicted to disk ----------------------
        assert task.status == TaskStatus.SUCCEEDED
        assert task.result == "bg answer"
        assert task.id in manager._inactive_tasks

        # -- Files on disk -----------------------------------------------
        task_dir = manager._get_task_directory(task.id)
        assert (task_dir / "task_state.json").exists()

        base_state_files = list(manager._tmp_dir.rglob("base_state.json"))
        assert len(base_state_files) >= 1

        event_files = list(manager._tmp_dir.rglob("event-*.json"))
        assert len(event_files) >= 2

        # -- Loadable from disk ------------------------------------------
        output = manager.get_task_output(task.id)
        assert output.status == TaskStatus.SUCCEEDED
        assert output.result == "bg answer"

    def test_multiple_tasks_each_get_separate_persistence(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """Two sequential tasks should each have their own persistence
        directory with independent events and task state."""
        manager._ensure_parent(parent_conversation)

        task_ids = []
        for i in range(2):
            with (
                patch(
                    "openhands.tools.claude.impl.get_agent_factory",
                    return_value=_TEST_FACTORY,
                ),
                patch(
                    "openhands.sdk.llm.llm.litellm_completion",
                    return_value=_make_finish_response(f"result_{i}"),
                ),
            ):
                task = manager.start_task(
                    prompt=f"task {i}",
                    conversation=parent_conversation,
                    run_in_background=False,
                )
                task_ids.append(task.id)
                assert task.result == f"result_{i}"

        # Both tasks evicted
        assert all(tid in manager._inactive_tasks for tid in task_ids)

        # Each task has its own task_state.json
        for i, tid in enumerate(task_ids):
            task_dir = manager._get_task_directory(tid)
            loaded = TaskState.load_from_disk(task_dir / "task_state")
            assert loaded is not None
            assert loaded.result == f"result_{i}"

        # There should be multiple base_state.json files (one per conversation)
        base_state_files = list(manager._tmp_dir.rglob("base_state.json"))
        assert len(base_state_files) >= 2

    def test_stopped_background_task_does_not_overwrite_status(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """If we stop a background task while it's blocked in the LLM call,
        the stopped status should be preserved after the thread exits."""
        manager._ensure_parent(parent_conversation)

        # Make litellm_completion block until we release it
        barrier = threading.Event()

        def _slow_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("should be ignored")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_slow_completion,
            ),
        ):
            task = manager.start_task(
                prompt="will be stopped",
                conversation=parent_conversation,
                run_in_background=True,
            )
            assert task.status == TaskStatus.RUNNING

            # Stop the task before the LLM returns
            manager.stop_task(task.id)
            assert task.status == TaskStatus.STOPPED

            # Release the LLM call
            barrier.set()
            assert task.thread
            task.thread.join(timeout=10)

        # Status must still be STOPPED, not overwritten by the finish result
        assert task.status == TaskStatus.STOPPED

    def test_task_with_llm_error_persists_error_state(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """If the LLM call raises an exception, the task should end up
        in ERROR state with the error persisted to disk."""
        manager._ensure_parent(parent_conversation)

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=RuntimeError("LLM is down"),
            ),
        ):
            task = manager.start_task(
                prompt="will fail",
                conversation=parent_conversation,
                run_in_background=False,
            )

        assert task.status == TaskStatus.ERROR
        assert task.error is not None
        assert "LLM is down" in task.error

        # Error state persisted to disk
        task_dir = manager._get_task_directory(task.id)
        loaded = TaskState.load_from_disk(task_dir / "task_state")
        assert loaded is not None
        assert loaded.status == TaskStatus.ERROR
        assert loaded.error is not None
        assert "LLM is down" in loaded.error


class TestBackgroundTaskExecution:
    """Integration tests for background task execution, polling, and blocking."""

    def test_get_task_output_blocks_until_completion(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """get_task_output(block=True) should wait for a background task
        to finish and return the completed result."""
        manager._ensure_parent(parent_conversation)

        # Use a barrier to control when the LLM responds
        barrier = threading.Event()

        def _delayed_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("blocking result")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_delayed_completion,
            ),
        ):
            task = manager.start_task(
                prompt="background work",
                conversation=parent_conversation,
                run_in_background=True,
            )

            assert task.status == TaskStatus.RUNNING

            # Release the LLM so the task can finish
            barrier.set()

            # Block until the task completes (via get_task_output)
            output = manager.get_task_output(task.id, block=True, timeout_ms=10000)

        assert output.status == TaskStatus.SUCCEEDED
        assert output.result == "blocking result"

    def test_get_task_output_nonblocking_returns_running(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """get_task_output(block=False) on a still-running background task
        should return immediately with status=running."""
        manager._ensure_parent(parent_conversation)

        barrier = threading.Event()

        def _blocked_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("eventually done")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_blocked_completion,
            ),
        ):
            task = manager.start_task(
                prompt="slow task",
                conversation=parent_conversation,
                run_in_background=True,
            )

            # Non-blocking poll while task is still running
            output = manager.get_task_output(task.id, block=False)
            assert output.status == TaskStatus.RUNNING
            assert output.result is None

            # Let the task finish and clean up
            barrier.set()
            assert task.thread
            task.thread.join(timeout=10)

    def test_get_task_output_timeout_expires(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """get_task_output with a short timeout should return while the
        task is still running if the timeout expires."""
        manager._ensure_parent(parent_conversation)

        barrier = threading.Event()

        def _very_slow_completion(**kwargs):
            barrier.wait(timeout=30)
            return _make_finish_response("too late")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_very_slow_completion,
            ),
        ):
            task = manager.start_task(
                prompt="very slow task",
                conversation=parent_conversation,
                run_in_background=True,
            )

            import time

            start = time.monotonic()
            output = manager.get_task_output(task.id, block=True, timeout_ms=200)
            elapsed = time.monotonic() - start

            # Should have returned after ~200ms, not waiting for the full task
            assert elapsed < 2.0
            # Task is still running because the timeout expired
            assert output.status == TaskStatus.RUNNING

            # Clean up
            barrier.set()
            assert task.thread
            task.thread.join(timeout=10)

    def test_background_task_error_retrievable_via_get_task_output(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """A background task that fails should have its error retrievable
        via get_task_output after the thread completes."""
        manager._ensure_parent(parent_conversation)

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=RuntimeError("background crash"),
            ),
        ):
            task = manager.start_task(
                prompt="will fail in background",
                conversation=parent_conversation,
                run_in_background=True,
            )

            # Wait for thread to finish
            output = manager.get_task_output(task.id, block=True, timeout_ms=10000)

        assert output.status == TaskStatus.ERROR
        assert output.error is not None
        assert "background crash" in output.error

    def test_background_task_stop_then_get_output(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """Stopping a background task and then getting its output should
        return the stopped state."""
        manager._ensure_parent(parent_conversation)

        barrier = threading.Event()

        def _blocked_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("ignored")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_blocked_completion,
            ),
        ):
            task = manager.start_task(
                prompt="will be stopped",
                conversation=parent_conversation,
                run_in_background=True,
            )

            # Stop the task
            stopped = manager.stop_task(task.id)
            assert stopped is not None
            assert stopped.status == TaskStatus.STOPPED

            # Release LLM and wait for thread
            barrier.set()
            assert task.thread
            task.thread.join(timeout=10)

        # Output should reflect stopped state (loaded from disk after eviction)
        output = manager.get_task_output(task.id)
        assert output.status == TaskStatus.STOPPED


class TestExecutorIntegration:
    """Integration tests that exercise the full executor → manager → conversation
    path, simulating how the tools are actually called by the agent loop."""

    def test_task_executor_sync_full_flow(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """TaskExecutor should run a sync task through the full
        conversation lifecycle and return a completed observation."""
        from openhands.tools.claude.definition import TaskAction, TaskObservation
        from openhands.tools.claude.impl import TaskExecutor

        manager._ensure_parent(parent_conversation)
        executor = TaskExecutor(manager=manager)

        action = TaskAction(
            prompt="Do work via executor",
            description="executor test",
        )

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                return_value=_make_finish_response("executor result"),
            ),
        ):
            obs = executor(action, conversation=parent_conversation)

        assert isinstance(obs, TaskObservation)
        assert obs.status == "completed"
        assert obs.text == "executor result"
        assert obs.task_id.startswith("task_")

    def test_task_executor_background_then_output_executor(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """Full flow: TaskExecutor launches background → TaskOutputExecutor
        retrieves result, simulating real agent tool call sequence."""
        from openhands.tools.claude.definition import (
            TaskAction,
            TaskObservation,
            TaskOutputAction,
            TaskOutputObservation,
        )
        from openhands.tools.claude.impl import TaskExecutor, TaskOutputExecutor

        manager._ensure_parent(parent_conversation)
        task_executor = TaskExecutor(manager=manager)
        output_executor = TaskOutputExecutor(manager=manager)

        barrier = threading.Event()

        def _delayed_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("bg executor result")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_delayed_completion,
            ),
        ):
            # Step 1: Launch background task
            launch_action = TaskAction(
                prompt="background via executor",
                run_in_background=True,
            )
            launch_obs = task_executor(launch_action, conversation=parent_conversation)

            assert isinstance(launch_obs, TaskObservation)
            assert launch_obs.status == "running"
            task_id = launch_obs.task_id

            # Step 2: Non-blocking poll
            poll_action = TaskOutputAction(task_id=task_id, block=False)
            poll_obs = output_executor(poll_action)
            assert isinstance(poll_obs, TaskOutputObservation)
            assert poll_obs.status == "running"

            # Step 3: Release LLM and block until done
            barrier.set()
            wait_action = TaskOutputAction(task_id=task_id, block=True, timeout=10000)
            final_obs = output_executor(wait_action)

        assert isinstance(final_obs, TaskOutputObservation)
        assert final_obs.status == "completed"
        assert final_obs.text == "bg executor result"

    def test_task_executor_stop_executor_full_flow(
        self, parent_conversation: LocalConversation, manager: DelegationManager
    ):
        """Full flow: TaskExecutor launches background → TaskStopExecutor
        stops it, simulating the stop tool call sequence."""
        from openhands.tools.claude.definition import (
            TaskAction,
            TaskStopAction,
            TaskStopObservation,
        )
        from openhands.tools.claude.impl import TaskExecutor, TaskStopExecutor

        manager._ensure_parent(parent_conversation)
        task_executor = TaskExecutor(manager=manager)
        stop_executor = TaskStopExecutor(manager=manager)

        barrier = threading.Event()

        def _blocked_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("should be stopped")

        with (
            patch(
                "openhands.tools.claude.impl.get_agent_factory",
                return_value=_TEST_FACTORY,
            ),
            patch(
                "openhands.sdk.llm.llm.litellm_completion",
                side_effect=_blocked_completion,
            ),
        ):
            # Launch background task
            launch_obs = task_executor(
                TaskAction(prompt="stoppable task", run_in_background=True),
                conversation=parent_conversation,
            )
            task_id = launch_obs.task_id

            # Stop it
            stop_obs = stop_executor(TaskStopAction(task_id=task_id))
            assert isinstance(stop_obs, TaskStopObservation)
            assert stop_obs.status == "stopped"

            # Clean up thread
            barrier.set()
            task = manager._active_tasks.get(task_id)
            if task and task.thread:
                task.thread.join(timeout=10)

    def test_task_executor_capacity_limit(
        self,
        parent_conversation: LocalConversation,
    ):
        """Starting more tasks than max_tasks should fail gracefully
        through the executor."""
        from openhands.tools.claude.definition import TaskAction, TaskObservation
        from openhands.tools.claude.impl import DelegationManager, TaskExecutor

        mgr = DelegationManager(max_tasks=1)
        mgr._ensure_parent(parent_conversation)
        executor = TaskExecutor(manager=mgr)

        barrier = threading.Event()

        def _blocked_completion(**kwargs):
            barrier.wait(timeout=10)
            return _make_finish_response("done")

        try:
            with (
                patch(
                    "openhands.tools.claude.impl.get_agent_factory",
                    return_value=_TEST_FACTORY,
                ),
                patch(
                    "openhands.sdk.llm.llm.litellm_completion",
                    side_effect=_blocked_completion,
                ),
            ):
                # First task should succeed
                obs1 = executor(
                    TaskAction(prompt="first", run_in_background=True),
                    conversation=parent_conversation,
                )
                assert obs1.status == "running"

                # Second task should fail (capacity reached)
                obs2 = executor(
                    TaskAction(prompt="second", run_in_background=True),
                    conversation=parent_conversation,
                )
                assert isinstance(obs2, TaskObservation)
                assert obs2.is_error is True

                barrier.set()
        finally:
            mgr.close()
