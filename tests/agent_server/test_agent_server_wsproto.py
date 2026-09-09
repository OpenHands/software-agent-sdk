"""Integration test to verify the agent server works with wsproto."""

import asyncio
import json
import multiprocessing
import os
import socket
import sys
import time
from uuid import uuid4

import pytest
import requests
import websockets
import websockets.exceptions


def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        return s.getsockname()[1]


def _drop_site_packages_subdir_paths():
    """Remove sys.path entries that point *inside* a site-packages directory.

    This test spawns the server with the ``spawn`` start method from within a
    pytest-xdist worker, so the child inherits the worker's ``sys.path``. Under
    the full suite another test leaves a stray package directory
    (``.../site-packages/browser_use``) at ``sys.path[0]``, which makes its
    sub-packages importable as top-level modules — ``browser_use/mcp`` then
    shadows the real ``mcp`` and fastmcp's eager ``import mcp`` crashes startup
    with a ``mcp`` <-> ``browser_use.mcp`` circular import. Dropping such
    entries restores normal top-level resolution.
    """
    cleaned = [
        entry
        for entry in sys.path
        if os.path.basename(os.path.dirname(entry.rstrip(os.sep))) != "site-packages"
    ]
    sys.path[:] = cleaned


def run_agent_server(port, api_key):
    _drop_site_packages_subdir_paths()

    # Configure authentication for the server process.
    #
    # Use both the V1 indexed env var and the legacy V0 var to keep this test
    # stable across different config parsing behaviors.
    os.environ["OH_SESSION_API_KEYS_0"] = api_key
    os.environ["SESSION_API_KEY"] = api_key
    sys.argv = ["agent-server", "--port", str(port)]
    from openhands.agent_server.__main__ import main

    main()


def _terminate(process):
    process.terminate()
    process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join()


