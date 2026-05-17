"""Stress test: lease contention — exactly one writer wins.

Bug class this catches:
    - Two services racing to start the same conversation both succeed,
      yielding a split-brain owner and silent event-log corruption.
    - Lease release happens twice or before the rightful owner finishes,
      enabling spurious takeovers.

How the lease works (ConversationLease):
    Each ConversationService has an ``owner_instance_id``. Starting an
    EventService claims the lease via a file lock + a per-conversation
    lease file. If the lease is held by another owner and not expired,
    ``claim()`` raises ConversationLeaseHeldError.
"""

import asyncio
import contextlib
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openhands.agent_server.conversation_lease import ConversationLeaseHeldError
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import Agent
from openhands.sdk.workspace import LocalWorkspace
from tests.agent_server.stress.budgets import LEASE_CONTENTION
from tests.agent_server.stress.scripts import placeholder_llm


pytestmark = [pytest.mark.stress, pytest.mark.timeout(30)]


async def _try_start(
    service: ConversationService,
    conv_id: UUID,
    *,
    workspace_dir: str,
    usage_id: str,
) -> tuple[bool, Exception | None]:
    """Attempt to start the conversation. Returns (success, exception)."""
    request = StartConversationRequest(
        conversation_id=conv_id,
        agent=Agent(llm=placeholder_llm(usage_id), tools=[]),
        workspace=LocalWorkspace(working_dir=workspace_dir),
        autotitle=False,
    )
    try:
        await service.start_conversation(request)
        return True, None
    except Exception as e:
        return False, e


async def test_concurrent_start_of_same_conversation_yields_one_winner(
    tmp_path: Path,
):
    """N services try to start the *same* conversation_id at once. Exactly
    one wins; the rest fail with ConversationLeaseHeldError (or analogous
    contention error)."""
    persist = tmp_path / "persist"
    persist.mkdir()
    workspace = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()

    n = LEASE_CONTENTION.n_concurrent
    services = [ConversationService(conversations_dir=persist) for _ in range(n)]
    # Ensure distinct owners so we exercise the cross-owner contention path.
    owner_ids = [uuid4().hex for _ in range(n)]
    for s, o in zip(services, owner_ids):
        s.owner_instance_id = o

    # Bring each service up. __aenter__ scans the persist dir; with no
    # pre-existing conversations, this is just initialization.
    started: list[ConversationService] = []
    try:
        for s in services:
            await s.__aenter__()
            started.append(s)
    except Exception:
        # If a later service fails to enter, tear down the ones already up.
        for s in reversed(started):
            with contextlib.suppress(Exception):
                await s.__aexit__(None, None, None)
        raise
    try:
        target = uuid4()
        try:
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        _try_start(
                            s, target, workspace_dir=workspace, usage_id=f"lc-{i}"
                        )
                        for i, s in enumerate(services)
                    ],
                    return_exceptions=False,
                ),
                timeout=LEASE_CONTENTION.settle_timeout_s,
            )
        except TimeoutError:
            pytest.fail(
                f"contention did not settle within "
                f"{LEASE_CONTENTION.settle_timeout_s}s; one of the {n} "
                f"services is wedged on lease acquisition."
            )

        winners = [(i, exc) for i, (ok, exc) in enumerate(results) if ok]
        losers = [(i, exc) for i, (ok, exc) in enumerate(results) if not ok]

        # 1. Exactly one winner. Catches "split brain — both services
        #    think they own the conversation" regressions.
        assert len(winners) == 1, (
            f"expected exactly 1 winner, got {len(winners)}: "
            f"{[i for i, _ in winners]}. Lease contention is broken."
        )
        assert len(losers) == n - 1, f"expected {n - 1} losers, got {len(losers)}"

        # 2. Every loser raised a recognisable lease-contention error.
        #    We accept ConversationLeaseHeldError directly, or any subclass
        #    chain that includes it (some paths wrap it).
        for i, exc in losers:
            assert exc is not None
            chain: list[BaseException | None] = [exc]
            while chain[-1] is not None and chain[-1].__cause__ is not None:
                chain.append(chain[-1].__cause__)
            kinds = {type(e) for e in chain if e is not None}
            assert any(issubclass(k, ConversationLeaseHeldError) for k in kinds), (
                f"loser service {i} raised {type(exc).__name__}: {exc}. "
                f"Expected ConversationLeaseHeldError somewhere in the "
                f"cause chain."
            )

        # 3. Persistence dir contains exactly one conversation directory
        #    for the target. If a loser partially wrote state, we'd see
        #    two — or worse, a corrupt one.
        target_dirs = list(persist.glob(f"{target.hex}*"))
        assert len(target_dirs) == 1, (
            f"expected 1 conversation directory for {target.hex}, found "
            f"{len(target_dirs)}: {[d.name for d in target_dirs]}. A loser "
            f"partially wrote state to disk."
        )
    finally:
        # Tear down all services. Order doesn't matter — losers had no
        # event_services attached. Suppress per-service exceptions so a
        # bad teardown doesn't mask the test's primary failure or skip
        # the rest of the cleanup.
        for s in services:
            with contextlib.suppress(Exception):
                await s.__aexit__(None, None, None)
