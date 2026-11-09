"""Tests for hooks service."""

import json
import tempfile
from pathlib import Path

from openhands.agent_server.hooks_service import load_hooks


class TestLoadProjectHooks:
    """Tests for loading project hooks only."""

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

            result = load_hooks(project_dir=tmpdir, load_user_hooks=False)

            assert result is not None
            assert not result.is_empty()
            assert len(result.stop) == 1

    def test_load_hooks_file_not_found(self):
        """Test loading hooks when hooks.json does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_hooks(project_dir=tmpdir, load_user_hooks=False)
            assert result is None

    def test_load_hooks_no_project_dir(self):
        """Test loading hooks with no project_dir provided."""
        result = load_hooks(project_dir=None, load_user_hooks=False)
        assert result is None

    def test_load_hooks_empty_hooks(self):
        """Test loading hooks when hooks.json is empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with empty content
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"
            hooks_file.write_text("{}")

            result = load_hooks(project_dir=tmpdir, load_user_hooks=False)
            assert result is None

    def test_load_hooks_invalid_json(self):
        """Test loading hooks when hooks.json contains invalid JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create .openhands/hooks.json with invalid JSON
            openhands_dir = Path(tmpdir) / ".openhands"
            openhands_dir.mkdir()
            hooks_file = openhands_dir / "hooks.json"
            hooks_file.write_text("not valid json {")

            result = load_hooks(project_dir=tmpdir, load_user_hooks=False)
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

            result = load_hooks(project_dir=tmpdir, load_user_hooks=False)

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

            result = load_hooks(project_dir=tmpdir, load_user_hooks=False)

            assert result is not None
            assert not result.is_empty()
            assert len(result.stop) == 1
            assert len(result.pre_tool_use) == 1


def _write_hooks(path: Path, command: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "stop": [
                    {"matcher": "*", "hooks": [{"type": "command", "command": command}]}
                ]
            }
        )
    )


class TestLoadHooks:
    """Tests for load_hooks, which merges project and user hooks."""

    def test_merges_project_then_user(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        project = tmp_path / "project"
        _write_hooks(project / ".openhands" / "hooks.json", "echo project")
        _write_hooks(user_dir / "hooks.json", "echo user")

        result = load_hooks(project_dir=str(project))

        assert result is not None
        assert [m.hooks[0].command for m in result.stop] == [
            "echo project",
            "echo user",
        ]

    def test_user_hooks_without_project_dir(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        _write_hooks(user_dir / "hooks.json", "echo user")

        result = load_hooks(project_dir=None)

        assert result is not None
        assert result.stop[0].hooks[0].command == "echo user"

    def test_flags_select_sources(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        project = tmp_path / "project"
        _write_hooks(project / ".openhands" / "hooks.json", "echo project")
        _write_hooks(user_dir / "hooks.json", "echo user")

        only_project = load_hooks(project_dir=str(project), load_user_hooks=False)
        only_user = load_hooks(project_dir=str(project), load_project_hooks=False)

        assert only_project is not None and only_user is not None
        assert [m.hooks[0].command for m in only_project.stop] == ["echo project"]
        assert [m.hooks[0].command for m in only_user.stop] == ["echo user"]
        assert (
            load_hooks(
                project_dir=str(project),
                load_project_hooks=False,
                load_user_hooks=False,
            )
            is None
        )

    def test_project_dir_that_is_home_reads_file_once(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(home / ".openhands"))
        _write_hooks(home / ".openhands" / "hooks.json", "echo once")

        result = load_hooks(project_dir=str(home))

        assert result is not None
        assert len(result.stop) == 1

    def test_invalid_user_file_keeps_project_hooks(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        project = tmp_path / "project"
        _write_hooks(project / ".openhands" / "hooks.json", "echo project")
        user_dir.mkdir()
        (user_dir / "hooks.json").write_text("{not json")

        result = load_hooks(project_dir=str(project))

        assert result is not None
        assert [m.hooks[0].command for m in result.stop] == ["echo project"]

    def test_load_user_hooks_false_ignores_user_hooks(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        _write_hooks(user_dir / "hooks.json", "echo user")

        assert load_hooks(project_dir=str(tmp_path), load_user_hooks=False) is None

    def test_symlink_loop_project_file_keeps_user_hooks(self, tmp_path, monkeypatch):
        user_dir = tmp_path / "user"
        monkeypatch.setenv("OH_PERSISTENCE_DIR", str(user_dir))
        _write_hooks(user_dir / "hooks.json", "echo user")
        loop = tmp_path / "project" / ".openhands" / "hooks.json"
        loop.parent.mkdir(parents=True)
        loop.symlink_to(loop)

        # Python 3.12 raises RuntimeError from resolve() on a loop; 3.13 does
        # not, so raise it here to cover both.
        real_resolve = Path.resolve

        def resolve(self, strict=False):
            if self == loop:
                raise RuntimeError(f"Symlink loop from {self!r}")
            return real_resolve(self, strict)

        monkeypatch.setattr(Path, "resolve", resolve)

        result = load_hooks(project_dir=str(tmp_path / "project"))

        assert result is not None
        assert [m.hooks[0].command for m in result.stop] == ["echo user"]
