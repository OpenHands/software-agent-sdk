"""Prove which checkout an outer and nested real server actually launch."""

import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


harness, target, output = map(Path, sys.argv[1:])
script = harness / ".agents/skills/verify-agent-server/scripts/control_agent_server.py"
spec = importlib.util.spec_from_file_location("nested_probe", script)
cli = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cli
spec.loader.exec_module(cli)
sha = subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=target, text=True
).strip()
version = tomllib.loads((target / "openhands-sdk/pyproject.toml").read_text())[
    "project"
]["version"]
with tempfile.TemporaryDirectory(prefix="pr5621-nested-map-") as temporary:
    feature_map = Path(temporary)
    cli.FEATURE_MAP_DIR = feature_map
    os.environ[cli.HOME_ENV] = str(output.parent / (output.stem + "-runs"))
    (feature_map / "F98-nested.md").write_text(
        "# Nested checkout probe\n\nSource: `test`\n\nRoutes: none\n\n"
        "## Sub-features\n\n"
        "- `F98.nested`: both servers use the requested checkout.\n\n"
        "## How to get to it (agent POV)\n\n- CLI\n\n"
        "## Driving it with control-agent-server\n\nPreconditions:\n\n"
        "- **Nested launch (`F98.nested`).**\n  ```sh\n"
        "  NESTED=$(control-agent-server launch --new "
        "--name nested-probe --print-run)\n"
        "  trap 'control-agent-server stop --run \"$NESTED\"' EXIT\n"
        f'  test "$PWD" = {shlex.quote(str(target))}\n'
        "  control-agent-server api GET /server_info --auth none "
        f"--check build_git_sha eq {shlex.quote(sha)} "
        f"--check sdk_version eq {shlex.quote(version)} "
        "--save F98.nested/outer\n"
        '  control-agent-server api GET /server_info --run "$NESTED" '
        f"--auth none --check build_git_sha eq {shlex.quote(sha)} "
        f"--check sdk_version eq {shlex.quote(version)} "
        "--save F98.nested/inner\n"
        f"  control-agent-server exec --expect-output {shlex.quote(version)} "
        "--save F98.nested/sdk -- .venv/bin/python -c 'import importlib.metadata; "
        'print(importlib.metadata.version("openhands-sdk"))\'\n'
        "  ```\n\n## Gotchas\n\n- Both servers must stop.\n"
    )
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = cli.main(
            [
                "map",
                "run",
                "--file",
                "F98",
                "--fresh",
                "--checkout",
                str(target),
                "--record",
            ]
        )
    report = json.loads(stdout.getvalue())
    report["probe_context"] = {
        "harness": str(harness),
        "target": str(target),
        "expected_sha": sha,
        "expected_sdk": version,
    }
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "exit": code,
                "ok": report["ok"],
                "summary": report["summary"],
                "context": report["probe_context"],
            }
        )
    )
