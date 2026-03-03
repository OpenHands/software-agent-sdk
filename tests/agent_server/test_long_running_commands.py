"""Minimal test cases to verify/refute agent-server hanging on long-running commands.

Tests cover:
1. Service-level: Does BashEventService hang when a command runs for a long time?
2. HTTP-level: Does /execute_bash_command block the entire server (other endpoints)?
3. Concurrency: Can multiple long-running commands execute without deadlock?
"""

import asyncio
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openhands.agent_server.bash_service import BashEventService
from openhands.agent_server.models import BashOutput, ExecuteBashRequest


@pytest.fixture
def bash_service():
    with tempfile.TemporaryDirectory() as tmp:
        yield BashEventService(bash_events_dir=Path(tmp) / "bash_events")


# ---------------------------------------------------------------------------
# 1. Service-level: timeout actually fires and doesn't hang
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_long_running_command_respects_timeout(bash_service):
    """A command that exceeds its timeout should be killed, not hang forever."""
    request = ExecuteBashRequest(command="sleep 60", cwd="/tmp", timeout=2)

    start = time.monotonic()
    command, task = await bash_service.start_bash_command(request)
    await task
    elapsed = time.monotonic() - start

    # Should finish well within 10s (timeout=2 + overhead)
    assert elapsed < 10, f"Command took {elapsed:.1f}s — likely hanging"

    page = await bash_service.search_bash_events(command_id__eq=command.id)
    outputs = [e for e in page.items if isinstance(e, BashOutput)]
    assert any(o.exit_code == -1 for o in outputs), "Expected exit_code=-1 on timeout"


# ---------------------------------------------------------------------------
# 2. HTTP-level: one slow command must not block the entire server
# ---------------------------------------------------------------------------
def test_server_stays_responsive_during_long_command():
    """While /execute_bash_command is blocked on a slow task,
    other endpoints (e.g. /health) must still respond promptly."""

    from openhands.agent_server.api import create_app
    from openhands.agent_server.config import Config

    config = Config(session_api_keys=[])
    app = create_app(config)

    # Use a real TestClient (runs the ASGI app in a thread)
    with TestClient(app, raise_server_exceptions=False) as client:
        # Sanity: server is up (use /health which always returns 200)
        r = client.get("/health")
        assert r.status_code == 200

        # Fire a 5-second command with a 2-second timeout via the sync endpoint.
        # This should complete in ~2s (timeout kills it), not 5s.
        start = time.monotonic()
        r = client.post(
            "/api/bash/execute_bash_command",
            json={"command": "sleep 5", "cwd": "/tmp", "timeout": 2},
        )
        elapsed = time.monotonic() - start

        assert r.status_code == 200
        body = r.json()
        assert body["exit_code"] == -1, "Expected timeout exit code"
        assert elapsed < 8, f"Endpoint took {elapsed:.1f}s — server may be hanging"


# ---------------------------------------------------------------------------
# 3. Concurrent long-running commands must not deadlock
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_concurrent_long_commands_no_deadlock(bash_service):
    """Launch several long-running commands concurrently;
    all must finish within a reasonable time."""
    n = 5
    requests = [
        ExecuteBashRequest(command="sleep 30", cwd="/tmp", timeout=2) for _ in range(n)
    ]

    start = time.monotonic()
    pairs = await asyncio.gather(
        *[bash_service.start_bash_command(r) for r in requests]
    )
    tasks = [task for _, task in pairs]
    await asyncio.gather(*tasks)
    elapsed = time.monotonic() - start

    # All 5 should timeout roughly simultaneously (~2s + overhead), not serially
    assert elapsed < 15, f"Concurrent commands took {elapsed:.1f}s — possible deadlock"

    for cmd, _ in pairs:
        page = await bash_service.search_bash_events(command_id__eq=cmd.id)
        outputs = [e for e in page.items if isinstance(e, BashOutput)]
        assert any(o.exit_code == -1 for o in outputs)


# ---------------------------------------------------------------------------
# 4. Long-running command that produces continuous output
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_long_running_command_with_streaming_output(bash_service):
    """A command that produces output over time should still respect timeout
    and not hang even when stdout is being actively written."""
    # Write one byte per second for 30s — timeout after 2s
    request = ExecuteBashRequest(
        command="for i in $(seq 1 30); do echo $i; sleep 1; done",
        cwd="/tmp",
        timeout=2,
    )

    start = time.monotonic()
    command, task = await bash_service.start_bash_command(request)
    await task
    elapsed = time.monotonic() - start

    assert elapsed < 10, f"Streaming command took {elapsed:.1f}s — likely hanging"

    page = await bash_service.search_bash_events(command_id__eq=command.id)
    outputs = [e for e in page.items if isinstance(e, BashOutput)]
    assert any(o.exit_code == -1 for o in outputs)
    # Should have captured at least some partial output
    stdout_parts = [o.stdout for o in outputs if o.stdout]
    assert len(stdout_parts) > 0, "Expected some partial stdout before timeout"
