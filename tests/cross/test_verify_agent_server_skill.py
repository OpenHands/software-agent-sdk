"""Tests for the verify-agent-server skill's control CLI and feature map.

The CLI lives at `.agents/skills/verify-agent-server/scripts/control_agent_server.py`.
These tests drive it against a tiny local HTTP server (via `attach`) and a
throwaway feature map, and validate the committed map's structure, without
launching an agent server.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agents/skills/verify-agent-server/scripts/control_agent_server.py"


def _load():
    spec = importlib.util.spec_from_file_location("control_agent_server", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["control_agent_server"] = module
    spec.loader.exec_module(module)
    return module


cas = _load()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = {
            "status": "ok",
            "uptime": 12.0,
            "items": [{"id": "first"}, {"id": "second"}],
            "echo_key": self.headers.get("X-Session-API-Key"),
        }
        if self.path.startswith("/missing"):
            self.send_response(404)
            body = {"detail": "Not Found"}
        else:
            self.send_response(200)
        payload = json.dumps(body).encode()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    """An attached run pointing at a local HTTP server, with a session key."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(cas.HOME_ENV, str(tmp_path / "runs"))
    monkeypatch.setenv("QA_TEST_SESSION_KEY", "qa-session-key-123456")
    monkeypatch.delenv(cas.RUN_ENV, raising=False)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    with pytest.raises(SystemExit):
        cas.main(
            ["attach", "--url", url, "--key-env", "QA_TEST_SESSION_KEY", "--print-run"]
        )
    run = next((tmp_path / "runs").iterdir())
    monkeypatch.setenv(cas.RUN_ENV, str(run))
    yield run
    server.shutdown()


def _run(capsys, *argv):
    code = cas.main(list(argv))
    out = capsys.readouterr().out
    return code, out


def test_api_checks_pass_and_fail_on_the_json_body(run_dir, capsys):
    code, out = _run(
        capsys,
        "api",
        "GET",
        "/info",
        "--check",
        "status",
        "eq",
        "ok",
        "--check",
        "uptime",
        "lt",
        "13",
        "--check",
        "items",
        "len-eq",
        "2",
        "--check",
        "missing.field",
        "missing",
    )
    assert code == 0, out
    code, out = _run(capsys, "api", "GET", "/info", "--check", "uptime", "gt", "13")
    assert code == 1
    assert json.loads(out)["checks"][0]["ok"] is False


