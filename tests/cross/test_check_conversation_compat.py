from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path

import pytest


def _load_script_module(name: str):
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / ".github" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_script = _load_script_module("check_conversation_compat")
FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "conversations" / "v1_49_4_mcp"
)


def _copy_fixture(tmp_path: Path) -> Path:
    state = json.loads((FIXTURE / "base_state.json").read_text())
    target = tmp_path / "conversations" / uuid.UUID(state["id"]).hex
    shutil.copytree(FIXTURE, target)
    return target


def _event_files(conversation_dir: Path) -> list[Path]:
    return sorted((conversation_dir / "events").glob("event-*.json"))


def test_resume_accepts_committed_fixture(tmp_path: Path) -> None:
    conversation_dir = _copy_fixture(tmp_path)

    count = _script.resume_conversation(conversation_dir, tmp_path / "workspace")

    assert count == len(_event_files(conversation_dir))


def test_resume_reports_missing_event_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        _script,
        "REQUIRED_EVENT_KINDS",
        _script.REQUIRED_EVENT_KINDS | {"CondensationEvent"},
    )

    with pytest.raises(_script.ConversationCompatError, match="CondensationEvent"):
        _script.resume_conversation(_copy_fixture(tmp_path), tmp_path / "workspace")


def test_resume_wraps_load_failures(tmp_path: Path) -> None:
    conversation_dir = _copy_fixture(tmp_path)
    system_prompt = _event_files(conversation_dir)[0]
    payload = json.loads(system_prompt.read_text())
    for tool in payload["tools"]:
        if "mcp_tool" in tool:
            tool["mcp_tool"] = {"name": "read_file"}
    system_prompt.write_text(json.dumps(payload))

    with pytest.raises(_script.ConversationCompatError, match="Resume failed"):
        _script.resume_conversation(conversation_dir, tmp_path / "workspace")


def test_save_fixture_replaces_machine_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_script.getpass, "getuser", lambda: "alice")
    monkeypatch.setattr(_script.socket, "gethostname", lambda: "box.example")
    root = tmp_path / "root"
    source = root / "conversations" / "abc"
    (source / "events").mkdir(parents=True)
    (source / "events" / ".eventlog.lock").write_text("")
    event = {
        "working_dir": f"{root}/workspace",
        "py_interpreter_path": f"{_script.REPO_ROOT}/.venv/bin/python",
        "home": f"{Path.home()}/cache",
        "username": "alice",
        "hostname": "box",
        "text": "alice's notes",
    }
    (source / "events" / "event-00000-a.json").write_text(json.dumps(event))
    destination = tmp_path / "fixture"

    _script.save_fixture(source, root, destination)

    saved = json.loads((destination / "events" / "event-00000-a.json").read_text())
    assert saved == {
        "working_dir": "/fixture/workspace",
        "py_interpreter_path": "/repo/.venv/bin/python",
        "home": "/home/fixture/cache",
        "username": "fixture-user",
        "hostname": "fixture-host",
        "text": "alice's notes",
    }
    assert not (destination / "events" / ".eventlog.lock").exists()
