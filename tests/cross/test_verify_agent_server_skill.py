"""Tests for the verify-agent-server skill's control CLI and feature map.

The CLI lives at `.agents/skills/verify-agent-server/scripts/control_agent_server.py`.
These tests drive it against a tiny local HTTP server (via `attach`) and a
throwaway feature map, and validate the committed map's structure, without
launching an agent server.
"""

from __future__ import annotations

import importlib.util
import io
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
  test "$VALUE" = fixed  # bug: exit 1
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


def test_helpers_outlive_their_bullet_until_stop(
    run_dir, tmp_path, write_verification_family, capsys
):
    helper_pid = tmp_path / "helper.pid"
    plain_pid = tmp_path / "plain.pid"
    recipe = (
        "  VALUE=$(echo carried)\n"
        "  control-agent-server helper start --name qa-sleeper --print-pid"
        f' -- sleep 60 > "{helper_pid}"\n'
        f'  sleep 60 >/dev/null 2>&1 &\n  echo "$!" > "{plain_pid}"\n'
    )
    write_verification_family(
        cas,
        FAMILY.replace("  VALUE=$(echo carried)\n", recipe).replace(
            '  test "$VALUE" = carried\n  control-agent-server api GET /info',
            f'  test "$VALUE" = carried\n  kill -0 "$(cat "{helper_pid}")"\n'
            "  control-agent-server api GET /info",
        ),
    )

    def running(path):
        pid = int(path.read_text())
        with suppress(psutil.NoSuchProcess):
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        return False

    try:
        code, out = _run(capsys, "map", "run", "--file", "F98")
        assert code == 0, out
        assert json.loads(out)["summary"]["pass"] == 2
        assert running(helper_pid)
        assert not running(plain_pid)

        code, out = _run(capsys, "stop")
        assert code == 0, out
        assert json.loads(out)["helpers"] == [
            {"name": "qa-sleeper", "outcome": "terminated"}
        ]
        assert not running(helper_pid)
    finally:
        for path in (helper_pid, plain_pid):
            if path.exists() and running(path):
                psutil.Process(int(path.read_text())).kill()


def test_helper_stop_and_start_errors(run_dir, capsys):
    code, out = _run(
        capsys, "helper", "start", "--name", "qa-sleeper", "--", "sleep", "30"
    )
    assert code == 0, out
    pid = json.loads(out)["pid"]
    code, out = _run(
        capsys, "helper", "start", "--name", "qa-sleeper", "--", "sleep", "30"
    )
    assert code == 2 and "already running" in out
    code, out = _run(capsys, "helper", "stop", "qa-sleeper")
    assert json.loads(out)["stopped"] == [
        {"name": "qa-sleeper", "outcome": "terminated"}
    ]
    assert not psutil.pid_exists(pid) or (
        psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    )
    code, out = _run(capsys, "helper", "stop", "qa-missing")
    assert code == 2 and "No helper named qa-missing" in out
    code, out = _run(capsys, "helper", "start", "--name", "qa-dies", "--", "false")
    assert code == 1 and "exited with 1" in out


def test_map_check_flags_variables_from_unrequired_recipes(
    tmp_path, monkeypatch, capsys
):
    feature_map = tmp_path / "map"
    feature_map.mkdir()
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", feature_map)
    program_text = "  cat > prog.py <<'PY'\n  print('$VALUE')\n  PY\n"
    read_back = '  test "$VALUE" = carried\n  control-agent-server api GET /info'
    family = FAMILY.replace("  Requires: `F98.first`\n", "").replace(
        read_back, program_text + read_back
    )
    (feature_map / "F98-demo.md").write_text(family)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    problems = json.loads(out)["problems"]["F98-demo.md"]
    assert code == 1
    assert problems == [
        f"{feature} reads $VALUE, last set by F98.first; "
        "add Requires: `F98.first` so --only runs it"
        for feature in ("F98.second", "F98.bug")
    ]

    quoted_only = FAMILY.replace("  Requires: `F98.first`\n", "").replace(
        read_back, program_text + "  control-agent-server api GET /info"
    )
    (feature_map / "F98-demo.md").write_text(quoted_only)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    problems = json.loads(out)["problems"].get("F98-demo.md", [])
    assert not [p for p in problems if p.startswith("F98.second")], out


def test_outside_reads_counts_reads_before_the_bullets_own_binding():
    script = "\n".join(
        [
            'test "$OLD" != x',
            "OLD=$(echo new)",
            'echo "$OLD"',
            'cli exec --env CID="$CID" -- true',
            'NEXT=$(cli get "$NEXT")',
            'for f in a b; do echo "$f"; done',
            'while read -r line; do echo "$line"; done < /dev/null',
            "LOCAL=1",
            'echo "$LOCAL"',
            "cat > prog.py <<'PY'",
            "print('$INSIDE')",
            "PY",
        ]
    )
    assert cas.outside_reads(script) == {"OLD", "CID", "NEXT"}


