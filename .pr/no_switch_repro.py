"""Issue #4032 — does the timeout revert when *no* switch is involved?

The report was read as: "make a profile with timeout=600, talk to the agent,
restore the conversation later, and the timeout has reverted" — with no LLM or
profile switch anywhere. This script tries to reproduce exactly that, three
ways, and prints the on-disk state at every step so the result is checkable
rather than asserted.

Run it yourself::

    uv run python .pr/no_switch_repro.py

No API key and no network are needed: the agent loop is never started, so the
LLM is never called. Each scenario uses a real ``ConversationService`` against
a real on-disk persistence dir, and "restart" means tearing the service down
and constructing a fresh one over the same directory — the genuine
resume-from-``meta.json`` path.

Scenarios
---------
A. plain agent created with ``timeout=600`` (what OpenHands sends after
   ``POST /profiles/{name}/activate`` copies the profile's LLM into
   ``agent_settings.llm``).
B. conversation started from an ``agent_profile_id`` whose referenced LLM
   profile has ``timeout=600`` — the agent-canvas path.
C. same as B, but the LLM profile is *edited* to 1234 after the conversation
   already exists. This is the literal wording of the bug report's repro
   steps, so it is worth showing what it actually does.

For the scenario that *does* revert (an LLM switch that is not mirrored into
``meta.json``), see ``timeout_restart_evidence.py``.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


PROFILE_TIMEOUT = 600
EDITED_TIMEOUT = 1234


def timeouts_in(path: Path) -> list[tuple[str, Any]]:
    """Every ``timeout`` value appearing anywhere in a JSON file, with paths."""
    found: list[tuple[str, Any]] = []

    def walk(node: Any, trail: str = "$") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "timeout":
                    found.append((f"{trail}.{key}", value))
                else:
                    walk(value, f"{trail}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{trail}[{index}]")

    walk(json.loads(path.read_text()))
    return found


def dump_disk(conv_dir: Path) -> None:
    for name in ("meta.json", "base_state.json"):
        path = conv_dir / name
        found = timeouts_in(path) if path.exists() else "<missing>"
        print(f"      {name}: {found}")


def make_llm(timeout: int, usage_id: str = "agent") -> LLM:
    return LLM(model="gpt-4o", usage_id=usage_id, timeout=timeout, api_key="sk-x")


async def live_timeout(service: ConversationService, cid: UUID) -> int | None:
    event_service = await service.get_event_service(cid)
    assert event_service is not None
    return event_service.get_conversation().agent.llm.timeout


async def scenario_a(root: Path) -> bool:
    print("\n=== A. plain agent created with timeout=600, no switch ===")
    root.mkdir(parents=True, exist_ok=True)
    conversations = root / "conversations"
    workspace = root / "workspace"
    workspace.mkdir()

    request = StartConversationRequest(
        agent=Agent(llm=make_llm(PROFILE_TIMEOUT), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations) as primary:
        info, _ = await primary.start_conversation(request)
        cid = info.id
        before = await live_timeout(primary, cid)
        print(f"    at creation: timeout={before}")

    print("    on disk after shutdown:")
    dump_disk(conversations / cid.hex)

    async with ConversationService(conversations_dir=conversations) as restarted:
        after = await live_timeout(restarted, cid)
        print(f"    after restart: timeout={after}")

    ok = before == PROFILE_TIMEOUT and after == PROFILE_TIMEOUT
    print(f"    => preserved: {ok}")
    return ok


def seed_profiles(persistence: Path, timeout: int) -> UUID:
    """Save an LLM profile plus an agent profile referencing it."""
    from openhands.agent_server.persistence import (
        get_agent_profile_store,
        get_llm_profile_store,
        reset_stores,
    )
    from openhands.sdk.profiles.agent_profile import OpenHandsAgentProfile

    reset_stores()
    os.environ["OH_PERSISTENCE_DIR"] = str(persistence)

    get_llm_profile_store().save("slow", make_llm(timeout), include_secrets=True)
    profile = OpenHandsAgentProfile(
        id=uuid4(),
        name="slow-agent",
        revision=1,
        llm_profile_ref="slow",
        tools=[],
    )
    get_agent_profile_store().save(profile)
    return profile.id


def edit_llm_profile(timeout: int) -> None:
    from openhands.agent_server.persistence import get_llm_profile_store

    get_llm_profile_store().save("slow", make_llm(timeout), include_secrets=True)


async def scenario_b_and_c(root: Path) -> tuple[bool, bool]:
    print("\n=== B. conversation started from agent_profile_id (canvas path) ===")
    root.mkdir(parents=True, exist_ok=True)
    persistence = root / "persistence"
    persistence.mkdir()
    profile_id = seed_profiles(persistence, PROFILE_TIMEOUT)

    conversations = root / "conversations"
    workspace = root / "workspace"
    workspace.mkdir()

    request = StartConversationRequest(
        agent_profile_id=profile_id,
        workspace=LocalWorkspace(working_dir=str(workspace)),
        confirmation_policy=NeverConfirm(),
    )

    async with ConversationService(conversations_dir=conversations) as primary:
        info, _ = await primary.start_conversation(request)
        cid = info.id
        before = await live_timeout(primary, cid)
        print(f"    at creation: timeout={before}")

        # --- scenario C starts here: edit the profile mid-conversation ---
        edit_llm_profile(EDITED_TIMEOUT)
        after_edit = await live_timeout(primary, cid)
        print(
            f"    after editing the profile to {EDITED_TIMEOUT}: timeout={after_edit}"
        )

    print("    on disk after shutdown:")
    dump_disk(conversations / cid.hex)

    async with ConversationService(conversations_dir=conversations) as restarted:
        after = await live_timeout(restarted, cid)
        print(f"    after restart: timeout={after}")

    b_ok = before == PROFILE_TIMEOUT and after == PROFILE_TIMEOUT
    print(f"    => B preserved across restart: {b_ok}")

    print("\n=== C. profile edited after creation (the report's literal steps) ===")
    c_unchanged = after_edit == PROFILE_TIMEOUT and after == PROFILE_TIMEOUT
    print(
        f"    live value never moved to {EDITED_TIMEOUT}: "
        f"before restart={after_edit}, after restart={after}"
    )
    print(
        "    => the restart changes nothing here; an edited-but-never-switched "
        f"profile is not picked up either way: {c_unchanged}"
    )
    return b_ok, c_unchanged


async def amain() -> int:
    from openhands.agent_server.persistence import reset_stores

    previous = os.environ.get("OH_PERSISTENCE_DIR")
    results: list[bool] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            results.append(await scenario_a(Path(tmp) / "a"))
        with tempfile.TemporaryDirectory() as tmp:
            b_ok, c_ok = await scenario_b_and_c(Path(tmp) / "bc")
            results.extend([b_ok, c_ok])
    finally:
        reset_stores()
        if previous is None:
            os.environ.pop("OH_PERSISTENCE_DIR", None)
        else:
            os.environ["OH_PERSISTENCE_DIR"] = previous

    print("\n=== SUMMARY ===")
    print(
        "    Without a switch, the creation-time timeout survives the restart "
        "in every path tested."
    )
    print(f"    all scenarios behaved as described: {all(results)}")
    return 0 if all(results) else 1


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
