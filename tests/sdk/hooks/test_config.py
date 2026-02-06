"""Tests for hook configuration loading and management."""

import json
import os
import tempfile

from openhands.sdk.hooks.config import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.hooks.types import HookEventType


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


class TestMultipleWorkingDirs:
    """Tests for loading hooks from multiple working directories."""

    def test_load_with_list_of_working_dirs(self):
        """Test that load() accepts a list of working directories."""
        with (
            tempfile.TemporaryDirectory() as tmpdir1,
            tempfile.TemporaryDirectory() as tmpdir2,
        ):
            # Create hooks.json in first directory
            openhands_dir1 = os.path.join(tmpdir1, ".openhands")
            os.makedirs(openhands_dir1)
            with open(os.path.join(openhands_dir1, "hooks.json"), "w") as f:
                json.dump({"PreToolUse": [{"hooks": [{"command": "hook1.sh"}]}]}, f)

            # Create hooks.json in second directory
            openhands_dir2 = os.path.join(tmpdir2, ".openhands")
            os.makedirs(openhands_dir2)
            with open(os.path.join(openhands_dir2, "hooks.json"), "w") as f:
                json.dump({"PostToolUse": [{"hooks": [{"command": "hook2.sh"}]}]}, f)

            # Load from both directories
            config = HookConfig.load(working_dir=[tmpdir1, tmpdir2])

            # Should have hooks from both directories
            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
            assert config.has_hooks_for_event(HookEventType.POST_TOOL_USE)

            pre_hooks = config.get_hooks_for_event(
                HookEventType.PRE_TOOL_USE, "AnyTool"
            )
            assert len(pre_hooks) == 1
            assert pre_hooks[0].command == "hook1.sh"

            post_hooks = config.get_hooks_for_event(
                HookEventType.POST_TOOL_USE, "AnyTool"
            )
            assert len(post_hooks) == 1
            assert post_hooks[0].command == "hook2.sh"

    def test_load_merges_hooks_from_multiple_dirs(self):
        """Test that hooks from multiple directories are merged."""
        with (
            tempfile.TemporaryDirectory() as tmpdir1,
            tempfile.TemporaryDirectory() as tmpdir2,
        ):
            # Create PreToolUse hook in first directory
            openhands_dir1 = os.path.join(tmpdir1, ".openhands")
            os.makedirs(openhands_dir1)
            with open(os.path.join(openhands_dir1, "hooks.json"), "w") as f:
                json.dump({"PreToolUse": [{"hooks": [{"command": "hook1.sh"}]}]}, f)

            # Create another PreToolUse hook in second directory
            openhands_dir2 = os.path.join(tmpdir2, ".openhands")
            os.makedirs(openhands_dir2)
            with open(os.path.join(openhands_dir2, "hooks.json"), "w") as f:
                json.dump({"PreToolUse": [{"hooks": [{"command": "hook2.sh"}]}]}, f)

            # Load from both directories
            config = HookConfig.load(working_dir=[tmpdir1, tmpdir2])

            # Should have both hooks merged
            hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
            assert len(hooks) == 2
            commands = [h.command for h in hooks]
            assert "hook1.sh" in commands
            assert "hook2.sh" in commands

    def test_load_with_empty_list_returns_empty_config(self):
        """Test that load() with empty list returns empty config."""
        config = HookConfig.load(working_dir=[])
        assert config.is_empty()

    def test_load_with_single_item_list(self):
        """Test that load() works with a single-item list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)
            with open(os.path.join(openhands_dir, "hooks.json"), "w") as f:
                json.dump({"PreToolUse": [{"hooks": [{"command": "hook.sh"}]}]}, f)

            # Load with single-item list
            config = HookConfig.load(working_dir=[tmpdir])

            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)

    def test_load_with_nonexistent_dirs_in_list(self):
        """Test that load() handles nonexistent directories in the list gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            openhands_dir = os.path.join(tmpdir, ".openhands")
            os.makedirs(openhands_dir)
            with open(os.path.join(openhands_dir, "hooks.json"), "w") as f:
                json.dump({"PreToolUse": [{"hooks": [{"command": "hook.sh"}]}]}, f)

            # Load with mix of existing and nonexistent directories
            config = HookConfig.load(working_dir=["/nonexistent/path", tmpdir])

            # Should still load hooks from the existing directory
            assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)


class TestHomeDirDeduplication:
    """Tests for preventing duplicate loading of ~/.openhands/hooks.json."""

    def test_home_dir_not_loaded_twice_with_multiple_working_dirs(
        self, tmp_path, monkeypatch
    ):
        """Test ~/.openhands/hooks.json is only loaded once with multiple dirs."""
        # Create a fake home directory with hooks.json
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        fake_openhands = fake_home / ".openhands"
        fake_openhands.mkdir()
        hooks_file = fake_openhands / "hooks.json"
        hooks_file.write_text(
            json.dumps({"PreToolUse": [{"hooks": [{"command": "home-hook.sh"}]}]})
        )

        # Monkeypatch Path.home() to return our fake home
        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

        # Create two working directories without hooks
        work_dir1 = tmp_path / "work1"
        work_dir1.mkdir()
        work_dir2 = tmp_path / "work2"
        work_dir2.mkdir()

        # Load from both working directories
        config = HookConfig.load(working_dir=[str(work_dir1), str(work_dir2)])

        # Should have exactly one hook from home directory (not duplicated)
        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert len(hooks) == 1
        assert hooks[0].command == "home-hook.sh"

    def test_home_dir_merged_with_working_dir_hooks(self, tmp_path, monkeypatch):
        """Test that home dir hooks are merged with working dir hooks."""
        # Create a fake home directory with hooks.json
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        fake_openhands = fake_home / ".openhands"
        fake_openhands.mkdir()
        home_hooks_file = fake_openhands / "hooks.json"
        home_hooks_file.write_text(
            json.dumps({"PreToolUse": [{"hooks": [{"command": "home-hook.sh"}]}]})
        )

        # Monkeypatch Path.home() to return our fake home
        monkeypatch.setattr("pathlib.Path.home", lambda: fake_home)

        # Create a working directory with its own hooks
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        work_openhands = work_dir / ".openhands"
        work_openhands.mkdir()
        work_hooks_file = work_openhands / "hooks.json"
        work_hooks_file.write_text(
            json.dumps({"PreToolUse": [{"hooks": [{"command": "work-hook.sh"}]}]})
        )

        # Load from working directory
        config = HookConfig.load(working_dir=str(work_dir))

        # Should have both hooks merged
        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert len(hooks) == 2
        commands = [h.command for h in hooks]
        assert "work-hook.sh" in commands
        assert "home-hook.sh" in commands
