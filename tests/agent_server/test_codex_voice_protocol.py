import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from openhands.agent_server.codex_voice import CodexRelay, CodexVoiceManager, RelayError
from openhands.agent_server.event_service import EventService


_SERVER = r"""
import json, sys
options = json.loads(sys.argv[1])
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if "id" not in message:
        continue
    if method == "fixture/wait":
        continue
    if method == "fixture/eof":
        break
    result = {}
    if method == "initialize":
        result = {"userAgent": "openhands_insider_voice/0.154.0 fixture"}
    elif method == "config/read":
        result = {"config": {"mcp_servers": {}}}
    elif method == "account/read":
        result = {"account": {"type": "chatgpt"}}
    elif method == "thread/start":
        result = {"thread": {"id": "fixture-thread"}}
    if method == options.get("invalid_method"):
        result = options["invalid_result"]
    print(json.dumps({"id": message["id"], "result": result}), flush=True)
    if method == "thread/realtime/start":
        failure = options.get("failure", "eof")
        if failure == "invalid_json":
            print("not-json", flush=True)
        elif failure == "invalid_shape":
            print("[]", flush=True)
        elif failure == "invalid_notification":
            print(json.dumps({"method": "thread/realtime/sdp", "params": None}),
                  flush=True)
        break
"""


@pytest.fixture
def failing_protocol(tmp_path, monkeypatch):
    script = tmp_path / "failing_app_server.py"
    script.write_text(_SERVER)
    options = {}
    monkeypatch.setattr(
        "openhands.agent_server.codex_voice._command",
        lambda home: ([sys.executable, str(script), json.dumps(options)], {}),
    )
    return options


@pytest.mark.parametrize(
    "failure", ["eof", "invalid_json", "invalid_shape", "invalid_notification"]
)
async def test_bad_output_releases_pending_sdp(failing_protocol, tmp_path, failure):
    failing_protocol["failure"] = failure
    events = AsyncMock(spec=EventService)
    relay = CodexRelay(uuid4(), events, tmp_path / "home")
    try:
        async with asyncio.timeout(5):
            with pytest.raises(RelayError, match="connection ended"):
                await relay.start("v=fixture-offer", "fixture context")
    finally:
        await relay.close()
    assert relay.status.status == "error"
    assert relay.process is not None and relay.process.returncode is not None
    assert not Path(relay.workspace.name).exists()
    events.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("method", "result"),
    [("initialize", []), ("config/read", None), ("account/read", "invalid")],
)
async def test_malformed_result_fails_preflight_closed(
    failing_protocol, tmp_path, method, result
):
    failing_protocol.update(invalid_method=method, invalid_result=result)
    events = AsyncMock(spec=EventService)
    manager = CodexVoiceManager(tmp_path / "home")
    assert await manager.availability(uuid4(), events) == "codex_unavailable"
    events.send_message.assert_not_awaited()


@pytest.mark.parametrize("ending", ["eof", "close"])
async def test_connection_end_releases_outstanding_rpc(
    failing_protocol, tmp_path, ending
):
    relay = CodexRelay(uuid4(), AsyncMock(spec=EventService), tmp_path / "home")
    try:
        await relay.initialize()
        pending = asyncio.create_task(relay.rpc("fixture/wait", {}))
        await relay.rpc("fixture/barrier", {})
        if ending == "eof":
            with pytest.raises(RelayError, match="connection ended"):
                await relay.rpc("fixture/eof", {})
        else:
            await relay.close()
        async with asyncio.timeout(5):
            with pytest.raises(RelayError, match="connection ended"):
                await pending
    finally:
        await relay.close()
    assert relay.process is not None and relay.process.returncode is not None
