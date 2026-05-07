"""Stress test: long-running bash command must not block the event loop.

Bug class this catches:
    - Blocking I/O in the async path during a long bash command (sync subprocess
      calls instead of asyncio.subprocess).
    - Leaked PTYs / zombies after the command finishes or times out.
    - The agent-server losing responsiveness on /health while bash runs.

API gap (documented):
    The bash router exposes ``POST /api/bash/start_bash_command`` (background)
    and ``DELETE /api/bash/bash_events`` (clear all), but **no per-command
    kill/cancel endpoint**. The proposal's "cancel returns < 1s" assertion
    cannot be tested through the public API today. The closest substitute is
    the ``timeout`` field on ExecuteBashRequest, which forces the service to
    SIGKILL the process after a deadline (bash_service.py:274). We exercise
    that code path here. A real cancel endpoint would warrant a separate test.

CI mode:
    ``--stress-quick`` (default): 5s. ``--stress-full`` would bump to 1800s
    per the proposal. We don't gate on the long path here; that's a
    separate workflow.
"""

import asyncio
import os
import statistics
import time
from uuid import UUID

import psutil
import pytest

from openhands.agent_server.bash_service import BashEventService
from tests.agent_server.stress.budgets import LONG_RUNNING_COMMAND


pytestmark = pytest.mark.stress


def _descendants_of(pid: int) -> list[psutil.Process]:
    """All recursive descendants of ``pid``. Empty if pid is gone."""
    try:
        return psutil.Process(pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return []


async def test_long_running_bash_does_not_block_event_loop(
    client,
    bash_service: BashEventService,
):
    """While bash runs, /health must stay responsive and the process tree
    must clean up after the command ends or times out."""
    duration = LONG_RUNNING_COMMAND.duration_s

    # Start a command that stays alive for ``duration`` seconds and emits a
    # final marker. We give the service a slightly larger timeout so the
    # natural-exit path runs (we test the timeout path separately below).
    pre_children = set(p.pid for p in _descendants_of(os.getpid()))
    resp = await client.post(
        "/api/bash/start_bash_command",
        json={
            "command": f"sleep {duration}; echo done",
            "timeout": duration + 5,
        },
    )
    assert resp.status_code == 200, resp.text
    cmd_id = UUID(resp.json()["id"])

    # Sample /health continuously while the bash command is running. A
    # pre-loop burst of N requests would finish in ~100 ms (in-process ASGI),
    # so blocking that happens later in the 5 s window would go unobserved.
    # Interleaving with the completion-poll spreads samples across the full
    # bash lifetime.
    health_lats: list[float] = []
    deadline = time.monotonic() + duration + 10
    while time.monotonic() < deadline:
        for _ in range(5):
            t0 = time.monotonic()
            h_resp = await client.get("/health")
            health_lats.append(time.monotonic() - t0)
            assert h_resp.status_code == 200

        events = await client.get(
            "/api/bash/bash_events/search",
            params={"command_id__eq": str(cmd_id)},
        )
        items = events.json()["items"]
        # Final BashOutput carries exit_code != null.
        final = next(
            (
                e
                for e in items
                if e["kind"] == "BashOutput" and e.get("exit_code") is not None
            ),
            None,
        )
        if final is not None:
            assert final["exit_code"] == 0
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(f"command {cmd_id} did not finish within {duration + 10}s")

    # 1. /health stayed responsive throughout. p95 budget catches event-loop
    #    starvation; failures here typically indicate sync subprocess.* in
    #    the async path.
    p95 = statistics.quantiles(health_lats, n=20)[-1]
    assert p95 < LONG_RUNNING_COMMAND.health_p95_s, (
        f"/health p95 {p95 * 1000:.1f} ms during running bash exceeded "
        f"{LONG_RUNNING_COMMAND.health_p95_s * 1000:.0f} ms. The event loop "
        f"is probably being blocked by the bash command's I/O."
    )

    # 2. No descendant processes leaked. The bash subprocess and any of its
    #    children must be reaped within cleanup_timeout_s of the command's
    #    completion.
    cleanup_deadline = time.monotonic() + LONG_RUNNING_COMMAND.cleanup_timeout_s
    leaked: set[int] = set()
    while time.monotonic() < cleanup_deadline:
        post_children = set(p.pid for p in _descendants_of(os.getpid()))
        leaked = post_children - pre_children
        if not leaked:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail(
            f"after {LONG_RUNNING_COMMAND.cleanup_timeout_s}s, descendants of "
            f"the test process include unexpected pids: {leaked}. Bash "
            f"subprocess teardown is leaking children."
        )


async def test_bash_timeout_kills_process_cleanly(
    client,
    bash_service: BashEventService,
):
    """A command that exceeds its ``timeout`` is SIGKILLed, exit_code reported,
    no zombie left in the descendant tree.

    This is the closest available substitute for an explicit cancel; see the
    module docstring for the API gap.
    """
    pre_children = set(p.pid for p in _descendants_of(os.getpid()))

    resp = await client.post(
        "/api/bash/start_bash_command",
        json={
            "command": "sleep 30",
            "timeout": 1,  # forces the timeout-kill path
        },
    )
    assert resp.status_code == 200, resp.text
    cmd_id = UUID(resp.json()["id"])

    # Wait for the timeout to fire and the kill to propagate.
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        events = await client.get(
            "/api/bash/bash_events/search",
            params={"command_id__eq": str(cmd_id)},
        )
        items = events.json()["items"]
        final = next(
            (
                e
                for e in items
                if e["kind"] == "BashOutput" and e.get("exit_code") is not None
            ),
            None,
        )
        if final is not None:
            # exit_code == -1 is the bash_service signal for "timed out and
            # SIGKILLed" (bash_service.py:289).
            assert final["exit_code"] == -1, (
                f"expected exit_code -1 (timeout-kill), got {final['exit_code']}"
            )
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("timeout-killed command never produced a final event")

    cleanup_deadline = time.monotonic() + LONG_RUNNING_COMMAND.cleanup_timeout_s
    leaked: set[int] = set()
    while time.monotonic() < cleanup_deadline:
        post_children = set(p.pid for p in _descendants_of(os.getpid()))
        leaked = post_children - pre_children
        if not leaked:
            return
        await asyncio.sleep(0.1)
    pytest.fail(
        f"after timeout-kill, descendants still include {leaked}. "
        f"SIGKILL path is leaving zombies."
    )
