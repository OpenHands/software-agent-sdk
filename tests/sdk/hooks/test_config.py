"""Tests for hook configuration loading and management."""

import json
import os
import tempfile

from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.hooks.types import HookEventType
from openhands.sdk.hooks.utils import discover_hook_scripts, load_project_hooks


class TestHookMatcher:
    """Tests for HookMatcher pattern matching."""

    def test_wildcard_matches_all(self):
        """Test that * matches all tool names."""
        matcher = HookMatcher(matcher="*")
        assert matcher.matches("BashTool")
        assert matcher.matches("FileEditorTool")
        assert matcher.matches(None)

    def test_exact_match(self):
        """Test exact string matching."""
        matcher = HookMatcher(matcher="BashTool")
        assert matcher.matches("BashTool")
        assert not matcher.matches("FileEditorTool")

    def test_regex_match_with_delimiters(self):
        """Test regex pattern matching with explicit /pattern/ delimiters."""
        matcher = HookMatcher(matcher="/.*Tool$/")
        assert matcher.matches("BashTool")
        assert matcher.matches("FileEditorTool")
        assert not matcher.matches("BashCommand")

    def test_regex_match_auto_detect(self):
        """Test regex auto-detection (bare regex without delimiters)."""
        # Pipe character triggers regex mode
        matcher = HookMatcher(matcher="Edit|Write")
        assert matcher.matches("Edit")
        assert matcher.matches("Write")
        assert not matcher.matches("Read")
        assert not matcher.matches("EditWrite")

        # Wildcard pattern
        matcher2 = HookMatcher(matcher="Bash.*")
        assert matcher2.matches("BashTool")
        assert matcher2.matches("BashCommand")
        assert not matcher2.matches("ShellTool")

    def test_empty_matcher_matches_all(self):
        """Test that empty string matcher matches all tools."""
        matcher = HookMatcher(matcher="")
        assert matcher.matches("BashTool")
        assert matcher.matches(None)


