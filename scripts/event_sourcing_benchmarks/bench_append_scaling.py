#!/usr/bin/env python3
"""
Benchmark: EventLog.append latency vs. conversation length.

`bench_persist_latency.py` measures the lock-and-write path in isolation, which
is flat by construction. This script measures the whole of `EventLog.append()`,
including the multi-writer disk sync it performs while holding the lock, so any
per-append cost that scales with the number of events already in the log shows
up here.

Self-contained: it synthesizes events instead of replaying an evaluation run,
so it needs no --eval-dir and runs in seconds.

Usage:
    python bench_append_scaling.py [--events 4000] [--buckets 10]
"""

import argparse
import gc
import json
import shutil
import statistics
import tempfile
import time

from openhands.sdk.conversation.event_store import EventLog
from openhands.sdk.event.llm_convertible import MessageEvent
from openhands.sdk.io import LocalFileStore
from openhands.sdk.llm import Message, TextContent


# Median persisted event in the SWE-Bench traces is ~1.4KB (see README).
PAYLOAD_BYTES = 1_400


def make_event(idx: int) -> MessageEvent:
    return MessageEvent(
        id=f"{idx:08x}-0000-0000-0000-000000000000",
        llm_message=Message(
            role="user", content=[TextContent(text="x" * PAYLOAD_BYTES)]
        ),
        source="user",
    )


def measure_append_latencies(n_events: int) -> list[float]:
    """Append `n_events` through the real EventLog path, timing each append."""
    tmpdir = tempfile.mkdtemp(prefix="bench_append_")
    try:
        fs = LocalFileStore(tmpdir, cache_limit_size=n_events + 100)
        log = EventLog(fs)

        latencies: list[float] = []
        for i in range(n_events):
            event = make_event(i)

            gc.disable()
            t0 = time.perf_counter()
            log.append(event)
            t1 = time.perf_counter()
            gc.enable()

            latencies.append((t1 - t0) * 1000)
        return latencies
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    import logging

    logging.getLogger("openhands").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(
        description="Benchmark EventLog.append latency vs. conversation length"
    )
    parser.add_argument(
        "--events",
        type=int,
        default=4000,
        help="Number of events to append (default: 4000)",
    )
    parser.add_argument(
        "--buckets",
        type=int,
        default=10,
        help="Number of equal-width buckets to report (default: 10)",
    )
    parser.add_argument(
        "--output",
        default="bench_append_scaling_results.json",
        help="Output JSON file path",
    )
    args = parser.parse_args()

    print(f"Appending {args.events} events through EventLog.append()...\n")
    latencies = measure_append_latencies(args.events)

    print(f"{'=' * 62}")
    print("RESULTS: Append Latency vs. Log Length")
    print(f"{'=' * 62}")
    print(f"\n  {'Log length at append':<24} {'N':>6} {'Median':>11} {'Mean':>11}")
    print(f"  {'-' * 56}")

    width = max(1, args.events // args.buckets)
    buckets: list[dict] = []
    for start in range(0, args.events, width):
        chunk = latencies[start : start + width]
        if not chunk:
            continue
        bucket = {
            "start": start,
            "end": start + len(chunk),
            "n": len(chunk),
            "median_ms": statistics.median(chunk),
            "mean_ms": statistics.mean(chunk),
        }
        buckets.append(bucket)
        label = f"{bucket['start']:,} - {bucket['end']:,}"
        print(
            f"  {label:<24} {bucket['n']:>6}"
            f" {bucket['median_ms']:>9.3f}ms {bucket['mean_ms']:>9.3f}ms"
        )

    first, last = buckets[0]["median_ms"], buckets[-1]["median_ms"]
    growth = last / first if first else float("inf")
    print(f"  {'-' * 56}")
    print(f"\n  First bucket median: {first:.3f}ms")
    print(f"  Last bucket median:  {last:.3f}ms")
    print(f"  Growth factor:       {growth:.2f}x  (1.00x == flat)")

    with open(args.output, "w") as f:
        json.dump(
            {"n_events": args.events, "buckets": buckets, "growth_factor": growth},
            f,
            indent=2,
        )
    print(f"\nRaw data saved to {args.output}")


if __name__ == "__main__":
    main()
