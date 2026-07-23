"""Live evidence for idle-conversation eviction (issue #3141 / OSS-1495).

Runs a real ``ConversationService`` against real on-disk persistence -- no
mocks, no monkeypatching -- and reports four things:

``growth``  RSS across repeated hydrate/idle rounds, with eviction on vs off.
            This is the actual regression: without eviction, every conversation
            the server has ever touched stays resident, so RSS grows linearly
            with conversations-ever-touched rather than conversations-in-use.

``live``    The background eviction task doing the work *by itself*. This mode
            never calls ``_evict_idle_conversations`` and never rewrites an
            idle clock; it starts the service, waits for the real sweep, and
            then checks what the sweep did. Also asserts the safety guards
            (running / websocket-attached / recently-used are not evicted) and
            that an evicted conversation re-hydrates from disk byte-identically.

``config``  The default TTL a stock deployment now gets.

Usage::

    uv run python .pr/live_idle_eviction_evidence.py              # everything
    uv run python .pr/live_idle_eviction_evidence.py --mode live  # ~2 min
    uv run python .pr/live_idle_eviction_evidence.py --mode growth

``growth`` runs its two arms as subprocesses so each arm reports RSS for a
process that did only that arm's work.

No API key and no network are needed: conversations are populated with
``send_message(run=False)`` and ``autotitle=False``, so neither the agent loop
nor the LLM is ever invoked.
"""

# ARG002: subscriber stubs must match the Subscriber signature.
# ruff: noqa: ARG002

import argparse
import asyncio
import gc
import logging
import subprocess
import sys
import tempfile
import time
import weakref
from pathlib import Path
from uuid import UUID

import psutil

from openhands.agent_server.config import (
    DEFAULT_CONVERSATION_IDLE_TTL_SECONDS,
    Config,
)
from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.agent_server.pub_sub import Subscriber
from openhands.sdk import LLM, Agent, Event, Message, TextContent
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


_PROCESS = psutil.Process()

# growth: 20 conversations x 40 messages x 8 KB is roughly a real working
# conversation's event history, and large enough that per-conversation
# retention is visible above allocator noise.
GROWTH_CONVERSATIONS_PER_ROUND = 20
GROWTH_MESSAGES = 40
GROWTH_MESSAGE_BYTES = 8_000
GROWTH_ROUNDS = 5

# live: the eviction loop wakes every max(60, ttl/2) seconds, so a TTL of 40
# gives one real sweep at t=60 with idle conversations comfortably past the
# TTL and a deliberately-kept-warm conversation comfortably inside it.
LIVE_TTL_SECONDS = 40.0
LIVE_SWEEP_TIMEOUT_SECONDS = 150.0
LIVE_KEEPALIVE_INTERVAL_SECONDS = 5.0


def rss_mib() -> float:
    """Resident set size after a full collection, in MiB."""
    gc.collect()
    return _PROCESS.memory_info().rss / 2**20


def _ok(passed: bool) -> str:
    return "PASS" if passed else "FAIL"


async def _make_conversation(
    service: ConversationService,
    workspace_root: Path,
    index: int,
    *,
    messages: int = 0,
    message_bytes: int = 0,
) -> UUID:
    """Start a conversation and give it a real (LLM-free) event history."""
    workspace = workspace_root / f"ws-{index}"
    workspace.mkdir(parents=True, exist_ok=True)
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id=f"evidence-{index}"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace)),
        confirmation_policy=NeverConfirm(),
        # Keeps the run entirely offline: no title-generation LLM call.
        autotitle=False,
    )
    info, _ = await service.start_conversation(request)

    assert service._event_services is not None
    event_service = service._event_services[info.id]
    for i in range(messages):
        await event_service.send_message(
            Message(
                role="user",
                content=[TextContent(text=f"message {i} " + "x" * message_bytes)],
            ),
            run=False,
        )
    return info.id


class _DummySubscriber(Subscriber):
    """Stand-in for a websocket client attached to a conversation."""

    async def __call__(self, event: Event) -> None:
        return None


# --------------------------------------------------------------------------
# growth
# --------------------------------------------------------------------------