class TestHookConfig:
    """Tests for HookConfig loading and management."""

    def test_load_from_dict(self):
        """Test loading config from dictionary."""
        data = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "BashTool",
                        "hooks": [{"type": "command", "command": "echo pre-hook"}],
                    }
                ]
            }
        }
        config = HookConfig.from_dict(data)
        assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "BashTool")
        assert len(hooks) == 1
        assert hooks[0].command == "echo pre-hook"

    def test_load_from_json_file(self):
        """Test loading config from JSON file."""
        hook = {"type": "command", "command": "logger.sh", "timeout": 30}
        data = {"hooks": {"PostToolUse": [{"matcher": "*", "hooks": [hook]}]}}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()
            config = HookConfig.load(f.name)

        assert config.has_hooks_for_event(HookEventType.POST_TOOL_USE)
        hooks = config.get_hooks_for_event(HookEventType.POST_TOOL_USE, "AnyTool")
        assert len(hooks) == 1
        assert hooks[0].timeout == 30

    def test_load_missing_file_returns_empty(self):
        """Test that loading missing file returns empty config."""
        config = HookConfig.load("/nonexistent/path/hooks.json")
        assert config.is_empty()

    def test_load_discovers_config_in_working_dir(self):
        """Test that load() discovers .openhands/hooks.json in working_dir."""
        hook = {"type": "command", "command": "test-hook.sh"}
        data = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [hook]}]}}

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json in the working directory
            import os

            hooks_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(hooks_dir)
            hooks_file = os.path.join(hooks_dir, "hooks.json")
            with open(hooks_file, "w") as f:
                json.dump(data, f)

            # Load using working_dir (NOT cwd)
            config = HookConfig.load(working_dir=tmpdir)

            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
            hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
            assert len(hooks) == 1
            assert hooks[0].command == "test-hook.sh"

    def test_get_hooks_filters_by_tool_name(self):
        """Test that hooks are filtered by tool name."""
        data = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "BashTool",
                        "hooks": [{"type": "command", "command": "bash-hook.sh"}],
                    },
                    {
                        "matcher": "FileEditorTool",
                        "hooks": [{"type": "command", "command": "file-hook.sh"}],
                    },
                ]
            }
        }
        config = HookConfig.from_dict(data)

        bash_hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "BashTool")
        assert len(bash_hooks) == 1
        assert bash_hooks[0].command == "bash-hook.sh"

        file_hooks = config.get_hooks_for_event(
            HookEventType.PRE_TOOL_USE, "FileEditorTool"
        )
        assert len(file_hooks) == 1
        assert file_hooks[0].command == "file-hook.sh"

    def test_typed_field_instantiation(self):
        """Test creating HookConfig with typed fields (recommended approach)."""
        config = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="terminal",
                    hooks=[HookDefinition(command="block.sh", timeout=10)],
                )
            ],
            post_tool_use=[HookMatcher(hooks=[HookDefinition(command="log.sh")])],
        )

        assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
        assert config.has_hooks_for_event(HookEventType.POST_TOOL_USE)
        assert not config.has_hooks_for_event(HookEventType.STOP)

        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "terminal")
        assert len(hooks) == 1
        assert hooks[0].command == "block.sh"
        assert hooks[0].timeout == 10

    def test_json_round_trip(self):
        """Test that model_dump produces JSON-compatible output for round-trip."""
        config = HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="terminal",
                    hooks=[HookDefinition(command="test.sh")],
                )
            ]
        )

        # model_dump should produce snake_case format
        output = config.model_dump(mode="json", exclude_defaults=True)
        assert "pre_tool_use" in output
        assert output["pre_tool_use"][0]["matcher"] == "terminal"
        assert output["pre_tool_use"][0]["hooks"][0]["command"] == "test.sh"

        # Should be able to reload from the output
        reloaded = HookConfig.model_validate(output)
        assert reloaded.pre_tool_use == config.pre_tool_use

    def test_is_empty(self):
        """Test is_empty() correctly identifies empty configs."""
        empty_config = HookConfig()
        assert empty_config.is_empty()

        non_empty_config = HookConfig(
            pre_tool_use=[HookMatcher(hooks=[HookDefinition(command="a.sh")])],
        )
        assert not non_empty_config.is_empty()

    def test_legacy_format_is_still_supported(self):
        """Test that legacy format remains supported without warnings."""
        import warnings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cfg = HookConfig.from_dict(
                {"hooks": {"PreToolUse": [{"hooks": [{"command": "test.sh"}]}]}}
            )

        assert len(w) == 0
        assert cfg.pre_tool_use[0].hooks[0].command == "test.sh"

    def test_duplicate_keys_raises_error(self):
        """Test that providing both PascalCase and snake_case raises error."""
        import pytest

        with pytest.raises(ValueError, match="Duplicate hook event"):
            HookConfig.from_dict(
                {
                    "PreToolUse": [{"hooks": [{"command": "a.sh"}]}],
                    "pre_tool_use": [{"hooks": [{"command": "b.sh"}]}],
                }
            )

    def test_unknown_event_type_raises_error(self):
        """Test that typos in event types raise helpful errors."""
        import pytest

        with pytest.raises(ValueError, match="Unknown event type.*PreToolExecute"):
            HookConfig.from_dict(
                {"PreToolExecute": [{"hooks": [{"command": "test.sh"}]}]}
            )

    def test_load_discovers_agent_finish_script(self):
        """Test that load() discovers .openhands/agent_finish.sh (legacy naming)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)
            finish_script = os.path.join(openhands_dir, "agent_finish.sh")
            with open(finish_script, "w") as f:
                f.write("#!/bin/bash\necho done\n")

            config = HookConfig.load(working_dir=tmpdir)

            assert config.has_hooks_for_event(HookEventType.STOP)
            hooks = config.get_hooks_for_event(HookEventType.STOP, None)
            assert len(hooks) == 1
            assert hooks[0].command == "bash .openhands/agent_finish.sh || true"
            assert hooks[0].timeout == 600

    def test_load_discovers_stop_script(self):
        """Test that load() discovers .openhands/stop.sh (standard naming)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)
            stop_script = os.path.join(openhands_dir, "stop.sh")
            with open(stop_script, "w") as f:
                f.write("#!/bin/bash\necho stopping\n")

            config = HookConfig.load(working_dir=tmpdir)

            assert config.has_hooks_for_event(HookEventType.STOP)
            hooks = config.get_hooks_for_event(HookEventType.STOP, None)
            assert len(hooks) == 1
            assert hooks[0].command == "bash .openhands/stop.sh || true"

    def test_load_discovers_scripts_in_hooks_subdir(self):
        """Test that load() discovers scripts in .openhands/hooks/ subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = os.path.join(tmpdir, ".openhands", "hooks")
            os.makedirs(hooks_dir)
            pre_tool_script = os.path.join(hooks_dir, "pre_tool_use.sh")
            with open(pre_tool_script, "w") as f:
                f.write("#!/bin/bash\necho pre-tool\n")

            config = HookConfig.load(working_dir=tmpdir)

            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
            hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
            assert len(hooks) == 1
            assert hooks[0].command == "bash .openhands/hooks/pre_tool_use.sh || true"

    def test_load_merges_json_and_scripts(self):
        """Test that load() merges hooks.json with discovered scripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)

            # Create hooks.json with pre_tool_use hook
            hooks_json = os.path.join(openhands_dir, "hooks.json")
            data = {"PreToolUse": [{"hooks": [{"command": "json-hook.sh"}]}]}
            with open(hooks_json, "w") as f:
                json.dump(data, f)

            # Create stop.sh script
            stop_script = os.path.join(openhands_dir, "stop.sh")
            with open(stop_script, "w") as f:
                f.write("#!/bin/bash\necho stop\n")

            config = HookConfig.load(working_dir=tmpdir)

            # Should have both hooks
            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
            assert config.has_hooks_for_event(HookEventType.STOP)

            pre_hooks = config.get_hooks_for_event(
                HookEventType.PRE_TOOL_USE, "AnyTool"
            )
            assert len(pre_hooks) == 1
            assert pre_hooks[0].command == "json-hook.sh"

            stop_hooks = config.get_hooks_for_event(HookEventType.STOP, None)
            assert len(stop_hooks) == 1
            assert stop_hooks[0].command == "bash .openhands/stop.sh || true"


