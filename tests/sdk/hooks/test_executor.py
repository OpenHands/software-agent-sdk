"""Tests for hook executor."""

import json
import subprocess
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.hooks.config import HookDefinition
from openhands.sdk.hooks.executor import HookExecutor
from openhands.sdk.hooks.types import HookDecision, HookEvent, HookEventType
from openhands.sdk.llm import LLM
from tests.command_utils import python_command


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
        hook = HookDefinition(
            command=python_command("import sys; sys.stdout.write(sys.stdin.read())")
        )
        result = executor.execute(hook, sample_event)

        assert result.success
        output_data = json.loads(result.stdout)
        assert output_data["event_type"] == "PreToolUse"
        assert output_data["tool_name"] == "BashTool"

    def test_execute_blocking_exit_code(self, executor, sample_event):
        """Test that exit code 2 blocks the operation."""
        hook = HookDefinition(command=python_command("import sys; sys.exit(2)"))
        result = executor.execute(hook, sample_event)

        assert not result.success
        assert result.blocked
        assert result.exit_code == 2
        assert not result.should_continue

    def test_execute_json_output_decision(self, executor, sample_event):
        """Test parsing JSON output with decision field."""
        hook = HookDefinition(
            command=python_command(
                "import json; print(json.dumps("
                "{'decision': 'deny', 'reason': 'Not allowed'}))"
            )
        )
        result = executor.execute(hook, sample_event)

        assert result.decision == HookDecision.DENY
        assert result.reason == "Not allowed"
        assert result.blocked

    def test_execute_environment_variables(self, executor, sample_event, tmp_path):
        """Test that environment variables are set correctly."""
        hook = HookDefinition(
            command=python_command(
                "import os; "
                "print(f\"SESSION={os.environ['OPENHANDS_SESSION_ID']}\"); "
                "print(f\"TOOL={os.environ['OPENHANDS_TOOL_NAME']}\")"
            )
        )

        result = executor.execute(hook, sample_event)

        assert result.success
        assert "SESSION=test-session" in result.stdout
        assert "TOOL=BashTool" in result.stdout

    def test_execute_timeout(self, executor, sample_event):
        """Test that timeout is enforced."""
        hook = HookDefinition(
            command=python_command("import time; time.sleep(10)"), timeout=1
        )
        result = executor.execute(hook, sample_event)

        assert not result.success
        assert "timed out" in result.error.lower()

    def test_execute_all_stops_on_block(self, executor, sample_event):
        """Test that execute_all stops on blocking hook."""
        hooks = [
            HookDefinition(command="echo 'first'"),
            HookDefinition(command=python_command("import sys; sys.exit(2)")),
            HookDefinition(command="echo 'third'"),
        ]

        results = executor.execute_all(hooks, sample_event, stop_on_block=True)

        assert len(results) == 2  # Stopped after second hook
        assert results[0].success
        assert results[1].blocked

    def test_execute_captures_stderr(self, executor, sample_event):
        """Test that stderr is captured."""
        hook = HookDefinition(
            command=python_command(
                "import sys; sys.stderr.write('error message\\n'); sys.exit(2)"
            )
        )
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

        hook = HookDefinition.model_validate(
            {"command": python_command("import time; time.sleep(5)"), "async": True}
        )

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
            {
                "command": python_command(
                    "import time; "
                    "from pathlib import Path; "
                    "time.sleep(0.3); "
                    f"Path({str(marker)!r}).touch()"
                ),
                "async": True,
                "timeout": 5,
            }
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
            {
                "command": python_command(
                    "import sys; "
                    "from pathlib import Path; "
                    f"Path({str(output_file)!r}).write_text(sys.stdin.read())"
                ),
                "async": True,
                "timeout": 5,
            }
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

    def test_execute_async_hook_uses_windows_process_group(
        self, executor, sample_event, monkeypatch
    ):
        """Test Windows process-group kwargs by simulating win32 on any runner."""
        import openhands.sdk.hooks.executor as executor_module

        popen_kwargs: dict[str, object] = {}
        stdin = mock.Mock()
        process = mock.Mock()
        process.stdin = stdin
        process.poll.return_value = None

        def fake_popen(*args, **kwargs):
            popen_kwargs.update(kwargs)
            return process

        monkeypatch.setattr(executor_module.os, "name", "nt", raising=False)
        monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        hook = HookDefinition.model_validate({"command": "echo test", "async": True})
        result = executor.execute(hook, sample_event)

        assert result.async_started is True
        assert popen_kwargs["creationflags"] == 512
        assert popen_kwargs["start_new_session"] is False

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
                {
                    "command": python_command(
                        f"from pathlib import Path; Path({str(marker)!r}).touch()"
                    ),
                    "async": True,
                }
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
        from openhands.sdk.hooks.executor import AsyncProcessManager

        manager = AsyncProcessManager()

        # Start a long-running process with new session for process group cleanup
        process = subprocess.Popen(
            python_command("import time; time.sleep(60)"),
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
        import time

        from openhands.sdk.hooks.executor import AsyncProcessManager

        manager = AsyncProcessManager()

        # Start a process with very short timeout that's already expired
        process = subprocess.Popen(
            python_command("import time; time.sleep(60)"),
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

    def test_async_process_manager_windows_kill_uses_bounded_wait(self, monkeypatch):
        """Test that Windows cleanup does not wait indefinitely after kill."""
        import openhands.sdk.hooks.executor as executor_module
        from openhands.sdk.hooks.executor import AsyncProcessManager

        process = mock.Mock()
        process.pid = 123
        process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="cmd", timeout=1),
            subprocess.TimeoutExpired(cmd="cmd", timeout=1),
        ]

        taskkill_calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            taskkill_calls.append(args)
            return mock.Mock()

        monkeypatch.setattr(executor_module.os, "name", "nt", raising=False)
        monkeypatch.setattr(subprocess, "run", fake_run)

        manager = AsyncProcessManager()
        manager._terminate_process(process)

        assert taskkill_calls == [["taskkill", "/F", "/T", "/PID", "123"]]
        assert process.wait.call_args_list == [
            mock.call(timeout=1),
            mock.call(timeout=1),
        ]
        process.kill.assert_called_once_with()

    def test_cleanup_expired_keeps_active_processes(self, tmp_path):
        """Test that cleanup_expired keeps processes within their timeout."""
        from openhands.sdk.hooks.executor import AsyncProcessManager

        manager = AsyncProcessManager()

        process = subprocess.Popen(
            python_command("import time; time.sleep(60)"),
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


class TestAgentHookExecution:
    """Tests for HookType.AGENT execution path."""

    # Patch at the package/module level so lazy `from X import Y` picks up the mock.
    _AGENT_PATH = "openhands.sdk.agent.Agent"
    _CONV_PATH = "openhands.sdk.conversation.impl.local_conversation.LocalConversation"
    _RESPONSE_PATH = (
        "openhands.sdk.conversation.response_utils.get_agent_final_response"
    )

    @pytest.fixture
    def mock_llm(self):
        return LLM(model="gpt-4o", api_key=SecretStr("test-key"), usage_id="test")

    @pytest.fixture
    def executor(self, tmp_path, mock_llm):
        return HookExecutor(working_dir=str(tmp_path), llm=mock_llm)

    @pytest.fixture
    def executor_no_llm(self, tmp_path):
        return HookExecutor(working_dir=str(tmp_path), llm=None)

    @pytest.fixture
    def sample_event(self):
        return HookEvent(
            event_type=HookEventType.STOP,
            session_id="test-session",
        )

    def test_execute_dispatches_to_agent_hook(self, executor, sample_event):
        """execute() routes AGENT type to _execute_agent_hook, not subprocess."""
        from openhands.sdk.hooks.executor import HookResult

        hook = HookDefinition(type="agent", prompt="Verify task completion")

        with patch.object(
            executor,
            "_execute_agent_hook",
            return_value=HookResult(
                success=True, decision=HookDecision.ALLOW, reason="ok"
            ),
        ) as mock_agent:
            result = executor.execute(hook, sample_event)
            mock_agent.assert_called_once_with(hook, sample_event)

        assert result.decision == HookDecision.ALLOW
        assert result.should_continue

    def test_no_llm_defaults_to_allow(self, executor_no_llm, sample_event):
        """Agent hook with no LLM configured defaults to allow without error."""
        hook = HookDefinition(type="agent", prompt="Check something")
        result = executor_no_llm.execute(hook, sample_event)

        assert result.success
        assert result.decision == HookDecision.ALLOW
        assert not result.blocked

    def test_deny_decision_blocks(self, executor, sample_event):
        """_execute_agent_hook parsing deny JSON produces a blocked HookResult."""
        deny_json = '{"decision": "deny", "reason": "Tasks not complete"}'

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=deny_json),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type="agent", prompt="Check tasks")
            result = executor._execute_agent_hook(hook, sample_event)

        assert result.blocked
        assert result.decision == HookDecision.DENY
        assert result.reason == "Tasks not complete"
        assert not result.should_continue

    def test_allow_decision_passes(self, executor, sample_event):
        """_execute_agent_hook parsing allow JSON produces a non-blocking HookResult."""
        allow_json = '{"decision": "allow", "reason": "Looks safe"}'

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=allow_json),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type="agent")
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.reason == "Looks safe"

    def test_invalid_json_defaults_to_allow(self, executor, sample_event):
        """_execute_agent_hook with non-JSON LLM response defaults to allow."""
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value="I think you should allow this."),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type="agent")
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.should_continue

    def test_sub_conversation_failure_defaults_to_allow(self, executor, sample_event):
        """_execute_agent_hook when sub-conversation raises defaults to allow."""
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH, side_effect=RuntimeError("workspace error")),
        ):
            hook = HookDefinition(type="agent")
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.error is not None

    def test_empty_response_defaults_to_allow(self, executor, sample_event):
        """_execute_agent_hook when agent produces no response defaults to allow."""
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=""),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type="agent")
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.success is False

    def test_timeout_enforced(self, executor, sample_event):
        """Agent hook respects hook.timeout via ThreadPoolExecutor."""
        import time

        def slow_run():
            time.sleep(10)

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
        ):
            mock_conv = MagicMock()
            mock_conv.run = slow_run
            mock_conv_cls.return_value = mock_conv

            hook = HookDefinition(type="agent", timeout=1)
            start = time.time()
            result = executor._execute_agent_hook(hook, sample_event)
            elapsed = time.time() - start

        assert elapsed < 5
        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert "timed out" in (result.reason or "")

    def test_metrics_are_isolated_from_parent_llm(self, executor, sample_event):
        """model_copy + reset_metrics ensures hook tokens don't pollute parent LLM."""
        captured_llm = {}

        def capture_agent_init(**kwargs):
            captured_llm["llm"] = kwargs.get("llm")
            return MagicMock()

        with (
            patch(self._AGENT_PATH, side_effect=capture_agent_init),
            patch(self._CONV_PATH, side_effect=RuntimeError("stop early")),
        ):
            hook = HookDefinition(type="agent")
            executor._execute_agent_hook(hook, sample_event)

        hook_llm = captured_llm.get("llm")
        assert hook_llm is not None
        assert hook_llm is not executor.llm
        assert hook_llm.usage_id == "hook-agent-evaluator"


class TestPromptHookNotImplemented:
    """HookType.PROMPT is a future stub — execution defaults to allow, never crashes."""

    @pytest.fixture
    def executor(self, tmp_path):
        return HookExecutor(working_dir=str(tmp_path))

    @pytest.fixture
    def sample_event(self):
        return HookEvent(event_type=HookEventType.PRE_TOOL_USE, tool_name="BashTool")

    def test_prompt_hook_defaults_to_allow(self, executor, sample_event):
        """Executing a PROMPT hook returns allow instead of crashing."""
        hook = HookDefinition(type="prompt")
        result = executor.execute(hook, sample_event)
        assert result.decision == HookDecision.ALLOW
        assert result.success is False
        assert "not yet implemented" in (result.reason or "")

    def test_prompt_hook_does_not_block(self, executor, sample_event):
        """PROMPT hook must not block the operation while unimplemented."""
        hook = HookDefinition(type="prompt")
        result = executor.execute(hook, sample_event)
        assert result.blocked is False
        assert result.should_continue is True

    def test_prompt_hook_without_command_validates(self):
        """PROMPT hook with no command is valid at config time (future use)."""
        hook = HookDefinition(type="prompt")
        assert hook.command is None
