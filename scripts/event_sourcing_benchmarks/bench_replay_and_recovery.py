#!/usr/bin/env python3
"""
Benchmark: Replay time vs. log size and time-to-recover after failures.

Collects real event payloads from SWE-Bench evaluation traces, builds event
logs of increasing size, and measures:
  - Index rebuild time (directory listing + filename regex parse)
  - Full replay time (read + JSON parse all events)
  - Time-to-recover (full deserialization + unmatched-action detection
    using the SDK's ConversationState.get_unmatched_actions)

Usage:
    python bench_replay_and_recovery.py --eval-dir <path-to-eval-run>
"""

import argparse
import gc
import json
import os
import re
import shutil
import statistics
import tempfile
import time

from benchmark_utils import (
    extract_conversation,
    read_event_files,
    register_tool_types,
)


EVENTS_DIR_NAME = "events"


def collect_event_pool(eval_dir: str, target_count: int = 2000) -> list[dict]:
    """Collect events from conversation traces until we have enough."""
    conv_dir = os.path.join(eval_dir, "conversations")
    tarballs = sorted(os.listdir(conv_dir))

    all_events: list[dict] = []
    for tarname in tarballs:
        tarpath = os.path.join(conv_dir, tarname)
        tmpdir = tempfile.mkdtemp(prefix="bench_pool_")
        try:
            events_dir = extract_conversation(tarpath, tmpdir)
            if events_dir:
                events = read_event_files(events_dir)
                all_events.extend(events)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if len(all_events) >= target_count:
            break

    print(f"  Collected {len(all_events)} real events from traces")
    sizes = [e["size_bytes"] for e in all_events]
    print(
        f"  Size distribution: median={statistics.median(sizes):.0f}B, "
        f"mean={statistics.mean(sizes):.0f}B, "
        f"min={min(sizes)}B, max={max(sizes)}B"
    )
    return all_events


def benchmark_replay_and_recovery(
    event_pool: list[dict], n_trials: int = 5
) -> list[dict]:
    """Measure replay time and time-to-recover at increasing log sizes."""
    from openhands.sdk.conversation.state import ConversationState
    from openhands.sdk.event.base import Event

    checkpoints = [10, 25, 50, 100, 200, 500, 1000, 1500]
    pattern = re.compile(r"^event-(\d+)-([a-f0-9\-]+)\.json$")

    results = []
    for target in checkpoints:
        if target > len(event_pool):
            break

        events = event_pool[:target]

        tmpdir = tempfile.mkdtemp(prefix="bench_replay_")
        try:
            events_dir = os.path.join(tmpdir, EVENTS_DIR_NAME)
            os.makedirs(events_dir)
            for ef in events:
                path = os.path.join(events_dir, ef["filename"])
                with open(path, "w") as f:
                    f.write(ef["json_str"])

            total_bytes = sum(ef["size_bytes"] for ef in events)

            all_files = sorted(os.listdir(events_dir))
            json_files = [f for f in all_files if f.endswith(".json")]

            # Index rebuild: list dir + parse filenames
            index_times = []
            for _ in range(n_trials):
                gc.disable()
                t0 = time.perf_counter()
                files = sorted(os.listdir(events_dir))
                jfiles = [f for f in files if f.endswith(".json")]
                index = {}
                for fname in jfiles:
                    m = pattern.match(fname)
                    if m:
                        index[int(m.group(1))] = fname
                t1 = time.perf_counter()
                gc.enable()
                index_times.append((t1 - t0) * 1000)

            # Full replay: read + JSON parse all events
            replay_times = []
            for _ in range(n_trials):
                gc.disable()
                t0 = time.perf_counter()
                for fname in json_files:
                    path = os.path.join(events_dir, fname)
                    with open(path) as f:
                        json.load(f)
                t1 = time.perf_counter()
                gc.enable()
                replay_times.append((t1 - t0) * 1000)

            # Time-to-recover: deserialize via SDK + get_unmatched_actions
            recovery_times = []
            for _ in range(n_trials):
                gc.disable()
                t0 = time.perf_counter()
                deserialized = []
                for fname in json_files:
                    path = os.path.join(events_dir, fname)
                    with open(path) as f:
                        content = f.read()
                    deserialized.append(Event.model_validate_json(content))
                ConversationState.get_unmatched_actions(deserialized)
                t1 = time.perf_counter()
                gc.enable()
                recovery_times.append((t1 - t0) * 1000)

            def stats(times: list[float]) -> dict:
                s = sorted(times)
                n = len(s)
                return {
                    "median": s[n // 2],
                    "mean": statistics.mean(s),
                    "min": min(s),
                    "max": max(s),
                }

            r = {
                "n_events": target,
                "total_bytes": total_bytes,
                "total_kb": total_bytes / 1024,
                "index_rebuild_ms": stats(index_times),
                "full_replay_ms": stats(replay_times),
                "time_to_recover_ms": stats(recovery_times),
            }
            results.append(r)

            idx_ms = r["index_rebuild_ms"]["median"]
            rpl_ms = r["full_replay_ms"]["median"]
            rec_ms = r["time_to_recover_ms"]["median"]
            print(
                f"  {target:>5} events"
                f" ({total_bytes / 1024:>7.1f}KB):"
                f" index={idx_ms:.2f}ms"
                f"  replay={rpl_ms:.2f}ms"
                f"  recover={rec_ms:.2f}ms"
            )

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return results


def main():
    import logging

    logging.getLogger("openhands").setLevel(logging.ERROR)
    register_tool_types()

    parser = argparse.ArgumentParser(
        description=("Benchmark replay time and time-to-recover vs. log size")
    )
    parser.add_argument(
        "--eval-dir",
        required=True,
        help="Path to evaluation run directory",
    )
    parser.add_argument(
        "--output",
        default="bench_replay_and_recovery_results.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--n-trials",
        type=int,
        default=5,
        help="Number of trials per checkpoint (default: 5)",
    )
    args = parser.parse_args()

    print("Collecting real event payloads from traces...")
    event_pool = collect_event_pool(args.eval_dir)

    print(f"\n{'=' * 70}")
    print("Replay Time and Time-to-Recover vs. Log Size")
    print(f"{'=' * 70}")
    results = benchmark_replay_and_recovery(event_pool, n_trials=args.n_trials)

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
