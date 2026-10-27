"""Pause a real ACP subprocess through the HTTP/WebSocket conversation client."""

import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from openhands.sdk.agent.acp_agent import ACPAgent
from openhands.sdk.conversation import RemoteConversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import PauseEvent
from openhands.sdk.workspace import RemoteWorkspace
from tests.cross.test_remote_conversation_live_server import live_server_env


def wait_until(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("Condition not reached within 15 seconds")


@pytest.mark.skipif(sys.platform == "win32", reason="ACP fixture uses POSIX shell")
@pytest.mark.parametrize("interrupt_after_completion", [False, True])
def test_acp_pause_waits_for_actual_turn_completion(
    tmp_path, monkeypatch, interrupt_after_completion
):
    control = tmp_path / "control"
    control.mkdir()
    fixture = Path(__file__).parents[1] / "sdk/agent/fake_acp_pause_server.py"

    if interrupt_after_completion:
        original_flush = ACPAgent._flush_file_credentials

        async def flush_after_gate(self):
            # Hold cleanup after the real ACP response has been finalized.
            (control / "cleanup-entered").touch()
            while not (control / "release-cleanup").exists():
                await asyncio.sleep(0.01)
            await original_flush(self)

        monkeypatch.setattr(ACPAgent, "_flush_file_credentials", flush_after_gate)

    def steps():
        path = control / "steps.txt"
        return path.read_text().splitlines() if path.exists() else []

    def allow(turn, step):
        (control / f"allow-{turn}-{step}").touch()

    with live_server_env(
        tmp_path, monkeypatch, config_overrides={"preload_tools": False}
    ) as env:
        conversation = RemoteConversation(
            agent=ACPAgent(acp_command=[sys.executable, str(fixture), str(control)]),
            workspace=RemoteWorkspace(
                host=env["host"], working_dir=str(env["workspace_path"])
            ),
            visualizer=None,
        )
        try:
            conversation.send_message("first")
            conversation.send_message("second")
            allow(1, 1)
            with ThreadPoolExecutor(max_workers=1) as executor:
                run = executor.submit(conversation.run, poll_interval=0.05, timeout=20)
                try:
                    wait_until(lambda: steps() == ["1-1"])
                    conversation.pause()
                    conversation.pause()
                    # Existing clients keep waiting on RUNNING during the
                    # migration window; PAUSING remains the internal state.
                    assert conversation.state.execution_status.value == "running"
                    assert not any(
                        isinstance(e, PauseEvent) for e in conversation.state.events
                    )
                    assert not run.done()
                    with httpx.Client(base_url=env["host"]) as client:
                        assert (
                            client.post(
                                f"/api/conversations/{conversation.id}/run"
                            ).status_code
                            == 409
                        )
                    allow(1, 2)
                    allow(1, 3)
                    wait_until(lambda: steps() == ["1-1", "1-2", "1-3"])
                    assert conversation.state.execution_status.value == "running"
                    assert not run.done()
                    assert not (control / "cancels.txt").exists()
                    (control / "finish-1").touch()
                    if interrupt_after_completion:
                        wait_until(lambda: (control / "cleanup-entered").exists())
                        conversation.interrupt()
                    run.result(timeout=10)
                finally:
                    if not run.done():
                        conversation.interrupt()
                        run.result(timeout=10)

            assert (
                conversation.state.execution_status
                == ConversationExecutionStatus.PAUSED
            )
            assert sum(
                isinstance(e, PauseEvent) for e in conversation.state.events
            ) == (0 if interrupt_after_completion else 1)
            (control / "release-cleanup").touch()
            for step in range(1, 4):
                allow(2, step)
            (control / "finish-2").touch()
            conversation.run(poll_interval=0.05, timeout=15)
            assert steps() == ["1-1", "1-2", "1-3", "2-1", "2-2", "2-3"]
            prompts = [
                json.loads(line)
                for line in (control / "prompts.jsonl").read_text().splitlines()
            ]
            assert len(prompts) == 2
            assert prompts[0][0]["text"].startswith("first")
            assert prompts[1][0]["text"].startswith("second")
            assert (
                conversation.state.execution_status
                == ConversationExecutionStatus.FINISHED
            )
        finally:
            conversation.interrupt()
            conversation.close()
