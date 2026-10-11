"""Tests for the verify-agent-server skill's control CLI and feature map.

The CLI lives at `.agents/skills/verify-agent-server/scripts/control_agent_server.py`.
These tests drive it against a tiny local HTTP server (via `attach`) and a
throwaway feature map, and validate the committed map's structure, without
launching an agent server.
"""

from __future__ import annotations

import importlib.util
import json
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil
import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve


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
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path.startswith("/refused"):
            time.sleep(0.2)
            self.send_response(503)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        body = {
            "status": "ok",
            "uptime": 12.0,
            "items": [{"id": "first"}, {"id": "second"}],
            "echo_key": self.headers.get("X-Session-API-Key"),
            "encrypted": "gAAAAA" + "A" * 64,
        }
        if self.path.startswith("/missing"):
            self.send_response(404)
            body = {"detail": "Not Found"}
        else:
            self.send_response(200)
        payload = json.dumps(body).encode()
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.send_header("x-echo-key", self.headers.get("X-Session-API-Key", ""))
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
    cas.main(["stop", "--run", str(run)])
    server.shutdown()
    server.server_close()


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


@pytest.mark.parametrize(
    "flag,value,redacted",
    [
        ("--field", "echo_key", "<redacted:session-api-key>"),
        ("--field", "encrypted", "<redacted:encrypted>"),
        ("--field", ".", "<redacted:session-api-key>"),
        ("--print-header", "x-echo-key", "<redacted:session-api-key>"),
    ],
)
def test_raw_api_output_redacts_secrets(run_dir, capsys, flag, value, redacted):
    with pytest.raises(SystemExit) as exit_info:
        cas.main(["api", "GET", "/info", flag, value])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "qa-session-key-123456" not in out
    assert "gAAAAA" not in out
    assert redacted in out


