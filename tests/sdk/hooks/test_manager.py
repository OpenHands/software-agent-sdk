"""Tests for HookManager."""

import pytest

from openhands.sdk.hooks.config import HookConfig
from openhands.sdk.hooks.manager import HookManager


class TestHookManager:
    """Tests for HookManager orchestration."""

    @pytest.fixture
    def tmp_working_dir(self, tmp_path):
        """Create a temporary working directory."""
        return str(tmp_path)

    @pytest.fixture
    def config_with_blocking_hook(self, tmp_path):
        """Create config with a blocking PreToolUse hook."""
        script_path = tmp_path / "block.sh"
        script_path.write_text(
            "#!/bin/bash\n"
            'echo \'{"decision": "deny", "reason": "Blocked by test"}\'\n'
            "exit 2"
        )
        script_path.chmod(0o755)

        return HookConfig.from_dict(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "BashTool",
                            "hooks": [{"type": "command", "command": str(script_path)}],
                        }
                    ]
                }
            }
        )

    def test_run_pre_tool_use_blocks_when_hook_denies(
        self, tmp_working_dir, config_with_blocking_hook
    ):
        """Test that PreToolUse blocks when hook denies."""
        manager = HookManager(
            config=config_with_blocking_hook,
            working_dir=tmp_working_dir,
            session_id="test-session",
        )

        should_continue, results = manager.run_pre_tool_use(
            tool_name="BashTool",
            tool_input={"command": "rm -rf /"},
        )

        assert not should_continue
        assert len(results) == 1
        assert results[0].blocked

    def test_run_post_tool_use(self, tmp_working_dir, tmp_path):
        """Test PostToolUse hooks execute."""
        log_file = tmp_path / "log.txt"
        script = tmp_path / "log.sh"
        script.write_text(f"#!/bin/bash\necho 'logged' >> {log_file}")
        script.chmod(0o755)

        hook = {"type": "command", "command": str(script)}
        config = HookConfig.from_dict(
            {"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [hook]}]}}
        )

        manager = HookManager(config=config, working_dir=tmp_working_dir)
        results = manager.run_post_tool_use(
            tool_name="BashTool",
            tool_input={"command": "ls"},
            tool_response={"output": "file1.txt\nfile2.txt"},
        )

        assert len(results) == 1
        assert results[0].success
        assert log_file.read_text().strip() == "logged"

    def test_run_user_prompt_submit(self, tmp_working_dir):
        """Test UserPromptSubmit hooks execute and return additionalContext."""
        cmd = 'echo \'{"additionalContext": "Always check tests"}\''
        config = HookConfig.from_dict(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}
                    ]
                }
            }
        )

        manager = HookManager(config=config, working_dir=tmp_working_dir)
        should_continue, additional_context, results = manager.run_user_prompt_submit(
            message="Hello, agent!"
        )

        assert should_continue
        assert additional_context == "Always check tests"
        assert len(results) == 1

    def test_run_session_start(self, tmp_working_dir, tmp_path):
        """Test SessionStart hooks execute."""
        marker_file = tmp_path / "started"
        script = tmp_path / "start.sh"
        script.write_text(f"#!/bin/bash\ntouch {marker_file}")
        script.chmod(0o755)

        hook = {"type": "command", "command": str(script)}
        config = HookConfig.from_dict(
            {"hooks": {"SessionStart": [{"matcher": "*", "hooks": [hook]}]}}
        )

        manager = HookManager(config=config, working_dir=tmp_working_dir)
        results = manager.run_session_start()

        assert len(results) == 1
        assert results[0].success
        assert marker_file.exists()

    def test_run_stop_blocked_means_continue(self, tmp_working_dir, tmp_path):
        """Test that blocking Stop hook means agent should continue."""
        script = tmp_path / "block_stop.sh"
        script.write_text('#!/bin/bash\necho \'{"decision": "deny"}\'\nexit 2')
        script.chmod(0o755)

        hook = {"type": "command", "command": str(script)}
        config = HookConfig.from_dict(
            {"hooks": {"Stop": [{"matcher": "*", "hooks": [hook]}]}}
        )

        manager = HookManager(config=config, working_dir=tmp_working_dir)
        should_stop, results = manager.run_stop()

        assert not should_stop  # Blocking means don't stop (continue)

    def test_get_blocking_reason(self, tmp_working_dir):
        """Test get_blocking_reason extracts reason from results."""
        from openhands.sdk.hooks.executor import HookResult

        manager = HookManager(config=HookConfig(), working_dir=tmp_working_dir)

        # With reason field
        results = [HookResult(blocked=True, reason="Custom reason")]
        assert manager.get_blocking_reason(results) == "Custom reason"

        # With stderr
        results = [HookResult(blocked=True, stderr="Error from stderr\n")]
        assert manager.get_blocking_reason(results) == "Error from stderr"

        # Default message
        results = [HookResult(blocked=True)]
        assert manager.get_blocking_reason(results) == "Blocked by hook"

        # Not blocked
        results = [HookResult(success=True)]
        assert manager.get_blocking_reason(results) is None