def test_recipe_needs_block_that_recipe_and_its_dependents(
    run_dir, write_verification_family, monkeypatch, capsys
):
    family = FAMILY.replace(
        "- **Offline (`F98.offline`), blocked: needs a GPU.** Not executed.\n",
        "- **Container (`F98.container`).** Needs a Docker daemon.\n"
        "  Needs: `docker`\n"
        "  ```sh\n  CONTAINER=$(echo started)\n  ```\n"
        "- **After the container (`F98.offline`).** Uses it.\n"
        "  Requires: `F98.container`\n"
        '  ```sh\n  test "$CONTAINER" = started\n  ```\n',
    )
    write_verification_family(cas, family)
    monkeypatch.setattr(
        cas,
        "machine_capabilities",
        lambda: {
            "docker": {"available": False, "detail": "no docker daemon"},
        },
    )
    code, out = _run(capsys, "map", "run", "--file", "F98", "--record")
    result = json.loads(out)
    assert code == 0, out
    assert result["summary"] == {
        "pass": 2,
        "fail": 0,
        "xfail": 1,
        "xpass": 0,
        "blocked": 2,
        "skipped": 0,
        "no-commands": 0,
    }
    blocked = [r for r in result["bullets"] if r["status"] == "blocked"]
    assert [(r["ids"], r["missing"]) for r in blocked] == [
        (["F98.container"], ["docker"]),
        (["F98.offline"], ["docker"]),
    ]
    ledger = [
        json.loads(line)
        for line in (run_dir / "evidence" / "ledger.jsonl").read_text().splitlines()
    ]
    assert {r["feature"]: r["result"] for r in ledger}["F98.offline"] == "blocked"

    code, out = _run(capsys, "capabilities")
    family_row = json.loads(out)["families"][0]
    assert family_row["drivable"] is True
    assert family_row["blocked_recipes"] == [
        {"ids": ["F98.container"], "missing": ["docker"]},
        {"ids": ["F98.offline"], "missing": ["docker"]},
    ]


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
    (feature_map / "F98-demo.md").write_text(FAMILY.replace("  # bug: exit 1", ""))
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", feature_map)
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    assert code == 1
    assert "# bug" in out


@pytest.mark.parametrize(
    "marker, problem",
    [
        ("# bug", "needs the documented failure"),
        ("# bug: exit", "is not PATH OP"),
        ("# bug: exit 0", "from 1 to 255"),
        ("# bug: exit 1 && status eq 500", "stands alone"),
        ("# bug: status is 500", "is not PATH OP"),
        ("# bug: status eq", "takes one value"),
        ("# bug: detail missing 500", "takes no value"),
        ("# bug: detail missing", "needs a clause naming"),
        ("# bug: detail contains 'unclosed", "cannot parse"),
        ("# bug: status eq 500", "does not call control-agent-server"),
    ],
)
def test_map_check_rejects_a_missing_or_malformed_bug_predicate(
    write_verification_family, capsys, marker, problem
):
    write_verification_family(cas, FAMILY.replace("# bug: exit 1", marker))
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    assert code == 1, out
    assert problem in out


@pytest.mark.parametrize(
    "line",
    [
        'if test "$VALUE" = carried; then false; fi  # bug: exit 1',
        '! test "$VALUE" = carried  # bug: exit 1',
        'test ! -e "$VALUE"  # bug: exit 1',
    ],
)
def test_map_check_rejects_negative_bug_lines(write_verification_family, capsys, line):
    write_verification_family(
        cas, FAMILY.replace('test "$VALUE" = fixed  # bug: exit 1', line)
    )
    code, out = _run(capsys, "map", "check", "--file", "F98", "--no-routes")
    assert code == 1, out
    assert "positively" in out


def test_assert_names_a_computed_value(tmp_path, capsys, monkeypatch):
    code, out = _run(capsys, "assert", "--value", "1700", "--check", ".", "le", "1500")
    result = json.loads(out)
    assert code == 1
    assert result["value"] == 1700
    assert cas.bug_predicate_matches(
        cas.parse_bug_predicate("value gt 1500"), 1, result
    )
    saved = tmp_path / "saved.json"
    saved.write_text('{"status": 500}')
    code, out = _run(
        capsys, "assert", "--json", str(saved), "--check", "status", "eq", "409"
    )
    assert code == 1
    assert json.loads(out)["checks"][0]["actual"] == "500"
    monkeypatch.setattr("sys.stdin", io.StringIO("qa-text\n"))
    code, out = _run(capsys, "assert", "--check", ".", "eq", "qa-text")
    assert code == 0, out


