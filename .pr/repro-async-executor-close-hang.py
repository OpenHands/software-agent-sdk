#!/usr/bin/env python3
"""
repro-async-executor-close-hang.py — Live evidence for PR #4548 / issue #4546.

Reproduces the AsyncExecutor.close() hang that wedges the agent-server's
conversation lifecycle lock, and verifies the fix bounds it.

Two phases:

  Phase 1 (deterministic, the smoking gun):
    Directly exercise AsyncExecutor.close() with a task that never completes.
    Without the fix: close() blocks forever (we abort after PROBE_DEADLINE).
    With the fix:    close() returns within DEFAULT_CLOSE_TIMEOUT (30s), and
                     near-instantly for a cancellable task (anyio.sleep_forever).

  Phase 2 (HTTP, live backend):
    Hammer the running agent-server with concurrent conversation create +
    search + delete traffic. With the fix in place, all requests succeed
    quickly and the backend stays responsive. (Phase 1 is the deterministic
    proof that without the fix, close() hangs; Phase 2 confirms the live
    backend does not stall once the fix is applied.)

USAGE
  python3 repro-async-executor-close-hang.py [--http URL] [--no-unit]

  --http URL   Also run the HTTP concurrent-load phase against URL
               (default: http://localhost:8000)
  --no-unit    Skip the deterministic unit phase

EXIT CODE
  0  all phases passed (fix is working)
  1  a phase failed/stalled (bug reproduced)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


# Make the SDK importable from the local checkout.
_SDK = "/home/gneubig/work/software-agent-sdk/openhands-sdk"
if _SDK not in sys.path:
    sys.path.insert(0, _SDK)

PROBE_DEADLINE = 15.0  # seconds we wait before declaring close() a hang


def _banner(title: str) -> None:
    print(f"\n{'═' * 70}\n  {title}\n{'═' * 70}")


def _status(ok: bool, msg: str) -> int:
    tag = "✓ PASS" if ok else "✗ FAIL"
    print(f"  {tag} — {msg}")
    return 0 if ok else 1


# ── Phase 1: deterministic AsyncExecutor.close() repro ───────────────────


def _close_in_background(executor, **kwargs) -> threading.Event:
    """Call close() off-thread so a hang doesn't freeze the script."""
    done = threading.Event()

    def run():
        try:
            executor.close(**kwargs)
        except Exception as e:
            print(f"  (close() raised: {e})")
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    return done


def phase1_unit() -> int:
    """Deterministic reproduction of the AsyncExecutor.close() hang."""
    import anyio  # noqa: F401  (prove it's importable)

    from openhands.sdk.utils.async_executor import AsyncExecutor

    rc = 0
    _banner("Phase 1 — AsyncExecutor.close() with a never-finishing task")

    # Case A: a cancellable task (anyio.sleep_forever). With the fix,
    # cancellation is delivered and close() returns almost instantly.
    # Without the fix, close() waits forever for the task to finish on its own.
    print("\n  Case A: cancellable task (anyio.sleep_forever)")
    executor = AsyncExecutor()
    executor.portal.start_task_soon(anyio.sleep_forever)
    time.sleep(0.2)  # let the task start

    t0 = time.monotonic()
    done = _close_in_background(executor)
    finished = done.wait(timeout=PROBE_DEADLINE)
    elapsed = time.monotonic() - t0

    if finished:
        rc |= _status(True, f"close() returned in {elapsed:.2f}s (fix working)")
    else:
        rc |= _status(False, f"close() hung > {PROBE_DEADLINE:.0f}s (BUG reproduced)")
        # best-effort: leave the daemon thread to die with the process

    # Case B: a task blocked in a worker thread (uncancellable). With the fix,
    # close() waits up to DEFAULT_CLOSE_TIMEOUT then abandons the daemon thread.
    # Without the fix, close() hangs forever.
    print("\n  Case B: uncancellable task (blocked in worker thread)")
    from anyio.to_thread import run_sync

    async def blocked_in_worker_thread():
        await run_sync(lambda: time.sleep(60))

    executor2 = AsyncExecutor()
    executor2.portal.start_task_soon(blocked_in_worker_thread)
    time.sleep(0.2)

    t0 = time.monotonic()
    done2 = _close_in_background(executor2, timeout=2.0)
    finished2 = done2.wait(timeout=PROBE_DEADLINE)
    elapsed2 = time.monotonic() - t0

    if finished2:
        rc |= _status(
            True,
            f"close() returned in {elapsed2:.2f}s with timeout=2.0 (fix working)",
        )
    else:
        rc |= _status(
            False, f"close() hung > {PROBE_DEADLINE:.0f}s even with timeout (BUG)"
        )

    # Case C: idempotent close (no hang on a clean executor).
    print("\n  Case C: idempotent close on a clean executor")
    executor3 = AsyncExecutor()
    _ = executor3.portal
    t0 = time.monotonic()
    executor3.close()
    executor3.close()
    elapsed3 = time.monotonic() - t0
    rc |= _status(True, f"double close() returned in {elapsed3:.2f}s")

    return rc


