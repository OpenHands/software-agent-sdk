"""End-to-end reproducer for PR #2738: browser crash detection & recovery.

Spins up a real BrowserToolExecutor, does one successful navigation, FREEZES
the Chromium child processes with SIGSTOP (so the tool hangs and actually
times out), then verifies:

  1. Three consecutive calls after the freeze each return is_error=True and time out.
  2. On the third failure, _initialized flips to False and the error message
     mentions "reset".
  3. The _consecutive_failures counter resets to 0 after the reset.
  4. The very next call transparently re-initializes the browser and succeeds.

Why SIGSTOP and not SIGKILL?
  SIGKILL closes the Chromium WebSocket, and `browser_use` catches the
  resulting ConnectionClosedError internally and returns a canned success-
  looking observation with is_error=False. The BrowserToolExecutor never
  sees a TimeoutError, so PR #2738's crash detection never fires.

  SIGSTOP freezes Chromium while leaving the WebSocket alive. Commands are
  sent but never acknowledged, so the tool's async_executor.run_async hits
  its timeout budget, raises TimeoutError, and the recovery logic kicks in.
  This is the closest analog to the "hung browser" scenario issue #2412
  originally described.

Prereqs:
  - checkout of OpenHands/software-agent-sdk at fix/browser-crash-recovery
  - `uv sync` (pulls playwright + chromium)
  - `uv pip install psutil`

Run from repo root:
  uv run python .pr/test_browser_crash_recovery.py
"""

from __future__ import annotations

import os
import signal
import time

import psutil

# Shrink the degraded timeout constant so the demo completes in under a minute.
# The constant is imported by value into BrowserToolExecutor methods via the
# module, so we must patch it on the module itself, before any call.
import openhands.tools.browser_use.impl as impl


impl.DEGRADED_TIMEOUT_SECONDS = 3.0  # down from 30.0
ACTION_TIMEOUT = 6.0  # down from 300.0; first 2 failures wait this long

from openhands.tools.browser_use.definition import BrowserNavigateAction  # noqa: E402
from openhands.tools.browser_use.impl import (  # noqa: E402
    MAX_CONSECUTIVE_FAILURES,
    BrowserToolExecutor,
)


def _chromium_children() -> list[psutil.Process]:
    """Return all Chromium/Chrome descendants of this process."""
    me = psutil.Process(os.getpid())
    out: list[psutil.Process] = []
    for child in me.children(recursive=True):
        try:
            name = (child.name() or "").lower()
        except psutil.NoSuchProcess:
            continue
        if "chrom" in name or "headless_shell" in name:
            out.append(child)
    return out


def freeze_chromium_children() -> int:
    """SIGSTOP every Chromium descendant so the WebSocket hangs unresponsive."""
    count = 0
    for child in _chromium_children():
        try:
            child.send_signal(signal.SIGSTOP)
            count += 1
            print(f"  SIGSTOP pid={child.pid} name={child.name()!r}")
        except psutil.NoSuchProcess:
            pass
    return count


def kill_chromium_children() -> int:
    """SIGKILL every Chromium descendant. Used only for final cleanup."""
    count = 0
    for child in _chromium_children():
        try:
            # Unfreeze first so the kill isn't ignored (SIGSTOPed procs still
            # accept SIGKILL on most kernels, but CONT + KILL is safest).
            try:
                child.send_signal(signal.SIGCONT)
            except psutil.NoSuchProcess:
                pass
            child.kill()
            count += 1
        except psutil.NoSuchProcess:
            pass
    return count


def banner(msg: str) -> None:
    print(f"\n{'=' * 72}\n{msg}\n{'=' * 72}")


def describe(label: str, executor: BrowserToolExecutor, result, elapsed: float) -> None:
    print(
        f"[{label}] is_error={result.is_error} "
        f"initialized={executor._initialized} "
        f"consecutive_failures={executor._consecutive_failures} "
        f"elapsed={elapsed:.1f}s"
    )
    text = (result.text or "").replace("\n", " ")
    print(f"         text[:200]={text[:200]!r}")