BUG_API = "control-agent-server api GET {path} --expect 200"
DOCUMENTED = "  # bug: status eq 404 && response.body.detail eq 'Not Found'"


@pytest.mark.parametrize(
    "path, status",
    [
        # The documented failure: the marked command answers 404 Not Found.
        ("/missing", "xfail"),
        # An unrelated error at the same marked command is not the known bug.
        ("/refused", "fail"),
        # Correct behavior: the bug no longer reproduces.
        ("/info", "xpass"),
    ],
)
def test_known_bug_xfails_only_on_the_documented_failure(
    run_dir, write_verification_family, capsys, path, status
):
    family = FAMILY.replace(
        '  test "$VALUE" = fixed  # bug: exit 1',
        "  " + BUG_API.format(path=path) + DOCUMENTED,
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98", "--record")
    result = json.loads(out)
    bug = next(r for r in result["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == status, out
    assert code == (0 if status == "xfail" else 1)
    if status == "fail":
        assert bug["failed_status"] == 1
        assert bug["observed"]["status"] == 503
        assert "not with the documented failure" in bug["hint"]
        ledger = [
            json.loads(line)
            for line in (run_dir / "evidence" / "ledger.jsonl").read_text().splitlines()
        ]
        assert {r["feature"]: r["result"] for r in ledger}["F98.bug"] == "fail"


def test_known_bug_matches_the_failure_not_a_later_cleanup_call(
    run_dir, write_verification_family, capsys
):
    # A recipe's EXIT cleanup calls the CLI after the marked command failed;
    # the predicate still sees what the marked command observed.
    family = FAMILY.replace(
        '  test "$VALUE" = fixed  # bug: exit 1',
        "  trap 'control-agent-server api GET /info >/dev/null' EXIT\n  "
        + BUG_API.format(path="/missing")
        + DOCUMENTED,
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    bug = next(r for r in json.loads(out)["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == "xfail", out


def test_known_bug_pass_after_a_broken_arrange_is_not_a_fix(
    run_dir, write_verification_family, capsys
):
    # With --keep-going, a recipe whose prerequisite failed can pass on empty
    # variables; that is no evidence the bug is fixed.
    family = FAMILY.replace(
        "VALUE=$(echo carried)", "VALUE=$(echo carried)\n  false"
    ).replace(
        '  test "$VALUE" = carried\n  test "$VALUE" = fixed  # bug: exit 1',
        "  true  # bug: exit 1",
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98", "--keep-going")
    bug = next(r for r in json.loads(out)["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == "fail", out
    assert "F98.second" in bug["hint"]


def test_known_bug_pass_after_a_broken_indirect_arrange_is_not_a_fix(
    run_dir, write_verification_family, capsys
):
    # F98.second passes on its own, but it needs F98.first, which failed; the
    # known bug needs F98.second, so its pass is no evidence either.
    family = (
        FAMILY.replace("VALUE=$(echo carried)", "VALUE=$(echo carried)\n  false")
        .replace(
            '  test "$VALUE" = carried\n  control-agent-server api GET /info',
            "  control-agent-server api GET /info",
        )
        .replace(
            '  test "$VALUE" = carried\n  test "$VALUE" = fixed  # bug: exit 1',
            "  true  # bug: exit 1",
        )
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98", "--keep-going")
    rows = {tuple(r.get("ids") or []): r for r in json.loads(out)["bullets"]}
    assert rows[("F98.second",)]["status"] == "pass", out
    bug = rows[("F98.bug",)]
    assert bug["status"] == "fail", out
    assert "F98.first" in bug["hint"]


@pytest.mark.parametrize(
    "stored, status",
    [
        # The documented failure: the file is there and has no key.
        ('{"agent": {"model": "m"}}', "xfail"),
        # The file is missing: an error object has no `checks`, so the
        # `eq null` clause must not read its absence as the bug.
        (None, "fail"),
    ],
)
def test_known_bug_predicate_needs_its_paths_present(
    run_dir, write_verification_family, capsys, stored, status
):
    if stored is not None:
        (run_dir / "state.json").write_text(stored)
    family = FAMILY.replace(
        '  test "$VALUE" = fixed  # bug: exit 1',
        "  control-agent-server state cat state.json --check agent.key exists"
        "  # bug: checks.0.actual eq null",
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    bug = next(r for r in json.loads(out)["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == status, out


def test_known_bug_exit_predicate_tells_a_false_test_from_an_error(
    run_dir, write_verification_family, capsys
):
    # `test` exits 1 when the comparison is false and 2 when it cannot run.
    family = FAMILY.replace(
        '  test "$VALUE" = fixed  # bug: exit 1',
        '  test "$VALUE" -le 3  # bug: exit 1',
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    bug = next(r for r in json.loads(out)["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == "fail", out
    assert bug["failed_status"] == 2


def test_the_marked_call_replaces_an_earlier_observation(
    run_dir, write_verification_family, capsys
):
    # The marked line's own call replaces what an earlier call observed, so an
    # earlier 404 cannot stand in for the marked command's failure.
    family = FAMILY.replace(
        '  test "$VALUE" = fixed  # bug: exit 1',
        "  control-agent-server api GET /missing --expect 404\n"
        "  control-agent-server api GET /refused --expect 200" + DOCUMENTED,
    )
    write_verification_family(cas, family)
    code, out = _run(capsys, "map", "run", "--file", "F98")
    bug = next(r for r in json.loads(out)["bullets"] if r.get("ids") == ["F98.bug"])
    assert bug["status"] == "fail", out
    assert bug["observed"]["status"] == 503


def test_committed_feature_map_is_structurally_valid(capsys):
    """`map check --no-routes`: headings, IDs, index counts, recipes parse."""
    code, out = _run(capsys, "map", "check", "--no-routes")
    assert code == 0, out


def _index(feature_map, baseline_lines):
    rows = "\n".join(
        f"| F{n:02d} | [Family {n}](F{n:02d}-demo.md) | x | x | baseline | 0 |"
        for n in range(1, 35)
    )
    (feature_map / "README.md").write_text(
        "# Map\n\n" + "\n".join(baseline_lines) + "\n\n## Families\n\n" + rows + "\n"
    )


def test_map_baseline_reads_and_moves_the_index_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", tmp_path)
    head = cas.git("rev-parse", "HEAD")
    _index(tmp_path, ["Maintenance baseline: main@abc1234 (2026-10-01). Keep me."])
    code, out = _run(capsys, "map", "baseline")
    assert code == 0, out
    assert json.loads(out)["sha"] == "abc1234"

    code, out = _run(
        capsys,
        "map",
        "baseline",
        "--set",
        head,
        "--date",
        "2026-10-12",
        "--main",
        "HEAD",
    )
    assert code == 0, out
    text = (tmp_path / "README.md").read_text()
    assert f"Maintenance baseline: main@{head} (2026-10-12). Keep me." in text
    code, out = _run(capsys, "map", "baseline")
    assert json.loads(out)["commits_since"] == 0


@pytest.mark.parametrize(
    "lines, problem",
    [
        ([], "no `Maintenance baseline"),
        (
            [
                "Maintenance baseline: main@abc1234 (2026-10-01).",
                "Maintenance baseline: main@def5678 (2026-10-02).",
            ],
            "2 `Maintenance baseline:` lines",
        ),
        (["Maintenance baseline: `abc1234` (first pass)."], "malformed"),
    ],
)
def test_map_baseline_rejects_missing_duplicate_or_malformed_lines(
    tmp_path, monkeypatch, capsys, lines, problem
):
    monkeypatch.setattr(cas, "FEATURE_MAP_DIR", tmp_path)
    _index(tmp_path, lines)
    code, out = _run(capsys, "map", "baseline")
    assert code == 1
    assert problem in out
    assert any(
        problem in p
        for p in cas.baseline_problems((tmp_path / "README.md").read_text())
    )


def test_compare_ledgers_sorts_what_changed():
    def row(feature, result):
        return {"feature": feature, "result": result, "run": "r"}

    before = [row("F01.a", "pass"), row("F01.b", "fail"), row("F01.c", "pass")]
    before += [row("F01.d", "xfail"), row("F01.e", "pass")]
    after = [row("F01.a", "fail"), row("F01.b", "pass"), row("F01.d", "xpass")]
    after += [row("F01.e", "pass"), row("F01.f", "blocked")]
    changes = cas.compare_ledgers(before, after)
    assert [c["feature"] for c in changes["newly_failing"]] == ["F01.a", "F01.d"]
    assert [c["feature"] for c in changes["newly_passing"]] == ["F01.b"]
    assert [c["feature"] for c in changes["not_checked"]] == ["F01.c"]
    assert [c["feature"] for c in changes["other"]] == ["F01.f"]
    assert "Newly failing:" in "\n".join(cas.changes_markdown(changes))


def test_compare_ledgers_takes_the_newest_attempt_whatever_the_order():
    def row(result, at):
        return {"feature": "F01.a", "result": result, "run": at, "at": at}

    baseline = [row("pass", "2026-10-12T00:00:00+00:00")]
    early = row("pass", "2026-10-13T01:00:00+00:00")
    late = row("fail", "2026-10-13T02:00:00+00:00")
    for current in ([late, early], [early, late]):
        changes = cas.compare_ledgers(baseline, current)
        assert [c["feature"] for c in changes["newly_failing"]] == ["F01.a"]
        assert changes["conflicting"][0]["feature"] == "F01.a"
        text = "\n".join(cas.changes_markdown(changes))
        assert "No result changed" not in text
        assert "Conflicting attempts" in text


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
