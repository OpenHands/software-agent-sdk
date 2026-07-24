"""Tests for hook executor."""

import json
import subprocess
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from openhands.sdk.conversation.conversation_stats import ConversationStats
from openhands.sdk.hooks.config import HookDefinition, HookType
from openhands.sdk.hooks.executor import HookExecutor
from openhands.sdk.hooks.types import HookDecision, HookEvent, HookEventType
from openhands.sdk.llm import LLM
from openhands.sdk.llm.utils.metrics import Metrics
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

        hook = HookDefinition(
            type=HookType.AGENT,
            system_prompt="Verify task completion",
        )

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
        hook = HookDefinition(
            type=HookType.AGENT,
            system_prompt="Check something",
        )
        result = executor_no_llm.execute(hook, sample_event)

        assert not result.success
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
            hook = HookDefinition(
                type=HookType.AGENT,
                system_prompt="Check tasks",
            )
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
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.reason == "Looks safe"

    @pytest.mark.parametrize(
        "payload",
        [
            '{"reason": "no decision field"}',
            '{"decision": "maybe", "reason": "not allow or deny"}',
            '{"decision": "", "reason": "empty decision"}',
        ],
    )
    def test_invalid_decision_falls_open(self, executor, sample_event, payload):
        """Missing/unknown decision is a fall-open, not a deliberate allow."""
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=payload),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.should_continue
        assert result.success is False
        assert result.error is not None

    def test_markdown_wrapped_deny_is_parsed(self, executor, sample_event):
        """```json fenced JSON is honoured, not treated as non-JSON."""
        fenced = '```json\n{"decision": "deny", "reason": "Sensitive file read"}\n```'

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=fenced),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert result.blocked
        assert result.decision == HookDecision.DENY
        assert result.reason == "Sensitive file read"

    def test_plain_fence_without_language_tag_is_parsed(self, executor, sample_event):
        """Some LLMs use ``` without a language tag — still extract the JSON."""
        fenced = '```\n{"decision": "allow", "reason": "ok"}\n```'

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=fenced),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.reason == "ok"

    def test_prose_prefix_before_json_is_parsed(self, executor, sample_event):
        """Prose before the JSON object must not defeat the parser."""
        prose_then_json = (
            "After reviewing the workspace I found REPORT.md is missing.\n\n"
            '{"decision": "deny", "reason": "missing deliverable"}'
        )

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=prose_then_json),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert result.blocked
        assert result.decision == HookDecision.DENY
        assert result.reason == "missing deliverable"

    def test_prose_suffix_after_json_is_parsed(self, executor, sample_event):
        """Trailing chatter after the JSON object must not defeat the parser."""
        json_then_prose = (
            '{"decision": "deny", "reason": "sensitive file"}\n\n'
            "Let me know if you need more details."
        )

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value=json_then_prose),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert result.blocked
        assert result.decision == HookDecision.DENY
        assert result.reason == "sensitive file"

    def test_invalid_json_defaults_to_allow(self, executor, sample_event):
        """Non-JSON response falls open: ALLOW + success=False + error set."""
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH) as mock_conv_cls,
            patch(self._RESPONSE_PATH, return_value="I think you should allow this."),
        ):
            mock_conv_cls.return_value = MagicMock()
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.should_continue
        assert result.success is False
        assert result.error is not None

    def test_sub_conversation_failure_defaults_to_allow(self, executor, sample_event):
        """_execute_agent_hook when sub-conversation raises defaults to allow."""
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH, side_effect=RuntimeError("workspace error")),
        ):
            hook = HookDefinition(type=HookType.AGENT)
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
            hook = HookDefinition(type=HookType.AGENT)
            result = executor._execute_agent_hook(hook, sample_event)

        assert not result.blocked
        assert result.decision == HookDecision.ALLOW
        assert result.success is False

    def test_timeout_propagated_to_hook_llm(self, executor, sample_event):
        """hook.timeout is forwarded to the copied LLM, parent's timeout untouched."""
        parent_timeout = executor.llm.timeout
        captured_llm = {}

        def capture_agent_init(**kwargs):
            captured_llm["llm"] = kwargs.get("llm")
            return MagicMock()

        with (
            patch(self._AGENT_PATH, side_effect=capture_agent_init),
            patch(self._CONV_PATH, side_effect=RuntimeError("stop early")),
        ):
            hook = HookDefinition(type=HookType.AGENT, timeout=7)
            executor._execute_agent_hook(hook, sample_event)

        hook_llm = captured_llm.get("llm")
        assert hook_llm is not None
        assert hook_llm.timeout == 7
        assert executor.llm.timeout == parent_timeout

    def test_agent_hook_inherits_conversation_llm_headers(
        self, tmp_path, mock_llm, sample_event
    ):
        executor = HookExecutor(
            working_dir=str(tmp_path),
            llm=mock_llm,
            llm_extra_headers={"X-Request-ID": "request-1"},
        )

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH, side_effect=RuntimeError("stop early")) as mock_conv,
        ):
            executor._execute_agent_hook(
                HookDefinition(type=HookType.AGENT), sample_event
            )

        assert mock_conv.call_args.kwargs["llm_extra_headers"] == {
            "X-Request-ID": "request-1"
        }

    def test_hook_metrics_under_usage_id(self, executor, sample_event):
        """Hook LLM uses per-hook usage_id and an isolated Metrics object."""
        parent_metrics = executor.llm.metrics

        captured_llm = {}

        def capture_agent_init(**kwargs):
            captured_llm["llm"] = kwargs.get("llm")
            return MagicMock()

        with (
            patch(self._AGENT_PATH, side_effect=capture_agent_init),
            patch(self._CONV_PATH, side_effect=RuntimeError("stop early")),
        ):
            hook = HookDefinition(type=HookType.AGENT)
            executor._execute_agent_hook(hook, sample_event)

        hook_llm = captured_llm.get("llm")
        assert hook_llm is not None
        assert hook_llm is not executor.llm
        assert hook_llm.usage_id == "agent-hook:default"
        assert hook_llm.metrics is not parent_metrics

    def test_llm_getter_is_resolved_live(self, tmp_path, sample_event):
        """An llm_getter is read at execution time, so agent hooks follow
        switch_llm()/switch_profile() instead of using a stale captured LLM."""
        current = {
            "llm": LLM(model="gpt-4o", api_key=SecretStr("k1"), usage_id="first")
        }
        executor = HookExecutor(
            working_dir=str(tmp_path),
            llm_getter=lambda: current["llm"],
        )

        # Simulate switch_llm(): the conversation rebinds its agent's LLM.
        current["llm"] = LLM(
            model="gpt-5.5", api_key=SecretStr("k2"), usage_id="second"
        )

        captured_llm = {}

        def capture_agent_init(**kwargs):
            captured_llm["llm"] = kwargs.get("llm")
            return MagicMock()

        with (
            patch(self._AGENT_PATH, side_effect=capture_agent_init),
            patch(self._CONV_PATH, side_effect=RuntimeError("stop early")),
        ):
            executor._execute_agent_hook(
                HookDefinition(type=HookType.AGENT), sample_event
            )

        hook_llm = captured_llm.get("llm")
        assert hook_llm is not None
        # Copied from the *current* LLM (gpt-5.5), not the one present at init.
        assert hook_llm.model == "gpt-5.5"

    def test_hook_metrics_are_merged_into_parent_stats(
        self, tmp_path, mock_llm, sample_event
    ):
        """Child agent-hook spend is included in parent conversation stats."""
        parent_stats = ConversationStats()
        existing_hook_metrics = Metrics(model_name="gpt-4o")
        existing_hook_metrics.add_cost(0.25)
        parent_stats.usage_to_metrics["agent-hook:security-check"] = (
            existing_hook_metrics
        )

        child_hook_metrics = Metrics(model_name="gpt-4o")
        child_hook_metrics.add_cost(0.75)
        child_stats = ConversationStats(
            usage_to_metrics={"agent-hook:security-check": child_hook_metrics}
        )
        mock_conversation = MagicMock()
        mock_conversation.conversation_stats = child_stats
        mock_conversation.state.events = []

        executor = HookExecutor(
            working_dir=str(tmp_path),
            llm=mock_llm,
            conversation_stats=parent_stats,
        )

        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH, return_value=mock_conversation),
            patch(
                self._RESPONSE_PATH,
                return_value='{"decision": "allow", "reason": "ok"}',
            ),
        ):
            hook = HookDefinition(type=HookType.AGENT, name="security-check")
            result = executor._execute_agent_hook(hook, sample_event)

        assert result.decision == HookDecision.ALLOW
        assert parent_stats.usage_to_metrics[
            "agent-hook:security-check"
        ].accumulated_cost == pytest.approx(1.0)
        assert parent_stats.get_combined_metrics().accumulated_cost == pytest.approx(
            1.0
        )

    def test_hook_usage_id_uses_hook_name(self, executor, sample_event):
        """A named hook gets its own usage_id bucket: agent-hook:<name>."""
        captured_llm = {}

        def capture_agent_init(**kwargs):
            captured_llm["llm"] = kwargs.get("llm")
            return MagicMock()

        with (
            patch(self._AGENT_PATH, side_effect=capture_agent_init),
            patch(self._CONV_PATH, side_effect=RuntimeError("stop early")),
        ):
            hook = HookDefinition(type=HookType.AGENT, name="security-check")
            executor._execute_agent_hook(hook, sample_event)

        hook_llm = captured_llm.get("llm")
        assert hook_llm is not None
        assert hook_llm.usage_id == "agent-hook:security-check"

    def test_parent_visualizer_instance_is_not_rebound(self, tmp_path, mock_llm):
        """The parent visualizer instance must never be handed to the hook conv.

        LocalConversation.initialize() rebinds a visualizer instance to its own
        state, so the executor must request a fresh sub-visualizer instead.
        """
        from openhands.sdk.conversation.visualizer import ConversationVisualizerBase

        sub_viz = MagicMock(spec=ConversationVisualizerBase)

        class _Viz(ConversationVisualizerBase):
            def on_event(self, event):  # pragma: no cover - not exercised
                pass

            def create_sub_visualizer(self, agent_id):
                self.requested_agent_id = agent_id
                return sub_viz

        parent_viz = _Viz()
        executor = HookExecutor(
            working_dir=str(tmp_path),
            llm=mock_llm,
            visualizer=parent_viz,
        )

        captured = {}

        def capture_conv_init(**kwargs):
            captured["visualizer"] = kwargs.get("visualizer")
            raise RuntimeError("stop early")

        sample_event = HookEvent(
            event_type=HookEventType.PRE_TOOL_USE, tool_name="BashTool"
        )
        with (
            patch(self._AGENT_PATH),
            patch(self._CONV_PATH, side_effect=capture_conv_init),
        ):
            hook = HookDefinition(type=HookType.AGENT, name="security-check")
            executor._execute_agent_hook(hook, sample_event)

        assert captured["visualizer"] is sub_viz
        assert captured["visualizer"] is not parent_viz
        assert parent_viz.requested_agent_id == "agent-hook:security-check"


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
        hook = HookDefinition(type=HookType.PROMPT, prompt="evaluate this event")
        result = executor.execute(hook, sample_event)
        assert result.decision == HookDecision.ALLOW
        assert result.success is False
        assert "not yet implemented" in (result.reason or "")

    def test_prompt_hook_does_not_block(self, executor, sample_event):
        """PROMPT hook must not block the operation while unimplemented."""
        hook = HookDefinition(type=HookType.PROMPT, prompt="evaluate this event")
        result = executor.execute(hook, sample_event)
        assert result.blocked is False
        assert result.should_continue is True

    def test_prompt_hook_without_command_validates(self):
        """PROMPT hook with no command is valid at config time (future use)."""
        hook = HookDefinition(type=HookType.PROMPT, prompt="evaluate this event")
        assert hook.command == ""