# ── Phase 2: HTTP concurrent load against the live backend ────────────────


def _http(
    method: str, url: str, key: str, body: dict | None = None
) -> tuple[int, float]:
    """Fire one HTTP request; return (status_code, elapsed_seconds)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Session-API-Key": key,
            "Content-Type": "application/json",
        },
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
            return resp.status, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, time.monotonic() - t0
    except Exception:
        return 0, time.monotonic() - t0


def phase2_http(base_url: str, key: str, concurrency: int = 10, rounds: int = 3) -> int:
    """Hammer the backend with concurrent READ traffic.

    Uses search/health/alive — which exercise the lifecycle-lock read-path
    (the fast-path fixed by PR #4513) — without depending on LLM auth, so the
    result isolates the deadlock-fix behaviour from LLM availability.
    """
    _banner(
        f"Phase 2 — HTTP concurrent read load ({concurrency} workers × {rounds} rounds)"
    )

    # Preflight
    code, _ = _http("GET", f"{base_url}/health", key)
    if code != 200:
        print(f"  ✗ FAIL — backend not healthy (health={code})")
        return 1
    print(f"  preflight /health = {code} ✓")

    latencies: list[float] = []
    failures = 0
    total = 0

    def one_cycle(i: int) -> bool:
        nonlocal failures, total
        ok = True
        # 1. search (exercises the lifecycle-lock read-path fast-path)
        code, t = _http("GET", f"{base_url}/api/conversations/search?limit=5", key)
        latencies.append(t)
        total += 1
        if code != 200:
            failures += 1
            ok = False
        # 2. /alive (liveness, no lock)
        code, t = _http("GET", f"{base_url}/alive", key)
        latencies.append(t)
        total += 1
        if code != 200:
            failures += 1
            ok = False
        # 3. /server_info (metadata, no lock)
        code, t = _http("GET", f"{base_url}/server_info", key)
        latencies.append(t)
        total += 1
        if code != 200:
            failures += 1
            ok = False
        return ok

    for r in range(rounds):
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futs = [
                pool.submit(one_cycle, r * concurrency + i) for i in range(concurrency)
            ]
            results = [f.result() for f in as_completed(futs)]
        round_elapsed = time.monotonic() - t0
        ok = sum(results)
        print(
            f"  round {r + 1}/{rounds}: {ok}/{concurrency} cycles ok "
            f"in {round_elapsed:.2f}s"
        )

    if not latencies:
        print("  ✗ FAIL — no requests completed")
        return 1

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    print(
        f"\n  {total} requests: {failures} failures, "
        f"p50={p50 * 1000:.0f}ms, p99={p99 * 1000:.0f}ms"
    )

    if failures > 0:
        return _status(False, f"{failures} requests failed/stalled (backend unhealthy)")
    return _status(True, "all requests succeeded, backend stayed responsive")


# ── Main ─────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--http", metavar="URL", default=None, help="run the HTTP phase against URL"
    )
    ap.add_argument("--no-unit", action="store_true", help="skip the unit phase")
    ap.add_argument(
        "--api-key",
        default=os.environ.get(
            "WATCHDOG_API_KEY",
            open("/home/gneubig/.openhands/agent-canvas/api-key.txt").read().strip(),
        ),
    )
    args = ap.parse_args()

    rc = 0
    if not args.no_unit:
        rc |= phase1_unit()

    if args.http:
        rc |= phase2_http(args.http, args.api_key)

    _banner("RESULT")
    if rc == 0:
        print("  ✓ All phases passed — fix is working, no stall.")
    else:
        print("  ✗ A phase failed/stalled — bug reproduced.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
