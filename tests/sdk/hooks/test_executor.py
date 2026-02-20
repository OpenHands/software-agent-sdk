"""Tests for hook executor."""

import json

import pytest

from openhands.sdk.hooks.config import HookDefinition
from openhands.sdk.hooks.executor import HookExecutor
from openhands.sdk.hooks.types import HookDecision, HookEvent, HookEventType


class TestHookExecutor:
    """Tests for HookExecutor."""

    @pytest.fixture
    def executor(self, tmp_path):
        """Create an executor with a temporary working directory."""
        return HookExecutor(working_dir=str(tmp_path))

    @pytest.fixture
    def sample_event(self):
        """Create a sample hook event."""
        return HookEvent(
            event_type=HookEventType.PRE_TOOL_USE,
            tool_name="BashTool",
            tool_input={"command": "ls -la"},
            session_id="test-session",
        )

    def test_execute_simple_command(self, executor, sample_event):
        """Test executing a simple echo command."""
        hook = HookDefinition(command="echo 'success'")
        result = executor.execute(hook, sample_event)

        assert result.success
        assert result.exit_code == 0
        assert "success" in result.stdout

    def test_execute_receives_json_stdin(self, executor, sample_event, tmp_path):
        """Test that hook receives event data as JSON on stdin."""
        script_path = tmp_path / "echo_stdin.sh"
        script_path.write_text("#!/bin/bash\ncat")
        script_path.chmod(0o755)

        hook = HookDefinition(command=str(script_path))
        result = executor.execute(hook, sample_event)

        assert result.success
        output_data = json.loads(result.stdout)
        assert output_data["event_type"] == "PreToolUse"
        assert output_data["tool_name"] == "BashTool"

    def test_execute_blocking_exit_code(self, executor, sample_event):
        """Test that exit code 2 blocks the operation."""
        hook = HookDefinition(command="exit 2")
        result = executor.execute(hook, sample_event)

        assert not result.success
        assert result.blocked
        assert result.exit_code == 2
        assert not result.should_continue

    def test_execute_json_output_decision(self, executor, sample_event):
        """Test parsing JSON output with decision field."""
        hook = HookDefinition(
            command='echo \'{"decision": "deny", "reason": "Not allowed"}\''
        )
        result = executor.execute(hook, sample_event)

        assert result.decision == HookDecision.DENY
        assert result.reason == "Not allowed"
        assert result.blocked

    def test_execute_environment_variables(self, executor, sample_event, tmp_path):
        """Test that environment variables are set correctly."""
        script_path = tmp_path / "check_env.sh"
        script_path.write_text(
            "#!/bin/bash\n"
            'echo "SESSION=$OPENHANDS_SESSION_ID"\n'
            'echo "TOOL=$OPENHANDS_TOOL_NAME"\n'
        )
        script_path.chmod(0o755)

        hook = HookDefinition(command=str(script_path))
        result = executor.execute(hook, sample_event)

        assert result.success
        assert "SESSION=test-session" in result.stdout
        assert "TOOL=BashTool" in result.stdout

    def test_execute_timeout(self, executor, sample_event):
        """Test that timeout is enforced."""
        hook = HookDefinition(command="sleep 10", timeout=1)
        result = executor.execute(hook, sample_event)

        assert not result.success
        assert "timed out" in result.error.lower()

    def test_execute_all_stops_on_block(self, executor, sample_event):
        """Test that execute_all stops on blocking hook."""
        hooks = [
            HookDefinition(command="echo 'first'"),
            HookDefinition(command="exit 2"),
            HookDefinition(command="echo 'third'"),
        ]

        results = executor.execute_all(hooks, sample_event, stop_on_block=True)

        assert len(results) == 2  # Stopped after second hook
        assert results[0].success
        assert results[1].blocked

    def test_execute_captures_stderr(self, executor, sample_event):
        """Test that stderr is captured."""
        hook = HookDefinition(command="echo 'error message' >&2 && exit 2")
        result = executor.execute(hook, sample_event)

        assert result.blocked
        assert "error message" in result.stderr


