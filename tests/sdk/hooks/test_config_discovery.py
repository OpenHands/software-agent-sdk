"""Tests for discovering `hooks.json` across the supported config directories."""

import json
from pathlib import Path

import pytest

from openhands.sdk.hooks.config import HOOK_CONFIG_DIRS, HookConfig, find_hooks_file
from openhands.sdk.hooks.types import HookEventType


def write_hooks(base_dir: Path, directory: str, command: str) -> Path:
    """Write a minimal hooks.json under `base_dir/directory`."""
    hooks_dir = base_dir / directory
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_file = hooks_dir / "hooks.json"
    matcher = {"matcher": "*", "hooks": [{"command": command}]}
    hooks_file.write_text(json.dumps({"hooks": {"PreToolUse": [matcher]}}))
    return hooks_file


class TestFindHooksFile:
    def test_returns_none_when_no_config_exists(self, tmp_path: Path):
        assert find_hooks_file(tmp_path) is None

    @pytest.mark.parametrize("directory", HOOK_CONFIG_DIRS)
    def test_finds_hooks_in_each_supported_directory(
        self, tmp_path: Path, directory: str
    ):
        expected = write_hooks(tmp_path, directory, "run.sh")
        assert find_hooks_file(tmp_path) == expected

    def test_prefers_agents_over_the_legacy_openhands_directory(self, tmp_path: Path):
        expected = write_hooks(tmp_path, ".agents", "agents.sh")
        write_hooks(tmp_path, ".openhands", "openhands.sh")

        assert find_hooks_file(tmp_path) == expected

    def test_accepts_a_string_base_dir(self, tmp_path: Path):
        expected = write_hooks(tmp_path, ".agents", "run.sh")
        assert find_hooks_file(str(tmp_path)) == expected


class TestLoadDiscovery:
    @pytest.mark.parametrize("directory", HOOK_CONFIG_DIRS)
    def test_load_discovers_each_supported_directory(
        self, tmp_path: Path, directory: str
    ):
        write_hooks(tmp_path, directory, f"{directory}.sh")

        config = HookConfig.load(working_dir=tmp_path)

        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert [hook.command for hook in hooks] == [f"{directory}.sh"]

    def test_load_does_not_merge_across_directories(self, tmp_path: Path):
        """Only the preferred directory is read — configs are not combined."""
        write_hooks(tmp_path, ".agents", "agents.sh")
        write_hooks(tmp_path, ".openhands", "openhands.sh")

        config = HookConfig.load(working_dir=tmp_path)

        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert [hook.command for hook in hooks] == ["agents.sh"]

    def test_explicit_path_still_wins_over_discovery(self, tmp_path: Path):
        write_hooks(tmp_path, ".agents", "discovered.sh")
        explicit = write_hooks(tmp_path / "elsewhere", ".agents", "explicit.sh")

        config = HookConfig.load(path=explicit, working_dir=tmp_path)

        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert [hook.command for hook in hooks] == ["explicit.sh"]

    def test_falls_back_to_the_home_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        write_hooks(home_dir, ".agents", "user.sh")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))

        config = HookConfig.load(working_dir=project_dir)

        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert [hook.command for hook in hooks] == ["user.sh"]

    def test_project_config_wins_over_the_home_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        # The project uses the least-preferred directory and the home dir uses
        # the most-preferred one: project scope must still win.
        write_hooks(project_dir, ".openhands", "project.sh")
        write_hooks(home_dir, ".agents", "user.sh")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))

        config = HookConfig.load(working_dir=project_dir)

        hooks = config.get_hooks_for_event(HookEventType.PRE_TOOL_USE, "AnyTool")
        assert [hook.command for hook in hooks] == ["project.sh"]

    def test_returns_empty_config_when_nothing_is_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        home_dir = tmp_path / "home"
        home_dir.mkdir()
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home_dir))

        assert HookConfig.load(working_dir=tmp_path).is_empty()