def test_api_expect_status_and_field_chaining(run_dir, capsys):
    code, _ = _run(capsys, "api", "GET", "/missing", "--expect", "404")
    assert code == 0
    code, _ = _run(capsys, "api", "GET", "/missing")
    assert code == 1
    with pytest.raises(SystemExit) as exit_info:
        cas.main(["api", "GET", "/info", "--field", "items.1.id"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "second"


def test_session_key_never_reaches_output_or_evidence(run_dir, capsys):
    code, out = _run(capsys, "api", "GET", "/info", "--save", "F99.demo/info")
    assert code == 0
    assert "qa-session-key-123456" not in out
    assert "<redacted:session-api-key>" in out
    saved = Path(json.loads(out)["saved"])
    assert saved.is_relative_to(run_dir / "evidence" / "F99.demo")
    assert "qa-session-key-123456" not in saved.read_text()


FAMILY = """# Demo family

A family used by the CLI tests.

Source: `x`

Routes: none

## Sub-features

- `F98.first`: the first bullet sets a variable.
- `F98.second`: a later bullet sees it.
- `F98.bug`: a known bug fails as expected.
- `F98.offline`: needs something this machine lacks.

## How to get to it (agent POV)

- Nowhere.

## Driving it with control-agent-server

Preconditions:

- Nothing.

- **First (`F98.first`).** Set a value.
  ```sh
  VALUE=$(echo carried)
  ```
- **Second (`F98.second`).** Read it back.
  ```sh
  test "$VALUE" = carried
  control-agent-server api GET /info --check status eq ok
  ```
- **Broken thing (`F98.bug`), known bug.** Asserts the correct behavior.
  ```sh
  test "$VALUE" = carried
  test "$VALUE" = fixed  # bug
  ```
- **Offline (`F98.offline`), blocked: needs a GPU.** Not executed.

## Gotchas

- None.
"""


def test_map_run_carries_variables_and_reports_xfail_and_blocked(
    run_dir, capsys, tmp_path, monkeypatch
):
    feature_map = tmp_path / "map"
    feature_map.mkdir()
    (feature_map / "F98-demo.md").write_text(FAMILY)
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", feature_map)
    code, out = _run(capsys, "map", "run", "--file", "F98", "--record")
    result = json.loads(out)
    assert code == 0, out
    assert result["summary"]["pass"] == 2
    assert result["summary"]["xfail"] == 1
    assert result["summary"]["blocked"] == 1
    ledger = (run_dir / "evidence" / "ledger.jsonl").read_text().splitlines()
    assert {json.loads(row)["feature"] for row in ledger} == {
        "F98.first",
        "F98.second",
        "F98.bug",
        "F98.offline",
    }

    bug = next(r for r in result["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == "xfail"
    assert bug["failed_command"] == 'test "$VALUE" = fixed'

    fixed = FAMILY.replace(
        'test "$VALUE" = fixed  # bug', 'test "$VALUE" = carried  # bug'
    )
    (feature_map / "F98-demo.md").write_text(fixed)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    assert code == 1
    assert json.loads(out)["summary"]["xpass"] == 1

    broken_arrange = FAMILY.replace(
        'test "$VALUE" = carried\n  test "$VALUE" = fixed',
        'test "$VALUE" = other\n  test "$VALUE" = fixed',
    )
    (feature_map / "F98-demo.md").write_text(broken_arrange)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    result = json.loads(out)
    assert code == 1
    assert result["summary"]["xfail"] == 0
    assert result["summary"]["fail"] == 1


def test_map_check_rejects_bad_recipes(tmp_path, monkeypatch, capsys):
    feature_map = tmp_path / "map"
    feature_map.mkdir()
    broken = FAMILY.replace("--check status eq ok", "--bogus-flag")
    (feature_map / "F98-demo.md").write_text(broken)
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", feature_map)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    problems = json.loads(out)["problems"]["F98-demo.md"]
    assert code == 1
    assert any("--bogus-flag" in problem for problem in problems)


def test_map_check_accepts_backgrounded_commands_and_shell_values(
    tmp_path, monkeypatch, capsys
):
    feature_map = tmp_path / "map"
    feature_map.mkdir()
    recipe = (
        "  N=2\n"
        '  control-agent-server state ls "home/*" --expect-count "$N"\n'
        "  control-agent-server api GET /alive --auth none >/dev/null 2>&1 &\n"
        "  wait\n"
    )
    family = FAMILY.replace("  VALUE=$(echo carried)\n", recipe)
    (feature_map / "F98-demo.md").write_text(family)
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", feature_map)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    assert code == 0, out

    bad = family.replace('--expect-count "$N"', "--expect-count many")
    (feature_map / "F98-demo.md").write_text(bad)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    assert code == 1
    assert "many" in out


def test_sink_read_waits_for_requests_and_filters(run_dir, capsys):
    sink = run_dir / "fixtures" / "qa-sink.jsonl"
    sink.parent.mkdir(parents=True, exist_ok=True)
    sink.write_text(
        json.dumps({"path": "/events/a", "body": {"kind": "MessageEvent"}})
        + "\n"
        + json.dumps({"path": "/conversations", "body": {"status": "finished"}})
        + "\n"
    )
    code, out = _run(capsys, "sink", "read", "--name", "qa-sink", "--expect-min", "2")
    assert code == 0, out
    assert json.loads(out)["requests"] == 2

    code, out = _run(
        capsys,
        "sink",
        "read",
        "--name",
        "qa-sink",
        "--expect-min",
        "3",
        "--wait",
        "0.5",
    )
    assert code == 1
    assert json.loads(out)["requests"] == 2

    code, out = _run(
        capsys,
        "sink",
        "read",
        "--name",
        "qa-sink",
        "--contains",
        "QA_ABSENT",
        "--expect-max",
        "0",
    )
    assert code == 0, out


def test_map_check_requires_a_bug_marker_in_known_bug_bullets(
    tmp_path, monkeypatch, capsys
):
    feature_map = tmp_path / "map"
    feature_map.mkdir()
    (feature_map / "F98-demo.md").write_text(FAMILY.replace("  # bug", ""))
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", feature_map)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    assert code == 1
    assert "# bug" in out


def test_committed_feature_map_is_structurally_valid(capsys):
    """`map check --no-routes`: headings, IDs, index counts, recipes parse."""
    code, out = _run(capsys, "map", "check", "--no-routes")
    assert code == 0, out


def test_every_command_documents_examples(capsys):
    parser = cas.build_parser()
    for name in (
        "launch",
        "api",
        "doctor",
        "fixture",
        "exec",
        "restart",
        "stop",
        "state",
    ):
        with pytest.raises(SystemExit):
            parser.parse_args([name, "--help"])
        assert "Examples:" in capsys.readouterr().out, name


def test_without_a_run_errors_are_one_json_object(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(cas.HOME_ENV, str(tmp_path))
    monkeypatch.delenv(cas.RUN_ENV, raising=False)
    code, out = _run(capsys, "api", "GET", "/alive")
    payload = json.loads(out)
    assert code == 3
    assert payload["ok"] is False
    assert "launch --new --print-run" in payload["hint"]