def test_raw_secrets_require_an_explicit_opt_in(run_dir, capsys):
    with pytest.raises(SystemExit) as exit_info:
        cas.main(["api", "GET", "/info", "--field", "echo_key", "--unsafe-unredacted"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == "qa-session-key-123456"
    code, _ = _run(capsys, "api", "GET", "/info", "--unsafe-unredacted")
    assert code == 2


@pytest.mark.parametrize(
    "expectation",
    [
        [],
        ["--expect-open"],
        ["--expect-no-kind", "BashOutput"],
        ["--expect-frames", "0"],
    ],
)
def test_ws_handshake_errors_fail_listen_checks(run_dir, capsys, expectation):
    code, out = _run(capsys, "ws", "listen", "/refused", "--auth", "none", *expectation)
    assert code == 1, out
    assert json.loads(out)["close"]["http_status"] == 503


def test_ws_explicit_handshake_rejection_passes(run_dir, capsys):
    code, out = _run(
        capsys, "ws", "listen", "/refused", "--auth", "none", "--expect-reject", "503"
    )
    assert code == 0, out


@pytest.mark.parametrize("expectation", [["--expect-open"], ["--expect-none"]])
def test_ws_background_handshake_errors_fail_checks(run_dir, capsys, expectation):
    code, out = _run(
        capsys,
        "ws",
        "start",
        "/refused",
        "--auth",
        "none",
        "--name",
        "refused",
        "--settle",
        "0.01",
    )
    assert code == 0, out
    code, out = _run(
        capsys, "ws", "read", "refused", "--wait", "5", "--expect-reject", "503"
    )
    assert code == 0, out
    code, out = _run(capsys, "ws", "read", "refused", *expectation)
    assert code == 1, out
    assert json.loads(out)["close"]["http_status"] == 503


def test_ws_successful_connections_and_captures_still_pass(run_dir, capsys):
    def greet(ws):
        ws.send(json.dumps({"kind": "Greeting", "text": "hello"}))
        with suppress(ConnectionClosed):
            ws.recv(timeout=5)

    with serve(greet, "127.0.0.1", 0) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.socket.getsockname()[1]}"
        with pytest.raises(SystemExit):
            cas.main(["attach", "--url", url, "--print-run"])
        ws_run = capsys.readouterr().out.strip()
        try:
            code, out = _run(
                capsys,
                "ws",
                "listen",
                "/hello",
                "--run",
                ws_run,
                "--auth",
                "none",
                "--expect-kind",
                "Greeting",
                "--expect-open",
                "--count",
                "1",
            )
            assert code == 0, out
            code, out = _run(
                capsys,
                "ws",
                "start",
                "/hello",
                "--run",
                ws_run,
                "--auth",
                "none",
                "--name",
                "hello",
                "--settle",
                "0.01",
                "--duration",
                "3",
            )
            assert code == 0, out
            code, out = _run(
                capsys,
                "ws",
                "read",
                "hello",
                "--run",
                ws_run,
                "--wait",
                "5",
                "--expect-kind",
                "Greeting",
                "--expect-open",
            )
            assert code == 0, out
        finally:
            cas.main(["stop", "--run", ws_run])
            server.shutdown()
            worker.join(timeout=5)


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
  Requires: `F98.first`
  ```sh
  test "$VALUE" = carried
  control-agent-server api GET /info --check status eq ok
  ```
- **Broken thing (`F98.bug`), known bug.** Asserts the correct behavior.
  Requires: `F98.second`
  ```sh
  test "$VALUE" = carried
  test "$VALUE" = fixed  # bug
  ```
- **Offline (`F98.offline`), blocked: needs a GPU.** Not executed.

## Gotchas

- None.
"""


def test_map_run_carries_variables_and_reports_xfail_and_blocked(
    run_dir, capsys, write_verification_family
):
    family_path = write_verification_family(cas, FAMILY)
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
    family_path.write_text(fixed)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    assert code == 1
    assert json.loads(out)["summary"]["xpass"] == 1

    broken_arrange = FAMILY.replace(
        'test "$VALUE" = carried\n  test "$VALUE" = fixed',
        'test "$VALUE" = other\n  test "$VALUE" = fixed',
    )
    family_path.write_text(broken_arrange)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    result = json.loads(out)
    assert code == 1
    assert result["summary"]["xfail"] == 0
    assert result["summary"]["fail"] == 1


@pytest.mark.parametrize("setup", ["exit 3", "sleep 30"])
def test_map_setup_failure_is_not_a_reproduced_bug(
    run_dir, write_verification_family, capsys, setup
):
    family = FAMILY.replace(
        '  test "$VALUE" = carried\n  test "$VALUE" = fixed',
        f'  {setup}\n  test "$VALUE" = fixed',
    ).replace("  control-agent-server api GET /info --check status eq ok", "  true")
    write_verification_family(cas, family)
    code, out = _run(
        capsys, "map", "run", "--file", "F98", "--timeout", "0.5", "--record"
    )
    assert code == 1, out
    assert json.loads(out)["summary"]["xfail"] == 0
    ledger = [
        json.loads(line)
        for line in (run_dir / "evidence" / "ledger.jsonl").read_text().splitlines()
    ]
    assert (
        next(row for row in ledger if row["feature"] == "F98.bug")["result"] == "fail"
    )


@pytest.mark.parametrize("cancel", [False, True])
def test_map_recipe_children_stop_on_timeout_or_cancellation(
    run_dir, tmp_path, write_verification_family, cancel
):
    pidfile = tmp_path / "child.pid"
    recipe = (
        f'  sleep 30 &\n  CHILD=$!\n  echo "$CHILD" > "{pidfile}"\n  wait "$CHILD"\n'
    )
    family_path = write_verification_family(
        cas, FAMILY.replace("  VALUE=$(echo carried)\n", recipe)
    )
    try:
        program = (
            "import runpy, sys; from pathlib import Path; "
            "cli = runpy.run_path(sys.argv[1]); "
            "cli['main'].__globals__['FEATURE_MAP_DIR'] = Path(sys.argv[2]); "
            "sys.exit(cli['main'](sys.argv[3:]))"
        )
        with subprocess.Popen(
            [
                sys.executable,
                "-c",
                program,
                str(SCRIPT),
                str(family_path.parent),
                "map",
                "run",
                "--file",
                "F98",
                "--timeout",
                "1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as process:
            if cancel:
                deadline = time.monotonic() + 5
                while not pidfile.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert pidfile.exists()
                process.send_signal(signal.SIGTERM)
            out, err = process.communicate(timeout=10)
            code = process.returncode
        assert code == (130 if cancel else 1), out + err
        pid = int(pidfile.read_text())
        deadline = time.monotonic() + 2
        while psutil.pid_exists(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not psutil.pid_exists(pid)
    finally:
        if pidfile.exists() and psutil.pid_exists(int(pidfile.read_text())):
            psutil.Process(int(pidfile.read_text())).kill()


@pytest.mark.parametrize("only", ["F98.typo", "F98.first,F98.typo", "F97.first"])
def test_map_rejects_unknown_selections_before_setup(
    run_dir, write_verification_family, capsys, only
):
    write_verification_family(cas, FAMILY)
    code, out = _run(capsys, "map", "run", "--file", "F98", "--only", only, "--fresh")
    assert code == 2, out
    assert "--only" in json.loads(out)["error"]
    assert not (run_dir / "evidence" / "map-run").exists()


def test_selected_recipe_runs_its_transitive_prerequisites(
    run_dir, write_verification_family, capsys
):
    write_verification_family(cas, FAMILY)
    code, out = _run(capsys, "map", "run", "--file", "F98", "--only", "F98.bug")
    assert code == 0, out
    result = json.loads(out)
    assert result["summary"]["pass"] == 2
    assert result["summary"]["xfail"] == 1
    assert result["summary"]["blocked"] == 0


def test_coverage_fails_an_owned_route_without_a_recipe(
    write_verification_family, capsys
):
    write_verification_family(
        cas, FAMILY.replace("Routes: none", "Routes: `GET /alive`")
    )
    code, out = _run(capsys, "map", "coverage")
    assert code == 1, out
    assert {"route": "GET /alive", "owner": ["F98-demo.md"]} in json.loads(out)[
        "owned_but_not_driven"
    ]


def test_combined_reports_keep_each_runs_result_and_artifacts(
    run_dir, write_verification_family, capsys
):
    write_verification_family(cas, FAMILY)
    url = json.loads((run_dir / "run.json").read_text())["url"]
    with pytest.raises(SystemExit):
        cas.main(["attach", "--url", url, "--print-run"])
    other_run = Path(capsys.readouterr().out.strip())
    for run, result in [(run_dir, "xfail"), (other_run, "xpass")]:
        artifact = run / "evidence" / "proof.json"
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text("{}")
        code, out = _run(
            capsys,
            "evidence",
            "add",
            "--run",
            str(run),
            "--feature",
            "F98.bug",
            "--result",
            result,
            "--entry",
            "selected recipe",
            "--expected",
            "assertion succeeds",
            "--actual",
            result,
            "--artifact",
            str(artifact),
        )
        assert code == 0, out
    code, out = _run(
        capsys,
        "evidence",
        "report",
        "--runs",
        str(run_dir),
        str(other_run),
        "--print-markdown",
    )
    assert code == 0, out
    result = json.loads(out)
    assert result["features_with_evidence"] == 1
    assert result["counts"] == {"xpass": 1, "xfail": 1}
    for run in (run_dir, other_run):
        assert str(run / "evidence" / "proof.json") in result["markdown"]


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