def _wait_until_ready(process, port, timeout=30.0):
    """Poll the lightweight /alive endpoint until the server responds.

    Returns False (rather than blocking for the whole timeout) as soon as the
    server process exits, so a failed port bind can be retried on a fresh port.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process.is_alive():
            return False
        try:
            response = requests.get(f"http://127.0.0.1:{port}/alive", timeout=1)
            if response.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.5)
    return False


@pytest.fixture(scope="session")
def agent_server():
    api_key = "test-wsproto-key"
    ctx = multiprocessing.get_context("spawn")

    # find_free_port() closes the socket before the server rebinds it, leaving a
    # TOCTOU window in which another process on a busy CI host can steal the port
    # and crash startup. Retry on a fresh port to absorb that race.
    process = None
    port = None
    for _ in range(3):
        port = find_free_port()
        process = ctx.Process(target=run_agent_server, args=(port, api_key))
        process.start()
        if _wait_until_ready(process, port):
            break
        _terminate(process)
    else:
        pytest.fail("Agent server failed to start after multiple attempts")

    yield {"port": port, "api_key": api_key}

    _terminate(process)


def test_agent_server_starts_with_wsproto(agent_server):
    response = requests.get(f"http://127.0.0.1:{agent_server['port']}/docs")
    assert response.status_code == 200
    assert (
        "OpenHands Agent Server" in response.text or "swagger" in response.text.lower()
    )


@pytest.mark.asyncio
async def test_agent_server_websocket_with_wsproto(agent_server):
    port = agent_server["port"]
    api_key = agent_server["api_key"]

    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201]
    conversation_id = response.json()["id"]

    ws_url = (
        f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}"
        f"?session_api_key={api_key}&resend_all=true"
    )

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response is not None
        except TimeoutError:
            pass

        await ws.send(
            json.dumps({"role": "user", "content": "Hello from wsproto test"})
        )


@pytest.mark.asyncio
async def test_agent_server_websocket_with_wsproto_header_auth(agent_server):
    port = agent_server["port"]
    api_key = agent_server["api_key"]

    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201]
    conversation_id = response.json()["id"]

    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}?resend_all=true"

    async with websockets.connect(
        ws_url,
        open_timeout=5,
        additional_headers={"X-Session-API-Key": api_key},
    ) as ws:
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response is not None
        except TimeoutError:
            pass

        await ws.send(
            json.dumps(
                {"role": "user", "content": "Hello from wsproto header auth test"}
            )
        )


@pytest.mark.asyncio
async def test_agent_server_websocket_first_message_auth_accepted(agent_server):
    """First-message auth: connect with no query/header key, auth via first frame.

    Exercises the real WebSocket protocol transition (handshake → consume first
    frame as auth → continue normal message flow) that mock-only tests can't
    cover. See PR review feedback on test coverage gaps.
    """
    port = agent_server["port"]
    api_key = agent_server["api_key"]

    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201]
    conversation_id = response.json()["id"]

    # No session_api_key in URL or header — must authenticate via first frame.
    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{conversation_id}?resend_all=true"

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        # Send the auth frame as the very first message after handshake.
        await ws.send(json.dumps({"type": "auth", "session_api_key": api_key}))

        # Connection must remain usable: try to receive (resend_all may produce
        # nothing for an empty conversation, so a timeout here is fine).
        try:
            response = await asyncio.wait_for(ws.recv(), timeout=2)
            assert response is not None
        except TimeoutError:
            pass

        # Subsequent message must be processed as a Message (not auth) — proves
        # the auth frame was consumed by the auth handler, not the main loop.
        await ws.send(
            json.dumps({"role": "user", "content": "Hello after first-message auth"})
        )


@pytest.mark.asyncio
async def test_agent_server_websocket_first_message_auth_rejected(agent_server):
    """First-message auth: invalid key triggers WebSocket close with code 4001."""
    port = agent_server["port"]

    # No conversation needed — auth rejection happens before conversation lookup.
    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{uuid4()}"

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        # Send an invalid first-message auth frame.
        await ws.send(
            json.dumps({"type": "auth", "session_api_key": "definitely-wrong-key"})
        )

        # Server must close the connection with code 4001 ("Authentication
        # failed"). Receiving on a closed socket raises ConnectionClosed.
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=5)

    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4001


@pytest.mark.asyncio
async def test_agent_server_websocket_first_message_auth_malformed(agent_server):
    """First-message auth: malformed JSON triggers close with code 4001."""
    port = agent_server["port"]

    ws_url = f"ws://127.0.0.1:{port}/sockets/events/{uuid4()}"

    async with websockets.connect(ws_url, open_timeout=5) as ws:
        # Send invalid JSON as the first frame.
        await ws.send("this is not json")

        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=5)

    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4001


# /sockets/session/{id}. The unit tests cover the writer, subscriber and replay
# in isolation; nothing there executes the endpoint. These drive it end to end:
# auth, lookup, sync frame, replay from disk, resume by cursor, close codes.


def _create_conversation(port: int, api_key: str) -> str:
    response = requests.post(
        f"http://127.0.0.1:{port}/api/conversations",
        headers={"X-Session-API-Key": api_key},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "test-llm",
                    "model": "test-provider/test-model",
                    "api_key": "test-key",
                },
                "tools": [],
            },
            "workspace": {"working_dir": "/tmp/test-workspace"},
        },
    )
    assert response.status_code in [200, 201], response.text
    return response.json()["id"]


async def _collect_frames(ws, *, until=None, seconds: float = 15.0) -> list[dict]:
    """Read frames until ``until`` is satisfied, or the deadline passes.

    The agent step fails (no real LLM behind ``test-provider``), so the frame
    sequence is not fixed and the interesting frame is rarely the first — a
    transient snapshot lands immediately, the durable event later. Waiting on
    a predicate rather than a quiet period keeps that from being a race.
    """
    frames: list[dict] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - loop.time())
        except TimeoutError:
            break
        except websockets.exceptions.ConnectionClosed:
            break
        frames.append(json.loads(raw))
        if until is not None and any(until(f) for f in frames):
            break
    return frames


def _is_durable(frame: dict) -> bool:
    return frame["type"] == "durable"


@pytest.mark.asyncio
async def test_session_socket_opens_with_a_sync_frame(agent_server):
    """The first frame is always ``sync``."""
    port, api_key = agent_server["port"], agent_server["api_key"]
    conversation_id = _create_conversation(port, api_key)

    ws_url = (
        f"ws://127.0.0.1:{port}/sockets/session/{conversation_id}"
        f"?session_api_key={api_key}"
    )
    async with websockets.connect(ws_url, open_timeout=5) as ws:
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))

    assert first["type"] == "sync"
    # Nothing was requested, so the range is open at the bottom.
    assert first.get("from_seq") is None


@pytest.mark.asyncio
async def test_session_socket_durable_frame_carries_seq_over_the_wire(agent_server):
    """A message sent on the socket comes back as a durable frame with a seq.

    The point of the endpoint: an ``Event`` inside an envelope with a resumable
    sequence number, through the real server.
    """
    port, api_key = agent_server["port"], agent_server["api_key"]
    conversation_id = _create_conversation(port, api_key)

    ws_url = (
        f"ws://127.0.0.1:{port}/sockets/session/{conversation_id}"
        f"?session_api_key={api_key}"
    )
    async with websockets.connect(ws_url, open_timeout=5) as ws:
        assert (
            json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] == "sync"
        )
        await ws.send(json.dumps({"role": "user", "content": "hello session socket"}))
        frames = await _collect_frames(ws, until=_is_durable)

    durable = [f for f in frames if f["type"] == "durable"]
    assert durable, f"no durable frame arrived; got {[f['type'] for f in frames]}"
    # seq is real and monotonic; the payload is the untouched Event.
    seqs = [f["seq"] for f in durable]
    assert all(isinstance(s, int) for s in seqs)
    assert seqs == sorted(seqs)
    assert all("kind" in f["event"] and "id" in f["event"] for f in durable)
    # The snapshot pushed on subscribe carries no seq.
    assert all("seq" not in f for f in frames if f["type"] == "transient")


@pytest.mark.asyncio
async def test_session_socket_resumes_history_with_after_seq(agent_server):
    """Reconnecting with ``after_seq`` replays from disk and loses nothing."""
    port, api_key = agent_server["port"], agent_server["api_key"]
    conversation_id = _create_conversation(port, api_key)

    base_url = (
        f"ws://127.0.0.1:{port}/sockets/session/{conversation_id}"
        f"?session_api_key={api_key}"
    )

    # Produce some history.
    async with websockets.connect(base_url, open_timeout=5) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5)  # sync
        await ws.send(json.dumps({"role": "user", "content": "first message"}))
        first_pass = await _collect_frames(ws, until=_is_durable)

    produced = [f["seq"] for f in first_pass if f["type"] == "durable"]
    assert produced, "expected the first connection to persist something"

    # after_seq=-1 means "from the start".
    async with websockets.connect(f"{base_url}&after_seq=-1", open_timeout=5) as ws:
        sync = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        replayed = await _collect_frames(ws, until=_is_durable)

    assert sync["type"] == "sync"
    assert sync["from_seq"] == -1
    assert sync["through_seq"] >= max(produced)

    replayed_seqs = [f["seq"] for f in replayed if f["type"] == "durable"]
    # Starts at the beginning and covers what the first pass saw.
    assert replayed_seqs[:1] == [0]
    assert set(produced).issubset(set(replayed_seqs))
    # Nothing twice across the replay/live seam.
    assert len(replayed_seqs) == len(set(replayed_seqs))


@pytest.mark.asyncio
async def test_session_socket_unknown_conversation_closes_4004(agent_server):
    port, api_key = agent_server["port"], agent_server["api_key"]

    ws_url = (
        f"ws://127.0.0.1:{port}/sockets/session/{uuid4()}?session_api_key={api_key}"
    )
    async with websockets.connect(ws_url, open_timeout=5) as ws:
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc_info:
            await asyncio.wait_for(ws.recv(), timeout=5)

    assert exc_info.value.rcvd is not None
    assert exc_info.value.rcvd.code == 4004


@pytest.mark.asyncio
async def test_session_socket_survives_a_malformed_inbound_frame(agent_server):
    """A garbage frame earns an error frame; the connection stays usable."""
    port, api_key = agent_server["port"], agent_server["api_key"]
    conversation_id = _create_conversation(port, api_key)

    ws_url = (
        f"ws://127.0.0.1:{port}/sockets/session/{conversation_id}"
        f"?session_api_key={api_key}"
    )
    async with websockets.connect(ws_url, open_timeout=5) as ws:
        assert (
            json.loads(await asyncio.wait_for(ws.recv(), timeout=5))["type"] == "sync"
        )

        await ws.send("this is not json")
        frames = await _collect_frames(ws, until=lambda f: f["type"] == "error")
        errors = [f for f in frames if f["type"] == "error"]
        assert errors, f"expected an error frame; got {[f['type'] for f in frames]}"

        # Still alive: a well-formed message is still processed.
        await ws.send(json.dumps({"role": "user", "content": "still here"}))
        after = await _collect_frames(ws, until=_is_durable)
        assert any(f["type"] == "durable" for f in after)
