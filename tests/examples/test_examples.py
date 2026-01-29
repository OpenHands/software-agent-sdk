"""Integration tests that execute example scripts via pytest.

These tests are disabled by default. Pass ``--run-examples`` to enable them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXAMPLES_ROOT = REPO_ROOT / "examples"

# Maximum time (seconds) allowed for a single example script to run
EXAMPLE_TIMEOUT_SECONDS = 600  # 10 minutes

_TARGET_DIRECTORIES = (
    EXAMPLES_ROOT / "01_standalone_sdk",
    EXAMPLES_ROOT / "02_remote_agent_server",
    # These examples live under subdirectories (each with a single `main.py`).
    EXAMPLES_ROOT / "05_skills_and_plugins" / "01_loading_agentskills",
    EXAMPLES_ROOT / "05_skills_and_plugins" / "02_loading_plugins",
)

# LLM-specific examples that require model overrides
_LLM_SPECIFIC_EXAMPLES: dict[str, dict[str, str]] = {
    "examples/04_llm_specific_tools/01_gpt5_apply_patch_preset.py": {
        "LLM_MODEL": "openhands/gpt-5.1",
    },
    "examples/04_llm_specific_tools/02_gemini_file_tools.py": {
        "LLM_MODEL": "openhands/gemini-3-pro-preview",
    },
}

# Examples that require interactive input or additional infrastructure.
_EXCLUDED_EXAMPLES = {
    "examples/01_standalone_sdk/01_hello_world.py",
    "examples/01_standalone_sdk/04_confirmation_mode_example.py",
    "examples/01_standalone_sdk/06_interactive_terminal_w_reasoning.py",
    "examples/01_standalone_sdk/08_mcp_with_oauth.py",
    "examples/01_standalone_sdk/15_browser_use.py",
    "examples/01_standalone_sdk/16_llm_security_analyzer.py",
    "examples/01_standalone_sdk/27_observability_laminar.py",
    "examples/01_standalone_sdk/35_subscription_login.py",
    "examples/02_remote_agent_server/04_vscode_with_docker_sandboxed_server.py",
}


def _discover_examples() -> list[Path]:
    candidates: list[Path] = []
    for directory in _TARGET_DIRECTORIES:
        if not directory.exists():
            continue
        candidates.extend(sorted(directory.glob("*.py")))
    # Append any explicitly listed LLM-specific examples if present
    for rel_path in _LLM_SPECIFIC_EXAMPLES.keys():
        abs_path = REPO_ROOT / rel_path
        if abs_path.exists():
            candidates.append(abs_path)
    return candidates


def _iter_examples() -> Iterable[Path]:
    excluded = {_normalize_path(REPO_ROOT / p) for p in _EXCLUDED_EXAMPLES}
    for example_path in _discover_examples():
        normalized = _normalize_path(example_path)
        if normalized in excluded:
            continue
        yield example_path


def _normalize_path(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace(os.sep, "/")


EXAMPLES = tuple(_iter_examples())


@pytest.mark.parametrize("example_path", EXAMPLES, ids=_normalize_path)
def test_example_scripts(
    example_path: Path,
    examples_enabled: bool,
    examples_results_dir: Path,
) -> None:
    if not examples_enabled:
        pytest.skip("Use --run-examples to execute example scripts.")

    rel_path = example_path.relative_to(REPO_ROOT)
    result_file = (
        examples_results_dir
        / f"{_normalize_path(example_path).replace('/', '__')}.json"
    )

    start = time.perf_counter()
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    # Apply model overrides for certain examples requiring provider-specific models
    overrides = _LLM_SPECIFIC_EXAMPLES.get(_normalize_path(example_path))
    if overrides:
        env.update(overrides)

    timed_out = False
    try:
        process = subprocess.run(  # noqa: S603
            [sys.executable, str(example_path)],
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=EXAMPLE_TIMEOUT_SECONDS,
        )
        stdout = process.stdout
        stderr = process.stderr
        returncode = process.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        # e.stdout/e.stderr are bytes|str|None; ensure we have str
        raw_stdout = e.stdout
        raw_stderr = e.stderr
        stdout = (
            raw_stdout.decode() if isinstance(raw_stdout, bytes) else (raw_stdout or "")
        )
        stderr = (
            raw_stderr.decode() if isinstance(raw_stderr, bytes) else (raw_stderr or "")
        )
        returncode = -1

    duration = time.perf_counter() - start

    cost = None
    for line in stdout.splitlines():
        if line.startswith("EXAMPLE_COST:"):
            cost = line.split("EXAMPLE_COST:", 1)[1].strip()
            break

    status = "passed"
    failure_reason = None

    if timed_out:
        status = "failed"
        failure_reason = f"Timed out after {EXAMPLE_TIMEOUT_SECONDS} seconds"
    elif returncode != 0:
        status = "failed"
        failure_reason = f"Exit code {returncode}"
    elif cost is None:
        status = "failed"
        failure_reason = "Missing EXAMPLE_COST marker in stdout"

    result_payload = {
        "example": _normalize_path(example_path),
        "status": status,
        "duration_seconds": duration,
        "cost": cost,
        "returncode": returncode,
        "failure_reason": failure_reason,
    }

    result_file.write_text(json.dumps(result_payload, indent=2))

    if status != "passed":
        pytest.fail(
            "Example script failed:\n"
            f"Example: {rel_path}\n"
            f"Reason: {failure_reason}\n"
            f"Stdout:\n{stdout}\n"
            f"Stderr:\n{stderr}"
        )