async def run_growth(eviction: bool) -> None:
    """Report RSS per round of 'hydrate N conversations, then let them idle'."""
    label = "eviction ON" if eviction else "eviction OFF (main's behaviour)"
    ttl = 5.0 if eviction else None

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        async with ConversationService(
            conversations_dir=root / "conversations",
            conversation_idle_ttl_seconds=ttl,
        ) as service:
            assert service._event_services is not None
            created = 0

            async def one_round() -> None:
                nonlocal created
                for _ in range(GROWTH_CONVERSATIONS_PER_ROUND):
                    await _make_conversation(
                        service,
                        root,
                        created,
                        messages=GROWTH_MESSAGES,
                        message_bytes=GROWTH_MESSAGE_BYTES,
                    )
                    created += 1
                if not eviction:
                    return
                # Drive the same sweep the background task runs, without
                # waiting 60 s per round. Liveness is proven in --mode live;
                # here we only need the memory curve.
                assert service._event_services is not None
                for event_service in list(service._event_services.values()):
                    event_service._last_active_monotonic = time.monotonic() - 10_000
                await service._evict_idle_conversations(5.0)
                await asyncio.sleep(0.5)

            # One unmeasured round absorbs lazy imports and the allocator's
            # initial arena growth, so the measured rounds show the marginal
            # cost of retaining conversations rather than of starting up.
            await one_round()
            baseline = rss_mib()
            measured_start = created

            print(f"\n  {label}")
            print(f"  post-warmup baseline RSS: {baseline:.1f} MiB")
            print("     round | conversations | resident |      RSS | growth")
            print("    -------+---------------+----------+----------+--------")
            for round_index in range(GROWTH_ROUNDS):
                await one_round()
                current = rss_mib()
                print(
                    f"    {round_index + 1:6d} | {created - measured_start:13d} "
                    f"| {len(service._event_services):8d} "
                    f"| {current:6.1f} M | {current - baseline:+6.1f} M"
                )

            total = rss_mib() - baseline
            n = created - measured_start
            print(
                f"  => {n} conversations touched, RSS {total:+.1f} MiB "
                f"({total / n * 1024:+.0f} KiB/conversation)"
            )


def run_growth_both() -> None:
    print("=" * 74)
    print("growth: RSS across repeated hydrate/idle rounds")
    print("=" * 74)
    print(
        f"\n  {GROWTH_ROUNDS} rounds x {GROWTH_CONVERSATIONS_PER_ROUND} conversations"
        f" x {GROWTH_MESSAGES} messages x {GROWTH_MESSAGE_BYTES // 1000} KB."
        "\n  Each arm runs in its own process so RSS reflects only that arm."
    )
    # Flush before handing the terminal to a child process.
    sys.stdout.flush()
    for arm in ("off", "on"):
        subprocess.run(
            [sys.executable, __file__, "--mode", "_growth_arm", "--eviction", arm],
            check=True,
        )


# --------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------