class TestAsyncHookExecution:
    """Tests for async hook execution."""

    @pytest.fixture
    def executor(self, tmp_path):
        """Create an executor with a temporary working directory."""
        return HookExecutor(working_dir=str(tmp_path))

    @pytest.fixture
    def sample_event(self):
        """Create a sample hook event."""
        return HookEvent(
            event_type=HookEventType.POST_TOOL_USE,
            tool_name="TestTool",
            tool_input={"arg": "value"},
            session_id="test-session",
        )

    def test_execute_async_hook_returns_immediately(self, executor, sample_event):
        """Test that async hooks return immediately without waiting."""
        import time

        hook = HookDefinition.model_validate({"command": "sleep 5", "async": True})

        start = time.time()
        result = executor.execute(hook, sample_event)
        elapsed = time.time() - start

        assert result.success
        assert result.async_started
        assert elapsed < 1.0  # Should return immediately, not wait 5s

    def test_execute_async_hook_result_fields(self, executor, sample_event):
        """Test that async hook result has expected field values."""
        hook = HookDefinition.model_validate({"command": "echo 'test'", "async": True})
        result = executor.execute(hook, sample_event)

        assert result.success is True
        assert result.async_started is True
        assert result.exit_code == 0
        assert result.blocked is False
        assert result.stdout == ""  # No output captured for async
        assert result.stderr == ""

    def test_execute_async_hook_process_tracked(self, executor, sample_event, tmp_path):
        """Test that async hooks track processes for cleanup."""
        marker = tmp_path / "async_marker.txt"
        hook = HookDefinition.model_validate(
            {"command": f"sleep 0.3 && touch {marker}", "async": True, "timeout": 5}
        )

        result = executor.execute(hook, sample_event)
        assert result.async_started

        # Process should be tracked
        assert len(executor.async_process_manager._processes) == 1

        # Wait for process to complete and verify marker file created
        import time

        time.sleep(0.5)
        assert marker.exists()

    def test_execute_async_hook_receives_stdin(self, executor, sample_event, tmp_path):
        """Test that async hooks receive event data on stdin."""
        output_file = tmp_path / "stdin_output.json"
        # Script that reads stdin and writes to file
        hook = HookDefinition.model_validate(
            {"command": f"cat > {output_file}", "async": True, "timeout": 5}
        )

        result = executor.execute(hook, sample_event)
        assert result.async_started

        # Wait for async process to complete
        import json
        import time

        time.sleep(0.3)

        assert output_file.exists()
        content = json.loads(output_file.read_text())
        assert content["tool_name"] == "TestTool"
        assert content["event_type"] == "PostToolUse"

    def test_sync_hook_not_marked_async(self, executor, sample_event):
        """Test that synchronous hooks are not marked as async_started."""
        hook = HookDefinition.model_validate({"command": "echo 'sync'", "async": False})
        result = executor.execute(hook, sample_event)

        assert result.success
        assert result.async_started is False
        assert "sync" in result.stdout

    def test_execute_all_with_mixed_sync_async_hooks(
        self, executor, sample_event, tmp_path
    ):
        """Test execute_all with a mix of sync and async hooks."""
        marker = tmp_path / "async_ran.txt"
        hooks = [
            HookDefinition(command="echo 'sync1'"),
            HookDefinition.model_validate(
                {"command": f"touch {marker}", "async": True}
            ),
            HookDefinition(command="echo 'sync2'"),
        ]

        results = executor.execute_all(hooks, sample_event, stop_on_block=False)

        assert len(results) == 3
        assert results[0].async_started is False
        assert results[1].async_started is True
        assert results[2].async_started is False

        # Wait for async hook to complete
        import time

        time.sleep(0.2)
        assert marker.exists()


class TestAsyncProcessManager:
    """Tests for AsyncProcessManager."""

    def test_add_process_and_cleanup_all(self, tmp_path):
        """Test that processes can be added and cleaned up."""
        import subprocess

        from openhands.sdk.hooks.executor import AsyncProcessManager

        manager = AsyncProcessManager()

        # Start a long-running process with new session for process group cleanup
        process = subprocess.Popen(
            "sleep 60",
            shell=True,
            cwd=str(tmp_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        manager.add_process(process, timeout=30)
        assert len(manager._processes) == 1
        assert process.poll() is None  # Still running

        manager.cleanup_all()
        assert len(manager._processes) == 0

        # Give process time to terminate
        import time

        time.sleep(0.1)
        assert process.poll() is not None  # Terminated

    def test_cleanup_expired_terminates_old_processes(self, tmp_path):
        """Test that cleanup_expired terminates processes past their timeout."""
        import subprocess
        import time

        from openhands.sdk.hooks.executor import AsyncProcessManager

        manager = AsyncProcessManager()

        # Start a process with very short timeout that's already expired
        process = subprocess.Popen(
            "sleep 60",
            shell=True,
            cwd=str(tmp_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        # Add with a timeout in the past (simulated by setting start time)
        manager._processes.append(
            (process, time.time() - 10, 5)
        )  # Started 10s ago, 5s timeout

        assert process.poll() is None  # Still running
        manager.cleanup_expired()

        time.sleep(0.1)
        assert process.poll() is not None  # Terminated
        assert len(manager._processes) == 0

    def test_cleanup_expired_keeps_active_processes(self, tmp_path):
        """Test that cleanup_expired keeps processes within their timeout."""
        import subprocess

        from openhands.sdk.hooks.executor import AsyncProcessManager

        manager = AsyncProcessManager()

        process = subprocess.Popen(
            "sleep 60",
            shell=True,
            cwd=str(tmp_path),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        manager.add_process(process, timeout=60)  # Long timeout

        manager.cleanup_expired()

        # Process should still be tracked and running
        assert len(manager._processes) == 1
        assert process.poll() is None

        # Clean up for test teardown
        process.terminate()
