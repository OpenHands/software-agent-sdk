"""Engel's exact question (issue #4032 context):

  User makes an LLM profile with timeout=600, saves it, makes it ACTIVE,
  starts a NEW conversation with that active profile, and NEVER switches.
  After an agent-server restart: is the timeout 600 or 300, and why?

This is an independent check (not the PR's own harness). It:

  1. saves an LLM profile ``slow`` with timeout=600,
  2. references it from an agent profile (the "active profile"),
  3. starts a NEW conversation via ``agent_profile_id`` — the agent-server
     equivalent of "start a conversation with the active profile",
  4. reads the LIVE agent timeout at creation,
  5. dumps the RAW meta.json / base_state.json bytes (grepping "timeout"),
  6. tears the service down and builds a fresh one over the same dir
     (= an agent-server restart), touches the conversation, reads the LIVE
     timeout again,
  7. prints a verdict.

No network / API key: the agent loop never runs, so the LLM is never called.
Run:  uv run python .pr/engel_active_profile_question.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


PROFILE_TIMEOUT = 600
SDK_DEFAULT_TIMEOUT = 300  # openhands-sdk .../llm/llm.py: timeout Field(default=300)


def grep_timeout(path: Path) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []

    def walk(node, trail="$"):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "timeout":
                    found.append((f"{trail}.{k}", v))
                else:
                    walk(v, f"{trail}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{trail}[{i}]")

    walk(json.loads(path.read_text()))
    return found


def raw_timeout_tokens(path: Path) -> list[str]:
    """Prove it from the literal bytes, not just parsed structure."""
    text = path.read_text()
    return re.findall(r'"timeout"\s*:\s*[0-9]+', text)


def make_llm(timeout: int) -> LLM:
    return LLM(model="gpt-4o", usage_id="agent", timeout=timeout, api_key="sk-x")


def seed_active_profile(persistence: Path, timeout: int) -> UUID:
    from openhands.agent_server.persistence import (
        get_agent_profile_store,
        get_llm_profile_store,
        reset_stores,
    )
    from openhands.sdk.profiles.agent_profile import OpenHandsAgentProfile

    reset_stores()
    os.environ["OH_PERSISTENCE_DIR"] = str(persistence)
    # 1) LLM profile with the user's chosen timeout, saved.
    get_llm_profile_store().save("slow", make_llm(timeout), include_secrets=True)
    # 2) An agent profile that references it — this is the "active profile".
    profile = OpenHandsAgentProfile(
        id=uuid4(), name="slow-agent", revision=1, llm_profile_ref="slow", tools=[]
    )
    get_agent_profile_store().save(profile)
    return profile.id


async def live_timeout(service: ConversationService, cid: UUID) -> int | None:
    event_service = await service.get_event_service(cid)
    assert event_service is not None
    return event_service.get_conversation().agent.llm.timeout


async def amain() -> int:
    from openhands.agent_server.persistence import reset_stores

    previous = os.environ.get("OH_PERSISTENCE_DIR")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            persistence = root / "persistence"
            persistence.mkdir(parents=True)
            conversations = root / "conversations"
            workspace = root / "workspace"
            workspace.mkdir()

            profile_id = seed_active_profile(persistence, PROFILE_TIMEOUT)
            print(f"Saved LLM profile 'slow' timeout={PROFILE_TIMEOUT}, made active.")
            print(
                f"SDK default timeout if nothing carried it = {SDK_DEFAULT_TIMEOUT}\n"
            )

            request = StartConversationRequest(
                agent_profile_id=profile_id,
                workspace=LocalWorkspace(working_dir=str(workspace)),
                confirmation_policy=NeverConfirm(),
            )

            # ---- start a NEW conversation with the active profile, no switch ----
            async with ConversationService(conversations_dir=conversations) as svc:
                info, _ = await svc.start_conversation(request)
                cid = info.id
                created = await live_timeout(svc, cid)
                print(f"[create] live agent llm.timeout = {created}")

            conv_dir = conversations / cid.hex
            print("\n[on disk after shutdown]  (parsed paths + raw byte tokens)")
            for name in ("meta.json", "base_state.json"):
                p = conv_dir / name
                print(f"  {name}")
                print(f"    parsed: {grep_timeout(p)}")
                print(f"    raw   : {raw_timeout_tokens(p)}")

            # ---- restart the agent-server (fresh service, same dir) ----
            async with ConversationService(conversations_dir=conversations) as svc2:
                restarted = await live_timeout(svc2, cid)  # touch => hydrate
                print(f"\n[after restart] live agent llm.timeout = {restarted}")

            print("\n=== VERDICT ===")
            verdict = "600" if restarted == PROFILE_TIMEOUT else str(restarted)
            print(f"  It is {verdict}.")
            print(
                "  Why: the active profile's LLM (timeout=600) is copied into the "
                "conversation's\n  agent at CREATION and persisted to BOTH files. "
                "With no switch, the two files\n  never diverge, so the restart "
                "rebuild from meta.json reinstates the SAME 600.\n  The 300 default "
                "only appears if some path builds an LLM WITHOUT carrying the\n  "
                "profile's value — which is what a switch that isn't mirrored into "
                "meta.json does."
            )
            return 0 if restarted == PROFILE_TIMEOUT else 1
    finally:
        reset_stores()
        if previous is None:
            os.environ.pop("OH_PERSISTENCE_DIR", None)
        else:
            os.environ["OH_PERSISTENCE_DIR"] = previous


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
