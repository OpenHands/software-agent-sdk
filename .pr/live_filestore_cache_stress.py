from __future__ import annotations

import builtins
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from openhands.sdk.io.local import LocalFileStore


TIMING_ITERATIONS = 3000
MULTIFILE_ITERATIONS = 300
DIRECT_ITERATIONS = 3000


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def distribution(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "p50": statistics.median(values),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
    }


def start_load(load_file: Path) -> list[subprocess.Popen[bytes]]:
    load_file.write_bytes(b"f" * (16 * 1024 * 1024))
    processes = [
        subprocess.Popen(
            ["sha256sum", "/dev/zero"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(2)
    ]
    reader = (
        "import sys\n"
        "path=sys.argv[1]\n"
        "while True:\n"
        "  with open(path, 'rb', buffering=0) as stream:\n"
        "    while stream.read(1024 * 1024):\n"
        "      pass\n"
    )
    processes.append(
        subprocess.Popen(
            [sys.executable, "-c", reader, str(load_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    )
    return processes


def stop_load(processes: list[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def timing_cache_hit(store: LocalFileStore) -> dict[str, Any]:
    ratios: list[float] = []
    mismatches = 0
    expected = "x" * 100000
    for _ in range(TIMING_ITERATIONS):
        store.cache.clear()
        start = time.perf_counter()
        for _ in range(10):
            assert store.read("large_file.txt") == expected
        first = time.perf_counter() - start

        start = time.perf_counter()
        for _ in range(10):
            assert store.read("large_file.txt") == expected
        second = time.perf_counter() - start
        ratio = second / first
        ratios.append(ratio)
        mismatches += ratio >= 2.0

    return {
        "iterations": TIMING_ITERATIONS,
        "assertion": "second_pass_time < first_pass_time * 2",
        "mismatches": mismatches,
        "second_over_first": distribution(ratios),
    }


def timing_multifile(store: LocalFileStore) -> dict[str, Any]:
    speedups: list[float] = []
    mismatches = 0
    for _ in range(MULTIFILE_ITERATIONS):
        store.cache.clear()
        start = time.perf_counter()
        for index in range(50):
            store.read(f"file_{index}.txt")
        first = time.perf_counter() - start

        start = time.perf_counter()
        for index in range(50):
            store.read(f"file_{index}.txt")
        second = time.perf_counter() - start
        speedup = first / second
        speedups.append(speedup)
        mismatches += speedup <= 0.8

    return {
        "iterations": MULTIFILE_ITERATIONS,
        "assertion": "first_pass_time / second_pass_time > 0.8",
        "mismatches": mismatches,
        "first_over_second": distribution(speedups),
    }


def direct_read_assertions(root: Path) -> dict[str, Any]:
    store = LocalFileStore(str(root / "direct"), cache_limit_size=100)
    store.write("tracked.txt", "original")
    full_path = store.get_full_path("tracked.txt")
    real_open = builtins.open
    opens: list[str] = []

    def tracked_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened = os.path.abspath(os.fsdecode(os.fspath(file)))
        if opened == full_path:
            opens.append(opened)
        return real_open(file, *args, **kwargs)

    builtins.open = tracked_open
    try:
        for iteration in range(DIRECT_ITERATIONS):
            before = len(opens)
            store.cache.clear()
            assert store.read("tracked.txt") == "original"
            assert len(opens) == before + 1
            for _ in range(10):
                assert store.read("tracked.txt") == "original"
            assert len(opens) == before + 1

        stale_before = len(opens)
        with real_open(full_path, "w") as stream:
            stream.write("changed-on-disk")
        cached = store.read("tracked.txt")
        stale_open_count = len(opens)
        store.cache.clear()
        refreshed = store.read("tracked.txt")
        refreshed_open_count = len(opens)
    finally:
        builtins.open = real_open

    return {
        "iterations": DIRECT_ITERATIONS,
        "expected_disk_opens": DIRECT_ITERATIONS,
        "observed_disk_opens_before_stale_probe": stale_before,
        "cached_after_external_change": cached,
        "opens_while_returning_cached_value": stale_open_count - stale_before,
        "refreshed_after_cache_clear": refreshed,
        "opens_for_refresh": refreshed_open_count - stale_open_count,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="pr4088-stress-") as temp:
        root = Path(temp)
        store = LocalFileStore(str(root / "timing"), cache_limit_size=100)
        store.write("large_file.txt", "x" * 100000)
        for index in range(50):
            store.write(f"file_{index}.txt", f"Test content {index}\n" * 500)

        test_source = Path("tests/sdk/io/test_filestore_cache.py").read_text()
        filesystem = subprocess.run(
            ["findmnt", "-n", "-o", "FSTYPE", "--target", temp],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        processes = start_load(root / "filesystem-load.bin")
        try:
            result = {
                "test_style": (
                    "direct-open-counts"
                    if "_track_open_calls" in test_source
                    else "perf-counter-thresholds"
                ),
                "filesystem": filesystem,
                "cpu_count": os.cpu_count(),
                "load_processes": ["sha256sum /dev/zero"] * 2
                + ["continuous 16MiB filesystem reads"],
                "timing_cache_hit": timing_cache_hit(store),
                "timing_multifile": timing_multifile(store),
                "direct_read_assertions": direct_read_assertions(root),
            }
        finally:
            stop_load(processes)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
