"""Tests for hook executor."""

import json

import pytest

from openhands.sdk.hooks.config import HookDefinition, HookType
from openhands.sdk.hooks.executor import HookExecutor, HookResult
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


class TestCallbackHooks:
    """Tests for callback-based hooks."""

    @pytest.fixture
    def executor(self, tmp_path):
        """Create an executor with a temporary working directory."""
        return HookExecutor(working_dir=str(tmp_path))

    @pytest.fixture
    def sample_event(self):
        """Create a sample hook event."""
        return HookEvent(
            event_type=HookEventType.STOP,
            session_id="test-session",
        )

    def test_callback_hook_returns_hook_result(self, executor, sample_event):
        """Test that callback hooks can return HookResult directly."""

        def my_callback(event: HookEvent) -> HookResult:
            return HookResult(
                success=True,
                decision=HookDecision.ALLOW,
                additional_context="Callback executed successfully",
            )

        hook = HookDefinition(
            type=HookType.CALLBACK,
            callback=my_callback,
        )
        result = executor.execute(hook, sample_event)

        assert result.success
        assert result.decision == HookDecision.ALLOW
        assert result.additional_context == "Callback executed successfully"

    def test_callback_hook_can_deny(self, executor, sample_event):
        """Test that callback hooks can deny operations."""

        def deny_callback(event: HookEvent) -> HookResult:
            return HookResult(
                success=True,
                blocked=True,
                decision=HookDecision.DENY,
                reason="Not allowed by callback",
            )

        hook = HookDefinition(
            type=HookType.CALLBACK,
            callback=deny_callback,
        )
        result = executor.execute(hook, sample_event)

        assert result.blocked
        assert result.decision == HookDecision.DENY
        assert result.reason == "Not allowed by callback"
        assert not result.should_continue

    def test_callback_hook_receives_event(self, executor, sample_event):
        """Test that callback receives the event data."""
        received_event = None

        def capture_callback(event: HookEvent) -> HookResult:
            nonlocal received_event
            received_event = event
            return HookResult(success=True)

        hook = HookDefinition(
            type=HookType.CALLBACK,
            callback=capture_callback,
        )
        executor.execute(hook, sample_event)

        assert received_event is not None
        assert received_event.event_type == HookEventType.STOP
        assert received_event.session_id == "test-session"

    def test_callback_hook_exception_returns_error(self, executor, sample_event):
        """Test that exceptions in callbacks are handled gracefully."""

        def failing_callback(event: HookEvent) -> HookResult:
            raise ValueError("Something went wrong")

        hook = HookDefinition(
            type=HookType.CALLBACK,
            callback=failing_callback,
        )
        result = executor.execute(hook, sample_event)

        assert not result.success
        assert "Something went wrong" in result.error

    def test_callback_hook_validation_requires_callback(self):
        """Test that callback hook type requires a callback function."""
        with pytest.raises(ValueError, match="callback is required"):
            HookDefinition(type=HookType.CALLBACK)

    def test_callback_hook_in_execute_all(self, executor, sample_event):
        """Test that callback hooks work in execute_all."""

        def allow_callback(event: HookEvent) -> HookResult:
            return HookResult(success=True, decision=HookDecision.ALLOW)

        def deny_callback(event: HookEvent) -> HookResult:
            return HookResult(success=True, blocked=True, decision=HookDecision.DENY)

        hooks = [
            HookDefinition(type=HookType.CALLBACK, callback=allow_callback),
            HookDefinition(type=HookType.CALLBACK, callback=deny_callback),
            HookDefinition(type=HookType.CALLBACK, callback=allow_callback),
        ]

        results = executor.execute_all(hooks, sample_event, stop_on_block=True)

        assert len(results) == 2  # Stopped after deny
        assert results[0].decision == HookDecision.ALLOW
        assert results[1].decision == HookDecision.DENY
