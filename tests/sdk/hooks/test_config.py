"""Tests for hook configuration loading and management."""

import json
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


class TestAsyncHooks:
    """Tests for async hook configuration."""

    def test_async_field_defaults_false(self):
        """Test that async defaults to False."""
        hook = HookDefinition(command="echo test")
        assert hook.async_ is False

    def test_async_field_set_true(self):
        """Test that async can be set to True using async alias."""
        hook = HookDefinition.model_validate({"command": "echo test", "async": True})
        assert hook.async_ is True

    def test_async_field_parsed_from_json_alias(self):
        """Test that 'async' key in JSON is parsed correctly via alias."""
        data = {
            "hooks": {
                "PostToolUse": [
                    {"matcher": "*", "hooks": [{"command": "test.sh", "async": True}]}
                ]
            }
        }
        config = HookConfig.from_dict(data)
        hooks = config.get_hooks_for_event(HookEventType.POST_TOOL_USE, "AnyTool")
        assert len(hooks) == 1
        assert hooks[0].async_ is True

    def test_async_field_serialization_by_alias(self):
        """Test that async field serializes correctly using alias."""
        hook = HookDefinition.model_validate({"command": "test.sh", "async": True})
        output = hook.model_dump(mode="json", by_alias=True)
        assert output["async"] is True
        assert "async_" not in output

    def test_async_field_serialization_without_alias(self):
        """Test that async field serializes as async_ without by_alias."""
        hook = HookDefinition.model_validate({"command": "test.sh", "async": True})
        output = hook.model_dump(mode="json")
        assert output["async_"] is True

    def test_async_hook_in_config_round_trip(self):
        """Test that async hooks survive a JSON round-trip."""
        data = {
            "PostToolUse": [
                {
                    "matcher": "terminal",
                    "hooks": [
                        {"command": "sync-hook.sh", "async": False},
                        {"command": "async-hook.sh", "async": True, "timeout": 30},
                    ],
                }
            ]
        }
        config = HookConfig.from_dict(data)
        hooks = config.get_hooks_for_event(HookEventType.POST_TOOL_USE, "terminal")

        assert len(hooks) == 2
        assert hooks[0].async_ is False
        assert hooks[1].async_ is True
        assert hooks[1].timeout == 30

    def test_multiple_async_hooks_across_events(self):
        """Test async hooks configured across multiple event types."""
        data = {
            "PostToolUse": [
                {"matcher": "*", "hooks": [{"command": "log.sh", "async": True}]}
            ],
            "SessionStart": [{"hooks": [{"command": "notify.sh", "async": True}]}],
        }
        config = HookConfig.from_dict(data)

        post_hooks = config.get_hooks_for_event(HookEventType.POST_TOOL_USE, "test")
        assert len(post_hooks) == 1
        assert post_hooks[0].async_ is True

        start_hooks = config.get_hooks_for_event(HookEventType.SESSION_START)
        assert len(start_hooks) == 1
        assert start_hooks[0].async_ is True
