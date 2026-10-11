"""Compare the CLI's real entry points before and after the review fixes.

Run with the checkout's Python, passing its control_agent_server.py path and
an output JSON path. HTTP responses use an invented session key on loopback;
recipe probes use a temporary map. No model calls are made.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil


script, destination = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("review_cli", script)
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        if self.path == "/refused":
            self.send_response(503)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        key = self.headers.get("X-Session-API-Key", "")
        body = json.dumps({"echo_key": key}).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.send_header("content-type", "application/json")
        self.send_header("x-echo-key", key)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def command(*args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        try:
            code = cli.main(list(args))
        except SystemExit as exc:
            code = exc.code
    text = output.getvalue().strip()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = text
    return {"exit": code, "output": payload}


def family(code, label="Probe (`F98.probe`), known bug."):
    return (
        "# Probe\n\nSource: `test`\n\nRoutes: none\n\n"
        "## Sub-features\n\n- `F98.probe`: probe\n\n"
        "## How to get to it (agent POV)\n\n- CLI\n\n"
        "## Driving it with control-agent-server\n\nPreconditions:\n"
        "\n  ```sh\n  true\n  ```\n\n"
        f"- **{label}**\n  ```sh\n"
        + "".join(f"  {line}\n" for line in code.splitlines())
        + "  ```\n\n## Gotchas\n\n- None\n"
    )


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
results = {"checkout": cli.git("rev-parse", "HEAD"), "script": str(script)}
with tempfile.TemporaryDirectory(prefix="pr5621-probes-") as temporary:
    root = Path(temporary)
    os.environ[cli.HOME_ENV] = str(root / "runs")
    os.environ["QA_PROBE_KEY"] = "invented-review-session-key"
    os.environ.pop(cli.RUN_ENV, None)
    run = command(
        "attach",
        "--url",
        f"http://127.0.0.1:{server.server_port}",
        "--key-env",
        "QA_PROBE_KEY",
        "--print-run",
    )["output"]
    os.environ[cli.RUN_ENV] = run
    try:
        results["field"] = command("api", "GET", "/info", "--field", "echo_key")
        results["header"] = command(
            "api", "GET", "/info", "--print-header", "x-echo-key"
        )
        results["ws_negative"] = command(
            "ws",
            "listen",
            "/refused",
            "--auth",
            "none",
            "--expect-no-kind",
            "BashOutput",
        )
        command(
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
        capture = Path(run) / "private" / "unused"
        registered = cli.load_run(Path(run)).data["processes"][-1]
        capture = Path(registered["out"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if capture.exists() and '"direction": "ended"' in capture.read_text():
                break
            time.sleep(0.05)
        results["ws_background_open"] = command(
            "ws", "read", "refused", "--expect-open"
        )
        with contextlib.suppress(ChildProcessError):
            os.waitpid(registered["pid"], os.WNOHANG)

        feature_map = root / "map"
        feature_map.mkdir()
        cli.FEATURE_MAP_DIR = feature_map
        path = feature_map / "F98-probe.md"
        setup_pidfile = root / "setup-child.pid"
        timed_setup = (
            f'sleep 20 &\nCHILD=$!\necho "$CHILD" > "{setup_pidfile}"\nwait "$CHILD"'
        )
        for name, setup in [("early_exit", "exit 3"), ("setup_timeout", timed_setup)]:
            path.write_text(family(f"{setup}\nfalse  # bug"))
            results[name] = command(
                "map", "run", "--file", "F98", "--timeout", "0.1", "--record"
            )
            if setup_pidfile.exists():
                setup_child = int(setup_pidfile.read_text())
                if psutil.pid_exists(setup_child):
                    psutil.Process(setup_child).kill()
        pidfile = root / "child.pid"
        path.write_text(
            family(
                f'sleep 20 &\nCHILD=$!\necho "$CHILD" > "{pidfile}"\nwait "$CHILD"',
                "Child (`F98.probe`).",
            )
        )
        results["child_timeout"] = command(
            "map", "run", "--file", "F98", "--timeout", "0.1"
        )
        child = int(pidfile.read_text())
        time.sleep(0.2)
        results["child_timeout"]["child_survived"] = psutil.pid_exists(child)
        if psutil.pid_exists(child):
            psutil.Process(child).kill()
        path.write_text(family("true", "Ordinary (`F98.probe`)."))
        results["unknown_selection"] = command(
            "map", "run", "--file", "F98", "--only", "F98.typo"
        )
        path.write_text(
            path.read_text().replace("Routes: none", "Routes: `GET /alive`")
        )
        routes = cli.route_templates()
        (feature_map / "README.md").write_text(
            "## Not mapped\n\n"
            + "\n".join(
                f"- `{r['method']} {r['path']}`: outside the one-route coverage probe."
                for r in routes
                if (r["method"], r["path"]) != ("GET", "/alive")
            )
        )
        results["undriven_coverage"] = command("map", "coverage")
    finally:
        command("stop", "--run", run)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
destination.write_text(json.dumps(results, indent=2) + "\n")
print(
    json.dumps(
        {
            name: {
                "exit": row["exit"],
                **(
                    {"child_survived": row["child_survived"]}
                    if "child_survived" in row
                    else {}
                ),
            }
            for name, row in results.items()
            if isinstance(row, dict) and "exit" in row
        },
        indent=2,
    )
)