def main() -> int:
    banner("Setup: create BrowserToolExecutor")
    executor = BrowserToolExecutor(
        headless=True,
        action_timeout_seconds=ACTION_TIMEOUT,
    )
    action = BrowserNavigateAction(url="https://example.com")
    print(f"MAX_CONSECUTIVE_FAILURES={MAX_CONSECUTIVE_FAILURES}")
    print(f"action_timeout_seconds={ACTION_TIMEOUT}")
    print(f"DEGRADED_TIMEOUT_SECONDS={impl.DEGRADED_TIMEOUT_SECONDS}")

    # ---- Baseline: one successful navigation ----
    banner("Call 1 — baseline navigation (expect success)")
    t0 = time.time()
    r = executor(action)
    describe("baseline", executor, r, time.time() - t0)
    assert not r.is_error, "baseline nav failed — cannot continue"
    assert executor._initialized is True
    assert executor._consecutive_failures == 0

    # ---- Freeze the browser ----
    banner("Simulate hang: SIGSTOP all Chromium descendants")
    frozen = freeze_chromium_children()
    print(f"  frozen {frozen} process(es)")
    if frozen == 0:
        print("  WARNING: no Chromium children found — browser may be remote/proxied")
    time.sleep(0.5)

    # ---- Three calls that should time out against the hung browser ----
    # Per-call expectations: (label, counter, _initialized, reset_text?, timeout_secs)
    # The 3rd call uses DEGRADED_TIMEOUT_SECONDS; verified via error text, NOT
    # wall-clock elapsed — the reset path adds up-to-5s of cleanup budget on top,
    # so elapsed would be ~(DEGRADED + 5), not DEGRADED alone.
    failure_expectations = [
        ("failure 1/3", 1, True, False, ACTION_TIMEOUT),
        ("failure 2/3", 2, True, False, ACTION_TIMEOUT),
        ("failure 3/3", 0, False, True, impl.DEGRADED_TIMEOUT_SECONDS),
    ]
    for (
        label,
        want_counter,
        want_initialized,
        want_reset_text,
        want_timeout,
    ) in failure_expectations:
        banner(f"Call after crash — {label}")
        t0 = time.time()
        r = executor(action)
        dt = time.time() - t0
        describe(label, executor, r, dt)
        assert r.is_error is True, f"{label}: expected is_error=True"
        assert executor._consecutive_failures == want_counter, (
            f"{label}: counter={executor._consecutive_failures}, want {want_counter}"
        )
        assert executor._initialized is want_initialized, (
            f"{label}: _initialized={executor._initialized}, want {want_initialized}"
        )
        expected_phrase = f"timed out after {int(want_timeout)} seconds"
        assert expected_phrase in r.text, (
            f"{label}: expected {expected_phrase!r} in error text, got {r.text!r}"
        )
        if want_reset_text:
            assert "reset" in r.text.lower(), (
                f"{label}: expected 'reset' in error text, got {r.text!r}"
            )
    print(
        f"\nDegraded-timeout check OK: call 3 error text reports "
        f"{int(impl.DEGRADED_TIMEOUT_SECONDS)}-second timeout "
        f"(vs {int(ACTION_TIMEOUT)}s normal)."
    )

    # ---- Before testing recovery, finish killing the frozen Chromium -----
    # Detection requires a HUNG browser (SIGSTOP) to produce TimeoutErrors,
    # but issue #2412 targets CRASHED browsers. The PR's in-reset cleanup
    # couldn't fully tear down the hung session (the BrowserKillEvent ack
    # never arrives, so the 5s cleanup budget expires). That leaves
    # self._server holding a poisoned EventBus. Simulate the real-world
    # post-crash state by fully killing the stale processes here so
    # _ensure_initialized() on the next call can come up cleanly.
    #
    # OPEN QUESTION for the PR: should _handle_timeout_failure also null
    # out self._server (not just _initialized) so recovery works when the
    # in-reset cleanup itself times out against a hung browser? Without
    # that, hung-but-not-dead browsers will re-fail immediately after the
    # "reset" message. See the log line "EventBus_<same-id>" reused across
    # pre-reset and post-reset events.
    banner("Pre-recovery: SIGCONT+SIGKILL the stale Chromium to match #2412")
    killed = kill_chromium_children()
    print(f"  killed {killed} stale process(es)")
    time.sleep(0.5)

    # ---- The next call must re-initialize a fresh browser and succeed ----
    banner("Call 5 — expect automatic re-initialization + success")
    t0 = time.time()
    r = executor(action)
    dt = time.time() - t0
    describe("recovery", executor, r, dt)
    assert not r.is_error, f"recovery nav failed: {r.text!r}"
    assert executor._initialized is True, "executor did not re-initialize"
    assert executor._consecutive_failures == 0

    banner("✓ PASS: browser crash detection + recovery works end-to-end")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        # best-effort cleanup
        try:
            kill_chromium_children()
        except Exception:
            pass
