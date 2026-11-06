"""Compare built SDK wheels using locked dependencies and controlled HTTP."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--baseline-wheel", type=Path, required=True)
parser.add_argument("--candidate-wheel", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
wheels = {
    "baseline": args.baseline_wheel.resolve(),
    "candidate": args.candidate_wheel.resolve(),
}
with (
    zipfile.ZipFile(wheels["baseline"]) as before,
    zipfile.ZipFile(wheels["candidate"]) as after,
):
    assert before.namelist() == after.namelist()
    changed = [n for n in before.namelist() if before.read(n) != after.read(n)]
    assert set(changed) == {
        "openhands/sdk/llm/utils/telemetry.py",
        "openhands_sdk-1.60.1.dist-info/RECORD",
    }, changed
with TemporaryDirectory(prefix="ohe3895-wheels-") as tmp:
    deps = Path(tmp) / "deps"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-compile",
            "--require-hashes",
            "--only-binary=:all:",
            "--target",
            str(deps),
            "-r",
            str(root / "evidence/requirements.txt"),
        ],
        check=True,
    )
    for variant, wheel in wheels.items():
        target = Path(tmp) / variant
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-deps",
                "--no-compile",
                "--target",
                str(target),
                str(wheel),
            ],
            check=True,
        )
        env = dict(
            os.environ,
            PYTHONPATH=str(target) + os.pathsep + str(deps),
            PYTHON_DOTENV_DISABLED="1",
        )
        print(
            "WHEEL="
            + json.dumps(
                {
                    "variant": variant,
                    "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                }
            ),
            flush=True,
        )
        transport = [sys.executable, str(root / "http_probe.py")]
        if variant == "candidate":
            transport.append("--candidate")
        subprocess.run(transport, env=env, check=True, timeout=120)
        run = subprocess.run(
            [sys.executable, str(root / "cost_demo.py"), "--expect-total", "0.08"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
        print(run.stdout)
        records = [
            json.loads(line.split("=", 1)[1])
            for line in run.stdout.splitlines()
            if line.startswith("OHE3895_DEMO=")
        ]
        assert len(records) == 1, (
            "No completed conversation record; this is not a valid before/after result"
        )
        result = records[0]
        assert result["requests"] == 2
        assert result["first_status"].endswith("FINISHED") and result[
            "final_status"
        ].endswith("FINISHED")
        assert result["restored_total"] == result["first_total"]
        assert result["final_total"] == (0 if variant == "baseline" else 0.08)
        assert run.returncode == (1 if variant == "baseline" else 0)
print("PACKAGED_COMPARISON=PASS")