class TestLoadProjectHooks:
    """Tests for load_project_hooks utility function."""

    def test_load_project_hooks_no_openhands_dir(self):
        """Test load_project_hooks returns empty when .openhands doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_project_hooks(tmpdir)
            assert config.is_empty()

    def test_load_project_hooks_empty_openhands_dir(self):
        """Test load_project_hooks returns empty config when no scripts found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".openhands"))
            config = load_project_hooks(tmpdir)
            assert config.is_empty()

    def test_load_project_hooks_discovers_all_event_types(self):
        """Test load_project_hooks discovers scripts for all event types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)

            # Create scripts for multiple event types
            scripts = [
                "stop.sh",
                "pre_tool_use.sh",
                "post_tool_use.sh",
                "session_start.sh",
                "session_end.sh",
                "user_prompt_submit.sh",
            ]
            for script in scripts:
                with open(os.path.join(openhands_dir, script), "w") as f:
                    f.write(f"#!/bin/bash\necho {script}\n")

            config = load_project_hooks(tmpdir)

            assert config.has_hooks_for_event(HookEventType.STOP)
            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
            assert config.has_hooks_for_event(HookEventType.POST_TOOL_USE)
            assert config.has_hooks_for_event(HookEventType.SESSION_START)
            assert config.has_hooks_for_event(HookEventType.SESSION_END)
            assert config.has_hooks_for_event(HookEventType.USER_PROMPT_SUBMIT)

    def test_load_project_hooks_accepts_string_path(self):
        """Test load_project_hooks accepts string paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)
            with open(os.path.join(openhands_dir, "stop.sh"), "w") as f:
                f.write("#!/bin/bash\necho stop\n")

            # Pass string path instead of Path object
            config = load_project_hooks(str(tmpdir))
            assert config.has_hooks_for_event(HookEventType.STOP)


class TestDiscoverHookScripts:
    """Tests for discover_hook_scripts utility function."""

    def test_discover_hook_scripts_nonexistent_dir(self):
        """Test discover_hook_scripts returns empty dict for nonexistent directory."""
        from pathlib import Path

        result = discover_hook_scripts(Path("/nonexistent/path"))
        assert result == {}

    def test_discover_hook_scripts_finds_legacy_agent_finish(self):
        """Test discover_hook_scripts finds agent_finish.sh and maps to STOP."""
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            (openhands_dir / "agent_finish.sh").write_text("#!/bin/bash\necho done\n")

            result = discover_hook_scripts(openhands_dir)

            assert HookEventType.STOP in result
            assert len(result[HookEventType.STOP]) == 1
            assert result[HookEventType.STOP][0].name == "agent_finish.sh"

    def test_discover_hook_scripts_finds_scripts_in_hooks_subdir(self):
        """Test discover_hook_scripts searches .openhands/hooks/ subdirectory."""
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = Path(tmpdir) / ".openhands"
            hooks_subdir = openhands_dir / "hooks"
            hooks_subdir.mkdir(parents=True)
            (hooks_subdir / "pre_tool_use.sh").write_text("#!/bin/bash\necho pre\n")

            result = discover_hook_scripts(openhands_dir)

            assert HookEventType.PRE_TOOL_USE in result
            assert len(result[HookEventType.PRE_TOOL_USE]) == 1
            assert result[HookEventType.PRE_TOOL_USE][0].name == "pre_tool_use.sh"
