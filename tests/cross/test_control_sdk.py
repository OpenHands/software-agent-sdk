"""Tests for the verify-sdk skill's control-sdk driver.

The driver lives at `.agents/skills/verify-sdk/scripts/control_sdk.py`. These
tests drive a real, scripted conversation through it end to end (the file
editor tool only, so no tmux is needed) and cover its checks and argument
handling.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/skills/verify-sdk/scripts/control_sdk.py"


def _load():
    spec = importlib.util.spec_from_file_location("control_sdk", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["control_sdk"] = module
    spec.loader.exec_module(module)
    return module


control_sdk = _load()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private SDK_VERIFY_HOME; restores the cwd and environment the
    in-process verbs change."""
    monkeypatch.setenv("SDK_VERIFY_HOME", str(tmp_path / "verify"))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("SDK_VERIFY_RUN", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    saved_env, saved_cwd = dict(os.environ), os.getcwd()
    yield tmp_path
    os.chdir(saved_cwd)
    os.environ.clear()
    os.environ.update(saved_env)


def _cli(capsys, *argv):
    code = control_sdk.main(list(argv))
    out = capsys.readouterr().out
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out.strip()


def test_checks_cover_every_operator():
    body = {
        "a": 3,
        "s": "hello",
        "l": ["x", "yz"],
        "n": None,
        "t": True,
        "d": {"k.dotted": 1},
    }
    cases = [
        (["a", "eq", "3"], True),
        (["a", "ne", "3"], False),
        (["a", "gt", "2"], True),
        (["a", "le", "2"], False),
        (["s", "contains", "ell"], True),
        (["l", "contains", "yz"], True),
        (["l", "not-contains", "q"], True),
        (["s", "matches", "^h.l"], True),
        (["n", "missing"], True),
        (["s", "exists"], True),
        (["l", "len-eq", "2"], True),
        (["l.1", "eq", "yz"], True),
        (["l.-1", "eq", "yz"], True),
        (["t", "eq", "true"], True),
        ([r"d.k\.dotted", "eq", "1"], True),
    ]
    for spec, expected in cases:
        assert control_sdk.evaluate_check(body, spec)["ok"] is expected, spec


def test_send_run_means_then_run_but_run_with_a_path_names_the_run():
    argv = ["conversation", "send", "C", "--text", "hi", "--run"]
    assert control_sdk.normalize_argv(argv)[-1] == "--then-run"
    argv = ["conversation", "send", "C", "--run", "/tmp/r", "--text", "hi"]
    assert control_sdk.normalize_argv(argv) == argv


def test_a_scripted_conversation_persists_across_commands(home, capsys):
    code, run = _cli(capsys, "new", "--field", "run")
    assert code == 0, run
    os.environ["SDK_VERIFY_RUN"] = run
    script = json.dumps(
        [
            {
                "tool": "file_editor",
                "args": {
                    "command": "create",
                    "path": f"{run}/workspace/notes.txt",
                    "file_text": "alpha\n",
                },
            },
            {"finish": "created notes.txt"},
        ]
    )
    code, cid = _cli(
        capsys,
        "conversation",
        "new",
        "--tools",
        "file_editor",
        "--script-json",
        script,
        "--field",
        "id",
    )
    assert code == 0, cid
    code, out = _cli(
        capsys,
        "conversation",
        "send",
        cid,
        "--text",
        "write notes",
        "--run",
        "--check",
        "execution_status",
        "eq",
        "finished",
        "--check",
        "last_agent_message",
        "eq",
        "created notes.txt",
    )
    assert code == 0, out
    assert (Path(run) / "workspace/notes.txt").read_text() == "alpha\n"
    # A new process (here: a new call) resumes the saved conversation.
    code, out = _cli(capsys, "conversation", "state", cid)
    assert out["script_left"] == 0
    assert out["event_files"] == out["event_count"] >= 5
    code, out = _cli(capsys, "conversation", "events", cid, "--kind", "ActionEvent")
    assert [e["tool"] for e in out["events"]] == ["file_editor", "finish"]
    # A failing check exits 1 and shows the actual value.
    code, out = _cli(
        capsys, "conversation", "state", cid, "--check", "event_count", "eq", "0"
    )
    assert code == 1
    assert out["checks"][0]["ok"] is False


def test_a_real_model_without_its_key_is_blocked(home, capsys):
    code, run = _cli(capsys, "new", "--llm", "deepseek-flash", "--field", "run")
    os.environ["SDK_VERIFY_RUN"] = run
    code, out = _cli(capsys, "exec", "--model-env", "--", "true")
    assert code == control_sdk.BLOCKED_EXIT
    assert "DEEPSEEK_API_KEY" in out["error"]


def test_exec_scrubs_the_callers_model_and_openhands_settings(home, capsys):
    code, run = _cli(capsys, "new", "--field", "run")
    os.environ.update({"SDK_VERIFY_RUN": run, "LLM_API_KEY": "leak", "OH_X": "leak"})
    code, out = _cli(
        capsys,
        "exec",
        "--",
        "python",
        "-c",
        "import json, os; print(json.dumps({k: os.environ.get(k) for k in"
        " ('LLM_API_KEY', 'OH_X', 'HOME', 'OH_PERSISTENCE_DIR')}))",
    )
    assert code == 0, out
    seen = out["result"]
    assert seen["LLM_API_KEY"] is None and seen["OH_X"] is None
    assert seen["HOME"] == f"{run}/home"
    assert seen["OH_PERSISTENCE_DIR"] == f"{run}/openhands"
