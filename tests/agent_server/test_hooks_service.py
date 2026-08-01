"""Tests for hooks service."""

import json
import tempfile
from pathlib import Path

import pytest

from openhands.agent_server.hooks_service import load_hooks_from_workspace
from openhands.sdk.hooks import HOOK_CONFIG_DIRS


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


class TestWorkspaceHooksDiscovery:
    """The workspace loader must honour every supported config directory."""

    @staticmethod
    def write_hooks(base_dir: Path, directory: str, command: str) -> Path:
        hooks_dir = base_dir / directory
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hooks_file = hooks_dir / "hooks.json"
        matcher = {"matcher": "*", "hooks": [{"command": command}]}
        hooks_file.write_text(json.dumps({"hooks": {"stop": [matcher]}}))
        return hooks_file

    @pytest.mark.parametrize("directory", HOOK_CONFIG_DIRS)
    def test_loads_hooks_from_each_supported_directory(
        self, tmp_path: Path, directory: str
    ):
        self.write_hooks(tmp_path, directory, f"{directory}.sh")

        result = load_hooks_from_workspace(project_dir=str(tmp_path))

        assert result is not None
        assert [hook.command for hook in result.stop[0].hooks] == [f"{directory}.sh"]

    def test_prefers_agents_over_the_legacy_openhands_directory(self, tmp_path: Path):
        self.write_hooks(tmp_path, ".agents", "agents.sh")
        self.write_hooks(tmp_path, ".openhands", "openhands.sh")

        result = load_hooks_from_workspace(project_dir=str(tmp_path))

        assert result is not None
        assert [hook.command for hook in result.stop[0].hooks] == ["agents.sh"]

    def test_ignores_a_home_directory_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Workspace loading is project-scoped; user hooks are not picked up."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        self.write_hooks(home_dir, ".agents", "user.sh")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))

        assert load_hooks_from_workspace(project_dir=str(project_dir)) is None
