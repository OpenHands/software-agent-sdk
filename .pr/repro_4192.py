"""Repro for issue #4192: restarting agent-server quickly loses conversations.

Simulates three restart shapes against ConversationService and reports what a
freshly restarted server can see: catalog list, conversation info, and event
service availability.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from openhands.agent_server.conversation_lease import LEASE_FILE_NAME
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


def _request(workspace_dir: Path) -> StartConversationRequest:
    return StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="test-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace_dir)),
        confirmation_policy=NeverConfirm(),
    )


async def observe(service: ConversationService, cid, label: str) -> None:
    page = await service.search_conversations()
    listed = [i.id for i in page.items]
    info = await service.get_conversation(cid)
    events_service = await service.get_event_service(cid)
    print(
        f"  [{label}] listed={cid in listed} (n={len(listed)}) "
        f"info={'YES' if info else 'NO'} "
        f"event_service={'YES' if events_service else 'NO (lease held?)'}"
    )


async def scenario(name: str, mutate_lease) -> None:
    print(f"== {name} ==")
    tmp = Path(tempfile.mkdtemp())
    conversations_dir = tmp / "conversations"
    workspace = tmp / "ws"
    workspace.mkdir()

    primary = ConversationService(conversations_dir=conversations_dir)
    await primary.__aenter__()
    info, _ = await primary.start_conversation(_request(workspace))
    cid = info.id
    conversation_dir = conversations_dir / cid.hex
    lease_path = conversation_dir / LEASE_FILE_NAME

    if mutate_lease is None:
        # graceful shutdown
        await primary.__aexit__(None, None, None)
        print(f"  lease exists after graceful stop: {lease_path.exists()}")
    else:
        # hard crash: abandon primary without closing, then adjust the lease
        primary._event_services = None  # prevent accidental reuse
        mutate_lease(lease_path)

    secondary = ConversationService(conversations_dir=conversations_dir)
    await secondary.__aenter__()
    await observe(secondary, cid, "restarted server")
    await secondary.__aexit__(None, None, None)


def make_dead_pid(lease_path: Path) -> None:
    payload = json.loads(lease_path.read_text())
    payload["owner_pid"] = 2**22 + 12345  # unlikely to exist
    lease_path.write_text(json.dumps(payload))


def make_other_host(lease_path: Path) -> None:
    payload = json.loads(lease_path.read_text())
    payload["owner_host"] = "old-container-abc123"
    lease_path.write_text(json.dumps(payload))


def keep_alive_pid(lease_path: Path) -> None:
    # Same host, pid = our own parent shell (alive, not us): simulates the old
    # server process still shutting down during a quick restart.
    payload = json.loads(lease_path.read_text())
    payload["owner_pid"] = os.getppid()
    lease_path.write_text(json.dumps(payload))


async def main() -> None:
    await scenario("A: graceful stop, then restart", None)
    await scenario("B: crash (dead pid, same host), quick restart", make_dead_pid)
    await scenario("C: docker-style restart (different host in lease)", make_other_host)
    await scenario("D: quick restart while old process still alive", keep_alive_pid)


asyncio.run(main())
