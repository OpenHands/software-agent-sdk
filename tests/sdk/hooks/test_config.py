"""Tests for hook configuration loading and management."""

import json
import tempfile
from pathlib import Path

import pytest

from openhands.sdk.hooks.config import (
    HookConfig,
    HookDefinition,
    HookMatcher,
    load_project_hooks,
    load_user_hooks,
)
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
        with pytest.raises(ValueError, match="Duplicate hook event"):
            HookConfig.from_dict(
                {
                    "PreToolUse": [{"hooks": [{"command": "a.sh"}]}],
                    "pre_tool_use": [{"hooks": [{"command": "b.sh"}]}],
                }
            )

    def test_unknown_event_type_raises_error(self):
        """Test that typos in event types raise helpful errors."""
        with pytest.raises(ValueError, match="Unknown event type.*PreToolExecute"):
            HookConfig.from_dict(
                {"PreToolExecute": [{"hooks": [{"command": "test.sh"}]}]}
            )


class TestLoadProjectHooks:
    """Tests for load_project_hooks function."""

    def test_load_project_hooks_from_working_dir(self, tmp_path):
        """Test loading hooks from project's .openhands/hooks.json."""
        hooks_dir = tmp_path / ".openhands"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "*", "hooks": [{"command": "project-hook.sh"}]}
                        ]
                    }
                }
            )
        )

        config = load_project_hooks(tmp_path)
        assert config is not None
        assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert len(hooks) == 1
        assert hooks[0].command == "project-hook.sh"

    def test_load_project_hooks_returns_none_when_missing(self, tmp_path):
        """Test that load_project_hooks returns None when file doesn't exist."""
        config = load_project_hooks(tmp_path)
        assert config is None

    def test_load_project_hooks_returns_none_for_empty_config(self, tmp_path):
        """Test that load_project_hooks returns None for empty hooks config."""
        hooks_dir = tmp_path / ".openhands"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps({"hooks": {}}))

        config = load_project_hooks(tmp_path)
        assert config is None

    def test_load_project_hooks_returns_none_for_malformed_json(self, tmp_path):
        """Test that load_project_hooks returns None for malformed JSON."""
        hooks_dir = tmp_path / ".openhands"
        hooks_dir.mkdir()
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text("{ invalid json }")

        config = load_project_hooks(tmp_path)
        assert config is None


class TestLoadUserHooks:
    """Tests for load_user_hooks function."""

    def test_load_user_hooks_from_home_dir(self, tmp_path, monkeypatch):
        """Test loading hooks from user's ~/.openhands/hooks.json."""
        fake_home = tmp_path / "fake_home"
        hooks_dir = fake_home / ".openhands"
        hooks_dir.mkdir(parents=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "*", "hooks": [{"command": "user-hook.sh"}]}
                        ]
                    }
                }
            )
        )

        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config = load_user_hooks()
        assert config is not None
        assert config.has_hooks_for_event(HookEventType.PRE_TOOL_USE)
        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert len(hooks) == 1
        assert hooks[0].command == "user-hook.sh"

    def test_load_user_hooks_returns_none_when_missing(self, tmp_path, monkeypatch):
        """Test that load_user_hooks returns None when file doesn't exist."""
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config = load_user_hooks()
        assert config is None

    def test_load_user_hooks_returns_none_for_empty_config(self, tmp_path, monkeypatch):
        """Test that load_user_hooks returns None for empty hooks config."""
        fake_home = tmp_path / "fake_home"
        hooks_dir = fake_home / ".openhands"
        hooks_dir.mkdir(parents=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text(json.dumps({"hooks": {}}))

        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config = load_user_hooks()
        assert config is None

    def test_load_user_hooks_returns_none_for_malformed_json(
        self, tmp_path, monkeypatch
    ):
        """Test that load_user_hooks returns None for malformed JSON."""
        fake_home = tmp_path / "fake_home"
        hooks_dir = fake_home / ".openhands"
        hooks_dir.mkdir(parents=True)
        hooks_file = hooks_dir / "hooks.json"
        hooks_file.write_text("{ invalid json }")

        monkeypatch.setattr(Path, "home", lambda: fake_home)

        config = load_user_hooks()
        assert config is None
