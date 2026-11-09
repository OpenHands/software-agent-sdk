"""Real REST/restart/WebSocket recovery with no agent or model execution."""

import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from uuid import UUID

import pytest
import requests
import websockets

from tests.agent_server.test_agent_server_wsproto import (
    _terminate,
    _wait_until_ready,
    find_free_port,
    run_agent_server,
)


def serve_recovery_store(root: str, port: int, api_key: str) -> None:
    os.environ["OH_PERSISTENCE_DIR"] = f"{root}/settings"
    os.environ["OH_CONVERSATIONS_PATH"] = f"{root}/conversations"
    os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    run_agent_server(port, api_key)


@pytest.fixture
def native_server(tmp_path: Path):
    context = multiprocessing.get_context("spawn")
    processes = []
    api_key = "offline-sequence-recovery"

    def start():
        port = find_free_port()
        process = context.Process(
            target=serve_recovery_store, args=(str(tmp_path), port, api_key)
        )
        process.start()
        processes.append(process)
        assert _wait_until_ready(process, port), "Local agent server failed to start"
        return {
            "process": process,
            "port": port,
            "key": api_key,
            "base": f"http://127.0.0.1:{port}",
            "root": tmp_path,
        }

    try:
        yield start
    finally:
        for process in processes:
            if process.is_alive():
                _terminate(process)
            else:
                process.join(timeout=5)


def create_conversation(server) -> str:
    workspace = server["root"] / "workspace"
    workspace.mkdir(exist_ok=True)
    response = requests.post(
        f"{server['base']}/api/conversations",
        headers={"X-Session-API-Key": server["key"]},
        json={
            "agent": {
                "kind": "Agent",
                "llm": {
                    "usage_id": "offline-sequence",
                    "model": "test-provider/test-model",
                    "api_key": "offline",
                },
                "tools": [],
            },
            "workspace": {"working_dir": str(workspace)},
        },
        timeout=15,
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def send_without_running(server, conversation_id: str, text: str) -> None:
    response = requests.post(
        f"{server['base']}/api/conversations/{conversation_id}/events",
        headers={"X-Session-API-Key": server["key"]},
        json={
            "role": "user",
            "content": [{"type": "text", "text": text}],
            "run": False,
        },
        timeout=15,
    )
    assert response.status_code == 200, response.text


def stored_events(server, conversation_id: str) -> dict[str, tuple[int, Path]]:
    folder = server["root"] / "conversations" / UUID(conversation_id).hex / "events"
    return {
        json.loads(path.read_text())["id"]: (int(path.name.split("-")[1]), path)
        for path in folder.glob("event-*.json")
    }


async def receive_replay(server, conversation_id: str, after: int, expected_count: int):
    address = (
        f"ws://127.0.0.1:{server['port']}/sockets/session/{conversation_id}"
        f"?session_api_key={server['key']}&after_seq={after}"
    )
    frames = []
    async with websockets.connect(address, open_timeout=5) as socket:
        sync = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
        assert sync["type"] == "sync"
        deadline = asyncio.get_running_loop().time() + 3
        while len(frames) < expected_count:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                frame = json.loads(
                    await asyncio.wait_for(socket.recv(), timeout=remaining)
                )
            except TimeoutError:
                break
            if frame["type"] == "durable":
                frames.append(frame)
    return sync, frames


@pytest.mark.asyncio
@pytest.mark.parametrize("lost", ["tail", "middle_and_tail", "all"])
async def test_restart_reconnect_never_skips_new_events_after_file_loss(
    native_server, lost
):
    first = native_server()
    conversation_id = create_conversation(first)
    for text in ("before one", "before two", "before three"):
        send_without_running(first, conversation_id, text)
    original = stored_events(first, conversation_id)
    assert len(original) >= 3
    sync, frames = await receive_replay(first, conversation_id, -1, len(original))
    assert {frame["event"]["id"] for frame in frames} == set(original)
    cursor = sync["through_seq"]
    assert cursor == max(value[0] for value in original.values())
    _terminate(first["process"])

    ordered = sorted(original.values(), key=lambda value: value[0])
    if lost == "all":
        deleted = ordered
    elif lost == "middle_and_tail":
        deleted = [ordered[1], ordered[-1]]
    else:
        deleted = ordered[-1:]
    for _, path in deleted:
        path.unlink()
    survivors = stored_events(first, conversation_id)

    restarted = native_server()
    send_without_running(restarted, conversation_id, "after restart")
    current = stored_events(restarted, conversation_id)
    new_ids = set(current) - set(survivors)
    assert new_ids, "Sending a message must persist events"
    replay_sync, replay = await receive_replay(
        restarted, conversation_id, cursor, len(new_ids)
    )
    assert {frame["event"]["id"] for frame in replay} == new_ids
    assert all(frame["seq"] > cursor for frame in replay)
    assert len({frame["seq"] for frame in replay}) == len(replay)
    assert replay_sync["through_seq"] == max(value[0] for value in current.values())
    for identifier, (seq, _) in survivors.items():
        assert current[identifier][0] == seq


@pytest.mark.asyncio
async def test_second_reconnect_uses_the_new_high_water_after_another_restart(
    native_server,
):
    first = native_server()
    conversation_id = create_conversation(first)
    send_without_running(first, conversation_id, "initial")
    original = stored_events(first, conversation_id)
    cursor = max(value[0] for value in original.values())
    _terminate(first["process"])
    for _, path in original.values():
        path.unlink()
    second = native_server()
    send_without_running(second, conversation_id, "first recovery")
    current = stored_events(second, conversation_id)
    sync, replay = await receive_replay(second, conversation_id, cursor, len(current))
    assert len(replay) == len(current)
    recovered_cursor = sync["through_seq"]
    assert recovered_cursor > cursor
    _terminate(second["process"])
    for _, path in current.values():
        path.unlink()
    third = native_server()
    send_without_running(third, conversation_id, "second recovery")
    latest = stored_events(third, conversation_id)
    _, replay = await receive_replay(
        third, conversation_id, recovered_cursor, len(latest)
    )
    assert {frame["event"]["id"] for frame in replay} == set(latest)
    assert min(frame["seq"] for frame in replay) > recovered_cursor
