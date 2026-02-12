"""Tests for hooks service."""

import json
import tempfile
from pathlib import Path

from openhands.agent_server.hooks_service import load_hooks_from_workspace


class TestLoadHooksFromWorkspace:
    """Tests for load_hooks_from_workspace function."""

    def test_load_hooks_success(self):
        """Test loading hooks from a valid hooks.json file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"

            hooks_data = {
                "hooks": {
                    "stop": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "echo 'stop hook'"}
                            ],
                        }
                    ]
                }
            }
            hooks_file.write_text(json.dumps(hooks_data))

            result = load_hooks_from_workspace(project_dir=tmpdir)

            assert result is not None
            assert not result.is_empty()
            assert len(result.stop) == 1

    def test_load_hooks_file_not_found(self):
        """Test loading hooks when hooks.json does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_hooks_from_workspace(project_dir=tmpdir)
            assert result is None

    def test_load_hooks_no_project_dir(self):
        """Test loading hooks with no project_dir provided."""
        result = load_hooks_from_workspace(project_dir=None)
        assert result is None

    def test_load_hooks_empty_hooks(self):
        """Test loading hooks when hooks.json is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with empty content
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"
            hooks_file.write_text("{}")

            result = load_hooks_from_workspace(project_dir=tmpdir)
            assert result is None

    def test_load_hooks_invalid_json(self):
        """Test loading hooks when hooks.json contains invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with invalid JSON
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"
            hooks_file.write_text("not valid json {")

            result = load_hooks_from_workspace(project_dir=tmpdir)
            assert result is None

    def test_load_hooks_multiple_event_types(self):
        """Test loading hooks with multiple event types."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with multiple event types
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"

            hooks_data = {
                "hooks": {
                    "stop": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo 'stop'"}],
                        }
                    ],
                    "pre_tool_use": [
                        {
                            "matcher": "terminal",
                            "hooks": [
                                {"type": "command", "command": "echo 'pre_tool_use'"}
                            ],
                        }
                    ],
                }
            }
            hooks_file.write_text(json.dumps(hooks_data))

            result = load_hooks_from_workspace(project_dir=tmpdir)

            assert result is not None
            assert not result.is_empty()
            assert len(result.stop) == 1
            assert len(result.pre_tool_use) == 1

    def test_load_hooks_pascal_case_format(self):
        """Test loading hooks with PascalCase event names (legacy format)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with PascalCase format
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"

            hooks_data = {
                "hooks": {
                    "Stop": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "echo 'stop'"}],
                        }
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [
                                {"type": "command", "command": "echo 'pre_tool_use'"}
                            ],
                        }
                    ],
                }
            }
            hooks_file.write_text(json.dumps(hooks_data))

            result = load_hooks_from_workspace(project_dir=tmpdir)

            assert result is not None
            assert not result.is_empty()
            assert len(result.stop) == 1
            assert len(result.pre_tool_use) == 1
