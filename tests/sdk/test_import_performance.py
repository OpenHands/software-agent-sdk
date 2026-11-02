"""Import-cost guards for ``openhands.sdk``.

Three separate regressions are guarded here:

1. Wall time and peak RSS. A cold ``import openhands.sdk`` is seconds and
   hundreds of MB, all of it the eager import graph rather than any work.
2. The provider stack. ``litellm`` is most of that cost. A process that never
   calls a model should not pay it.
3. Process fan-out. A fresh interpreter per unit of work multiplies the import
   cost by the worker count. Forked workers should share the imported pages
   copy-on-write, so the per-child cost must stay far below the parent's.

Every measurement runs in a fresh subprocess, so it is unaffected by modules the
pytest session has already imported.
"""

import os
import statistics
import subprocess
import sys
import textwrap

import pytest


# Upper bound (seconds) for a cold `import openhands.sdk`. Kept well above the
# measured value so a slow CI machine does not flake, while still catching an
# accidental heavy eager import.
IMPORT_TIME_LIMIT_SECONDS = 8.0

# Upper bound (MiB) for peak RSS of a cold `import openhands.sdk`. Generous on
# purpose, but a litellm-sized re-addition pushes past it.
IMPORT_RSS_LIMIT_MIB = 600.0

_ITERATIONS = 5

# Removing litellm from the import graph (issue #6366, step 3) is blocked by the
# public `LLMStreamChunk` alias, so this is an expected failure rather than a red
# test until that is resolved.
_LITELLM_DEFERRAL_XFAIL = pytest.mark.xfail(
    strict=False,
    reason=(
        "openhands.sdk still imports litellm: the public alias "
        "LLMStreamChunk = litellm.types.utils.ModelResponseStream is exported "
        "from openhands.sdk.__all__, so it cannot be deferred without changing "
        "the public surface (rejected in #2937)."
    ),
)


def _run_snippet(body: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter with the banner suppressed."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(os.environ, OPENHANDS_SUPPRESS_BANNER="1"),
    )


def _measure_cold_import(snippet: str) -> tuple[float, float]:
    """Return (median wall seconds, peak RSS MiB) for a cold import."""
    body = f"""
        import resource, time
        start = time.perf_counter()
        {snippet}
        elapsed = time.perf_counter() - start
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        print(f"RESULT {{elapsed}} {{rss}}")
    """
    times: list[float] = []
    rss_mib = 0.0
    for _ in range(_ITERATIONS):
        result = _run_snippet(body)
        assert result.returncode == 0, (
            f"Import subprocess failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        line = next(ln for ln in result.stdout.splitlines() if ln.startswith("RESULT "))
        _, elapsed, rss = line.split()
        times.append(float(elapsed))
        rss_mib = float(rss)
    return statistics.median(times), rss_mib


def test_import_openhands_sdk_time_and_memory():
    """Cold import of openhands.sdk must fit the time and RSS budget."""
    elapsed, rss_mib = _measure_cold_import("import openhands.sdk")
    print(
        f"\n[import-perf] openhands.sdk: {elapsed:.3f}s "
        f"(limit {IMPORT_TIME_LIMIT_SECONDS}s), {rss_mib:.1f} MiB "
        f"(limit {IMPORT_RSS_LIMIT_MIB} MiB)"
    )
    assert elapsed < IMPORT_TIME_LIMIT_SECONDS, (
        f"Cold import took {elapsed:.3f}s, over the "
        f"{IMPORT_TIME_LIMIT_SECONDS}s budget."
    )
    assert rss_mib < IMPORT_RSS_LIMIT_MIB, (
        f"Cold import peaked at {rss_mib:.1f} MiB, over the "
        f"{IMPORT_RSS_LIMIT_MIB} MiB budget."
    )


@pytest.mark.parametrize(
    "snippet",
    ["import openhands.sdk", "import openhands.sdk, openhands.tools"],
)
@_LITELLM_DEFERRAL_XFAIL
def test_import_does_not_pull_litellm(snippet: str):
    """A cold import must leave no litellm module behind."""
    body = f"""
        import sys
        {snippet}
        leaked = sorted(
            m for m in sys.modules if m == "litellm" or m.startswith("litellm.")
        )
        print("LEAKED", len(leaked))
        if leaked:
            print("FIRST", leaked[0])
    """
    result = _run_snippet(body)
    assert result.returncode == 0, result.stderr
    line = next(ln for ln in result.stdout.splitlines() if ln.startswith("LEAKED "))
    count = int(line.split()[1])
    assert count == 0, f"{snippet} loaded {count} litellm modules"


@pytest.mark.skipif(
    sys.platform != "linux", reason="reads PSS from /proc/<pid>/smaps_rollup"
)
def test_forked_workers_share_import_pages():
    """Forked children must share the imported SDK pages, not copy them.

    Summed RSS overstates a fork model, because shared copy-on-write pages are
    counted once per child. PSS divides a shared page among its sharers, so the
    children's PSS sum staying well below ``N x parent PSS`` is the signal that
    the import is genuinely shared.
    """
    body = """
        import os, time

        import openhands.sdk  # noqa: F401

        def pss_mib() -> float:
            with open("/proc/self/smaps_rollup") as handle:
                for line in handle:
                    if line.startswith("Pss:"):
                        return int(line.split()[1]) / 1024
            raise RuntimeError("Pss not found")

        n = 8
        parent = pss_mib()
        read_fd, write_fd = os.pipe()
        pids = []
        for _ in range(n):
            pid = os.fork()
            if pid == 0:
                os.close(read_fd)
                time.sleep(0.4)
                # Newline-terminated; the parent reads to EOF, so no record is
                # dropped mid-read.
                os.write(write_fd, f"{pss_mib()}\\n".encode())
                os._exit(0)
            pids.append(pid)
        os.close(write_fd)
        data = b""
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            data += chunk
        for pid in pids:
            os.waitpid(pid, 0)
        children = [float(x) for x in data.split()]
        print("PARENT", parent)
        print("CHILDREN", sum(children))
        print("N", len(children))
    """
    result = _run_snippet(body)
    assert result.returncode == 0, result.stderr
    out = dict(
        (ln.split()[0], float(ln.split()[1]))
        for ln in result.stdout.splitlines()
        if ln.split() and ln.split()[0] in {"PARENT", "CHILDREN", "N"}
    )
    parent, children, n = out["PARENT"], out["CHILDREN"], int(out["N"])
    print(
        f"\n[import-perf] fork: parent {parent:.1f} MiB, "
        f"{n} children {children:.1f} MiB total"
    )
    assert n == 8, f"expected 8 children to report, got {n}"
    # Without sharing each child would cost about a parent, so the children sum
    # would approach N x parent. With sharing it stays far below.
    assert children < n * parent * 0.5, (
        f"{n} forked children used {children:.1f} MiB PSS, close to the "
        f"unshared {n * parent:.1f} MiB; the SDK import is not being shared."
    )
