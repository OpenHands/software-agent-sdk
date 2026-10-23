"""Exercise the release workflow's shell with controlled pytest exit statuses."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import yaml


root = Path(__file__).resolve().parents[1]
workflow = yaml.safe_load((root / ".github/workflows/create-release.yml").read_text())
step = next(
    item
    for item in workflow["jobs"]["pre-release-stress-tests"]["steps"]
    if item["name"] == "Run Agent Server stress tests"
)
cases = [
    ([0], 0, 1),
    ([1, 0], 0, 2),
    ([1, 1], 1, 2),
    ([2, 0], 2, 1),
    ([3, 0], 3, 1),
    ([4, 0], 4, 1),
    ([5, 0], 5, 1),
    ([130, 0], 130, 1),
]
results = []
for statuses, expected_exit, expected_calls in cases:
    with tempfile.TemporaryDirectory(prefix="stress-gate-") as directory:
        temp = Path(directory)
        executable = temp / "uv"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "p = Path(os.environ['STRESS_GATE_STATE'])\n"
            "state = json.loads(p.read_text())\n"
            "status = state['statuses'][state['calls']]\n"
            "state['calls'] += 1\n"
            "p.write_text(json.dumps(state))\n"
            "raise SystemExit(status)\n"
        )
        executable.chmod(0o755)
        state_path = temp / "state.json"
        state_path.write_text(json.dumps({"statuses": statuses, "calls": 0}))
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            cwd=root,
            env={
                **os.environ,
                "PATH": str(temp) + os.pathsep + os.environ["PATH"],
                "STRESS_GATE_STATE": str(state_path),
            },
            capture_output=True,
            text=True,
            timeout=20,
        )
        calls = json.loads(state_path.read_text())["calls"]
        assert result.returncode == expected_exit, result.stdout + result.stderr
        assert calls == expected_calls, result.stdout + result.stderr
        results.append(
            {"statuses": statuses, "exit": result.returncode, "calls": calls}
        )

print(json.dumps(results, indent=2))
