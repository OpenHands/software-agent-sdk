"""Test that importing openhands.sdk completes within a reasonable time.

This is a performance regression guard: it spawns a fresh Python process
so that the measurement is not affected by modules already imported by the
pytest session.
"""

import subprocess
import sys


# Upper bound (seconds) for `import openhands.sdk` in a cold process.
# Kept generous so CI machines don't flake, while still catching
# accidental heavy eager imports (e.g. loading Laminar at import time).
IMPORT_TIME_LIMIT_SECONDS = 10.0

# Number of subprocess runs to average over.
_ITERATIONS = 5


def _measure_import_time_seconds() -> float:
    """Return wall-clock seconds to `import openhands.sdk` in a subprocess."""
    code = (
        "import time; "
        "start = time.perf_counter(); "
        "import openhands.sdk; "
        "elapsed = time.perf_counter() - start; "
        "print(elapsed)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        env=None,  # inherit current env
    )
    assert result.returncode == 0, (
        f"Import subprocess failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    return float(result.stdout.strip())


def test_import_openhands_sdk_time():
    """Import of openhands.sdk must complete under the time limit."""
    times = [_measure_import_time_seconds() for _ in range(_ITERATIONS)]
    avg = sum(times) / len(times)
    print(
        f"\n[import-perf] openhands.sdk import times (s): {[f'{t:.3f}' for t in times]}"
    )
    print(f"[import-perf] average: {avg:.3f}s (limit: {IMPORT_TIME_LIMIT_SECONDS}s)")
    assert avg < IMPORT_TIME_LIMIT_SECONDS, (
        f"Average import time {avg:.3f}s exceeded {IMPORT_TIME_LIMIT_SECONDS}s limit. "
        f"Individual runs: {times}"
    )
