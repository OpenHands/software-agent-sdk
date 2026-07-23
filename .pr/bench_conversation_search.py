"""Benchmark for issue #3142: O(N) full scan for conversation search and count.

Builds ``N`` idle conversations on disk, then times the ``ConversationService``
reads behind ``GET /conversations`` and ``GET /conversations/count``, filtered
and unfiltered. What matters is the slope: a fixed-size page should cost the
same at 100 conversations as at 2000.

Usage::

    uv run python .pr/bench_conversation_search.py
    uv run python .pr/bench_conversation_search.py --sizes 100,500,1000 --repeat 5
"""

import argparse
import asyncio
import json
import shutil
import statistics
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from openhands.agent_server.conversation_service import ConversationService
from openhands.agent_server.models import StartConversationRequest
from openhands.sdk import LLM, Agent
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.security.confirmation_policy import NeverConfirm
from openhands.sdk.workspace import LocalWorkspace


async def _build_template(root: Path) -> Path:
    """Start one real conversation so the on-disk layout is authentic."""
    workspace = root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    conversations_dir = root / "template"
    request = StartConversationRequest(
        agent=Agent(llm=LLM(model="gpt-4o", usage_id="bench-llm"), tools=[]),
        workspace=LocalWorkspace(working_dir=str(workspace)),
        confirmation_policy=NeverConfirm(),
    )
    async with ConversationService(conversations_dir=conversations_dir) as service:
        info, _ = await service.start_conversation(request)
    return conversations_dir / info.id.hex


def _clone(template: Path, conversations_dir: Path, count: int) -> None:
    """Fan the template out into ``count`` distinct conversation directories."""
    conversations_dir.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        conversation_id = uuid4()
        target = conversations_dir / conversation_id.hex
        shutil.copytree(template, target)
        # Rewrite identity fields so each clone is a distinct conversation.
        for name in ("meta.json", "base_state.json"):
            path = target / name
            if not path.exists():
                continue
            payload = json.loads(path.read_text())
            payload["id"] = str(conversation_id)
            if "persistence_dir" in payload:
                payload["persistence_dir"] = str(target)
            # Spread timestamps so sorting has real work to do.
            if "created_at" in payload:
                payload["created_at"] = f"2026-01-01T00:00:{i % 60:02d}.{i:06d}Z"
            if "updated_at" in payload:
                payload["updated_at"] = f"2026-01-02T00:00:{i % 60:02d}.{i:06d}Z"
            path.write_text(json.dumps(payload))
        # Drop the template's lease so each clone reads as unowned.
        for stale in target.glob("*.lease"):
            stale.unlink()


async def _time(fn, repeat: int) -> tuple[float, float]:
    """Return (median_ms, min_ms) over ``repeat`` calls, after one warmup."""
    await fn()
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), min(samples)


async def _measure(conversations_dir: Path, repeat: int) -> dict[str, float]:
    async with ConversationService(conversations_dir=conversations_dir) as service:
        idle = ConversationExecutionStatus.IDLE
        results = {}
        results["count"], _ = await _time(lambda: service.count_conversations(), repeat)
        results["count_filtered"], _ = await _time(
            lambda: service.count_conversations(execution_status=idle), repeat
        )
        results["page"], _ = await _time(
            lambda: service.search_conversations(limit=20), repeat
        )
        results["page_filtered"], _ = await _time(
            lambda: service.search_conversations(limit=20, execution_status=idle),
            repeat,
        )
        return results


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        default="100,500,1000,2000",
        help="Comma-separated conversation counts to measure.",
    )
    parser.add_argument(
        "--repeat", type=int, default=5, help="Timed calls per measurement."
    )
    args = parser.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]

    root = Path(tempfile.mkdtemp(prefix="bench-conv-search-"))
    try:
        template = await _build_template(root)

        header = (
            f"{'N':>6} | {'count()':>10} | {'count(status)':>14} | "
            f"{'page(20)':>10} | {'page(20,status)':>16}"
        )
        print(f"\nAll timings in milliseconds (median of {args.repeat} calls)\n")
        print(header)
        print("-" * len(header))

        rows = []
        for n in sizes:
            conversations_dir = root / f"run-{n}"
            _clone(template, conversations_dir, n)
            r = await _measure(conversations_dir, args.repeat)
            rows.append((n, r))
            print(
                f"{n:>6} | {r['count']:>10.2f} | {r['count_filtered']:>14.2f} | "
                f"{r['page']:>10.2f} | {r['page_filtered']:>16.2f}"
            )
            shutil.rmtree(conversations_dir, ignore_errors=True)

        if len(rows) >= 2:
            (small_n, small), (big_n, big) = rows[0], rows[-1]
            print(f"\nScaling from N={small_n} to N={big_n}:")
            for key, label in (
                ("count", "count()"),
                ("count_filtered", "count(status)"),
                ("page", "page(20)"),
                ("page_filtered", "page(20,status)"),
            ):
                base = small[key] if small[key] > 0 else float("inf")
                print(f"  {label:<18} x{big[key] / base:>7.1f}")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
