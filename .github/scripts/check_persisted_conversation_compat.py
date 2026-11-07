#!/usr/bin/env python3
"""Check that conversations persisted by a published release still resume.

``tests/fixtures/conversations/generate.py`` runs inside a virtualenv with a
published ``openhands-sdk`` and writes scripted conversations, each with the
LLM input that release produced when it resumed them. Then
``tests/cross/test_persisted_conversation_compat.py`` resumes the same
conversations with the workspace SDK and requires the same LLM input.

Without arguments, the baseline is the latest release (the settings check
picks it the same way) and its conversations go to a temporary directory.
To check in fixtures for an older release:

    uv run python .github/scripts/check_persisted_conversation_compat.py \\
        --release 1.29.3 --output tests/fixtures/conversations/v1_29_3
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent))
from check_persisted_settings_compat import (  # noqa: E402
    get_pypi_baseline_version,
    get_pypi_release_cutoff,
    read_version_from_pyproject,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "tests" / "fixtures" / "conversations" / "generate.py"
TEST = REPO_ROOT / "tests" / "cross" / "test_persisted_conversation_compat.py"
CONVERSATIONS_DIR_ENV = "OH_PERSISTED_CONVERSATIONS_DIR"


def _run(args: list[str | Path], env: dict[str, str] | None = None) -> None:
    result = subprocess.run(args, capture_output=True, text=True, env=env)
    if result.returncode:
        output = (result.stdout + result.stderr).strip()[-2000:]
        raise SystemExit(f"::error title=Persisted conversations::{output}")


def generate_from_release(release: str, output: Path) -> None:
    """Write the generator's conversations with ``openhands-sdk==release``.

    They are written in a temporary directory with an empty HOME and then
    copied, so persisted paths and user-level config never name this machine.
    """
    # Resolve dependencies as of the release, as its users installed it:
    # --no-config drops this workspace's uv pins, which old releases predate.
    cutoff = get_pypi_release_cutoff("openhands-sdk", release)
    with tempfile.TemporaryDirectory(prefix="conversation-compat-") as tmp:
        venv = Path(tmp) / "venv"
        python = venv / "bin" / "python"
        _run(["uv", "venv", venv, "--python", sys.executable, "--quiet"])
        _run(
            [
                "uv", "pip", "install", "--no-config", "--python", python, "--quiet",
                "--refresh-package", "openhands-sdk",
                "--exclude-newer", cutoff,
                f"openhands-sdk=={release}",
            ]
        )  # fmt: skip
        home, written = Path(tmp) / "home", Path(tmp) / "out"
        home.mkdir()
        # -I keeps the workspace packages and the script directory off sys.path.
        _run([python, "-I", GENERATOR, written], env={**os.environ, "HOME": str(home)})
        shutil.copytree(written, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--release", help="openhands-sdk version to generate with")
    parser.add_argument("--output", type=Path, help="write fixtures here, no test")
    args = parser.parse_args()

    current = read_version_from_pyproject(REPO_ROOT / "openhands-sdk/pyproject.toml")
    release = args.release or get_pypi_baseline_version("openhands-sdk", current)
    if release is None:
        print("::warning title=Persisted conversations::No published baseline")
        return 0

    if args.output is not None:
        generate_from_release(release, args.output.resolve())
        print(f"Wrote conversations from openhands-sdk=={release} to {args.output}")
        return 0

    with tempfile.TemporaryDirectory(prefix="conversation-compat-out-") as out:
        generate_from_release(release, Path(out) / f"v{release}")
        print(f"Resuming conversations written by openhands-sdk=={release}", flush=True)
        env = {**os.environ, CONVERSATIONS_DIR_ENV: out}
        return subprocess.call([sys.executable, "-m", "pytest", "-q", TEST], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