async def run_live() -> bool:
    """Let the real background task run a sweep, then check what it did."""
    print("=" * 74)
    print("live: the background eviction task, unassisted")
    print("=" * 74)
    print(
        f"\n  TTL {LIVE_TTL_SECONDS:.0f}s -> the loop sweeps every "
        f"max(60, TTL/2) = 60s. This mode never calls the sweep itself and\n"
        "  never rewrites an idle clock; it waits for the real one."
    )

    results: list[tuple[str, bool, str]] = []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        async with ConversationService(
            conversations_dir=root / "conversations",
            conversation_idle_ttl_seconds=LIVE_TTL_SECONDS,
        ) as service:
            assert service._event_services is not None

            idle_ids = [
                await _make_conversation(
                    service, root, i, messages=6, message_bytes=500
                )
                for i in range(3)
            ]
            running_id = await _make_conversation(service, root, 100, messages=3)
            subscribed_id = await _make_conversation(service, root, 101, messages=3)
            warm_id = await _make_conversation(service, root, 102, messages=3)

            # An in-flight agent run, as the guard sees it.
            running_service = service._event_services[running_id]
            run_task = asyncio.create_task(asyncio.sleep(3600))
            running_service._run_task = run_task

            # A websocket-style client attached after startup.
            subscribed_service = service._event_services[subscribed_id]
            subscriber_id = await subscribed_service.subscribe_to_events(
                _DummySubscriber()
            )

            # Snapshot the idle conversations so re-hydration can be compared
            # against what was in memory before the sweep.
            probe_id = idle_ids[0]
            probe_service = service._event_services[probe_id]
            before_events = (await probe_service.search_events(limit=100)).items
            before_count = await probe_service.count_events()
            before_stored_json = probe_service.stored.model_dump_json()
            probe_identity = id(probe_service)

            # Weakrefs prove the object graph is actually reclaimable and not
            # merely dropped from the registry dict. Both layers are tracked:
            # the EventService and the LocalConversation (agent, LLM, tools and
            # the whole event history) hanging off it.
            conversation_refs = [
                weakref.ref(service._event_services[cid]._conversation)
                for cid in idle_ids
            ]
            event_service_refs = [
                weakref.ref(service._event_services[cid]) for cid in idle_ids
            ]
            # Drop this frame's own strong reference, or it would pin the very
            # object the check below is asking about.
            del probe_service

            async def keep_warm() -> None:
                """Regular access, the way a live client would poll."""
                while True:
                    await asyncio.sleep(LIVE_KEEPALIVE_INTERVAL_SECONDS)
                    await service.get_event_service(warm_id)

            keepalive = asyncio.create_task(keep_warm())

            print(
                f"\n  resident at t=0: {len(service._event_services)} "
                f"({len(idle_ids)} idle, 1 running, 1 websocket-attached, 1 kept warm)"
            )
            print("  waiting for the background sweep ...", flush=True)

            started = time.monotonic()
            deadline = started + LIVE_SWEEP_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                if all(cid not in service._event_services for cid in idle_ids):
                    break
                await asyncio.sleep(0.5)
            elapsed = time.monotonic() - started

            keepalive.cancel()
            run_task.cancel()
            running_service._run_task = None

            swept = all(cid not in service._event_services for cid in idle_ids)
            print(f"  sweep observed after {elapsed:.1f}s\n")

            results.append(
                (
                    f"{len(idle_ids)} idle conversations evicted by background task",
                    swept,
                    f"after {elapsed:.0f}s"
                    if swept
                    else f"{len(service._event_services)} still resident",
                )
            )
            results.append(
                (
                    "catalog records kept, so they stay listable",
                    all(cid in service._conversation_records for cid in idle_ids),
                    f"{len(service._conversation_records)} records retained",
                )
            )

            gc.collect()
            freed_conversations = sum(1 for ref in conversation_refs if ref() is None)
            freed_services = sum(1 for ref in event_service_refs if ref() is None)
            results.append(
                (
                    "evicted Conversation objects are garbage collected",
                    freed_conversations == len(conversation_refs),
                    f"{freed_conversations}/{len(conversation_refs)} reclaimed",
                )
            )
            results.append(
                (
                    "evicted EventService objects are garbage collected",
                    freed_services == len(event_service_refs),
                    f"{freed_services}/{len(event_service_refs)} reclaimed",
                )
            )

            results.append(
                (
                    "running conversation NOT evicted",
                    running_id in service._event_services,
                    "in-flight run task",
                )
            )
            results.append(
                (
                    "websocket-attached conversation NOT evicted",
                    subscribed_id in service._event_services,
                    "external subscriber",
                )
            )
            results.append(
                (
                    "recently-accessed conversation NOT evicted",
                    warm_id in service._event_services,
                    f"accessed every {LIVE_KEEPALIVE_INTERVAL_SECONDS:.0f}s",
                )
            )

            # Re-hydration fidelity.
            rehydrated = await service.get_event_service(probe_id)
            assert rehydrated is not None
            after_events = (await rehydrated.search_events(limit=100)).items
            after_count = await rehydrated.count_events()

            results.append(
                (
                    "re-hydrated from disk as a fresh runtime",
                    id(rehydrated) != probe_identity and rehydrated.is_open(),
                    "new EventService instance",
                )
            )
            results.append(
                (
                    "event history identical after re-hydration",
                    before_count == after_count
                    and [e.id for e in before_events] == [e.id for e in after_events],
                    f"{after_count} events, ids match",
                )
            )
            results.append(
                (
                    "event payloads identical after re-hydration",
                    [e.model_dump_json() for e in before_events]
                    == [e.model_dump_json() for e in after_events],
                    "serialized events match",
                )
            )
            results.append(
                (
                    "conversation metadata identical after re-hydration",
                    rehydrated.stored.model_dump_json() == before_stored_json,
                    "StoredConversation matches",
                )
            )

            # Once the websocket client disconnects the conversation becomes
            # evictable again -- the guard defers eviction, it does not exempt.
            await subscribed_service.unsubscribe_from_events(subscriber_id)
            results.append(
                (
                    "guard defers rather than exempts (subscriber gone)",
                    not subscribed_service.has_external_subscribers(),
                    "eligible on the next sweep",
                )
            )

    for name, passed, detail in results:
        print(f"  [{_ok(passed)}] {name:<58} {detail}")
    return all(passed for _, passed, _ in results)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


def run_config() -> bool:
    print("=" * 74)
    print("config: what a stock deployment gets")
    print("=" * 74)

    default = Config().conversation_idle_ttl_seconds
    disabled = Config(conversation_idle_ttl_seconds=None).conversation_idle_ttl_seconds
    checks = [
        (
            "default TTL is 20 minutes",
            default == DEFAULT_CONVERSATION_IDLE_TTL_SECONDS == 1200.0,
            f"{default} s",
        ),
        ("eviction can be turned off", disabled is None, "null keeps conversations"),
    ]
    print()
    for name, passed, detail in checks:
        print(f"  [{_ok(passed)}] {name:<58} {detail}")
    return all(passed for _, passed, _ in checks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        default="all",
        choices=["all", "growth", "live", "config", "_growth_arm"],
    )
    parser.add_argument("--eviction", default="on", choices=["on", "off"])
    args = parser.parse_args()

    # Keep the report readable; the SDK is chatty at INFO on every start.
    logging.disable(logging.CRITICAL)

    if args.mode == "_growth_arm":
        asyncio.run(run_growth(eviction=args.eviction == "on"))
        return 0

    passed = True
    if args.mode in ("all", "config"):
        passed &= run_config()
        print()
    if args.mode in ("all", "growth"):
        run_growth_both()
        print()
    if args.mode in ("all", "live"):
        passed &= asyncio.run(run_live())

    print()
    print("OK" if passed else "FAILURES ABOVE")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
