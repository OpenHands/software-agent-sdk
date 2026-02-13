#!/usr/bin/env python3
"""
Benchmark: Persist latency per event and per action cycle.

Extracts real event payloads from SWE-Bench evaluation conversation traces
and replays them through the SDK's LocalFileStore lock-and-write path to
measure per-event and per-cycle persist latency.

Usage:
    python bench_persist_latency.py --eval-dir <path-to-eval-run>
"""
import argparse
import gc
import json
import os
import shutil
import statistics
import tarfile
import tempfile
import time

import logging

logging.disable(logging.WARNING)

from openhands.sdk.io import LocalFileStore

EVENTS_DIR_NAME = "events"
LOCK_FILE = "events/.eventlog.lock"


def extract_conversation(tarpath: str, dest: str) -> str | None:
    """Extract a conversation .tar.gz and return the events/ dir path."""
    with tarfile.open(tarpath, "r:gz") as tf:
        tf.extractall(dest, filter="data")
    for root, _, _ in os.walk(dest):
        if os.path.basename(root) == "events":
            return root
    return None


def read_event_files(events_dir: str) -> list[dict]:
    """Read all event JSON files. Return list of {filename, json_str, size, kind}."""
    files = sorted(f for f in os.listdir(events_dir) if f.endswith(".json"))
    result = []
    for fname in files:
        path = os.path.join(events_dir, fname)
        with open(path) as f:
            content = f.read()
        try:
            kind = json.loads(content).get("kind", "unknown")
        except Exception:
            kind = "unknown"
        result.append(
            {
                "filename": fname,
                "json_str": content,
                "size_bytes": len(content.encode("utf-8")),
                "kind": kind,
            }
        )
    return result


def measure_persist_latencies(event_files: list[dict]) -> list[dict]:
    """Replay the persist path EventLog.append() uses:
    lock -> write JSON file -> release lock

    Uses LocalFileStore directly with real event payloads.
    """
    tmpdir = tempfile.mkdtemp(prefix="bench_persist_")
    try:
        fs = LocalFileStore(tmpdir, cache_limit_size=len(event_files) + 100)

        results = []
        for i, ef in enumerate(event_files):
            target_path = f"{EVENTS_DIR_NAME}/{ef['filename']}"

            gc.disable()
            t0 = time.perf_counter()
            with fs.lock(LOCK_FILE, timeout=30.0):
                fs.write(target_path, ef["json_str"])
            t1 = time.perf_counter()
            gc.enable()

            results.append(
                {
                    "kind": ef["kind"],
                    "size_bytes": ef["size_bytes"],
                    "persist_ms": (t1 - t0) * 1000,
                    "event_idx": i,
                }
            )
        return results
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Benchmark persist latency per event/action cycle")
    parser.add_argument("--eval-dir", required=True, help="Path to evaluation run directory (contains conversations/ and output.jsonl)")
    parser.add_argument("--output", default="bench_persist_latency_results.json", help="Output JSON file path")
    parser.add_argument("--sample-step", type=int, default=15, help="Sample every Nth conversation (default: 15)")
    args = parser.parse_args()

    # Load instance metadata
    instances = {}
    with open(os.path.join(args.eval_dir, "output.jsonl")) as f:
        for line in f:
            d = json.loads(line)
            instances[d["instance_id"]] = d

    conv_dir = os.path.join(args.eval_dir, "conversations")
    tarballs = sorted(os.listdir(conv_dir))
    sample_tarballs = tarballs[:: args.sample_step]
    print(f"Sampling {len(sample_tarballs)} of {len(tarballs)} conversations\n")

    all_persist = []
    conv_summaries = []

    for tarname in sample_tarballs:
        instance_id = tarname.replace(".tar.gz", "")
        instance_data = instances.get(instance_id)
        if not instance_data:
            continue

        tarpath = os.path.join(conv_dir, tarname)
        tmpdir = tempfile.mkdtemp(prefix="bench_persist_")
        try:
            events_dir = extract_conversation(tarpath, tmpdir)
            if not events_dir:
                continue
            event_files = read_event_files(events_dir)
            if not event_files:
                continue

            persist_results = measure_persist_latencies(event_files)
            all_persist.extend(persist_results)

            # Per-cycle persist time (action + observation pairs)
            action_p = [r for r in persist_results if r["kind"] == "ActionEvent"]
            obs_p = [r for r in persist_results if r["kind"] == "ObservationEvent"]
            n_cycles = min(len(action_p), len(obs_p))
            cycle_persist = [action_p[i]["persist_ms"] + obs_p[i]["persist_ms"] for i in range(n_cycles)]

            total_persist_ms = sum(r["persist_ms"] for r in persist_results)

            conv_summaries.append(
                {
                    "instance_id": instance_id,
                    "n_events": len(event_files),
                    "n_cycles": n_cycles,
                    "total_persist_ms": total_persist_ms,
                    "mean_cycle_persist_ms": statistics.mean(cycle_persist) if cycle_persist else 0,
                }
            )
            print(f"  {instance_id[:50]:50s}  events={len(event_files):>4}  persist={total_persist_ms:>7.1f}ms")

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # --- Analysis ---
    print(f"\n{'='*70}")
    print("RESULTS: Persist Latency per Event / Action Cycle")
    print(f"{'='*70}")

    # Per-event persist latency by type
    by_kind = {}
    for r in all_persist:
        by_kind.setdefault(r["kind"], []).append(r)

    print(f"\n--- Per-Event Persist Latency ---")
    print(f"  {'Event Type':<35} {'N':>5} {'Median':>10} {'Mean':>10} {'P95':>10} {'MedSize':>10}")
    print(f"  {'-'*80}")
    for kind in [
        "SystemPromptEvent",
        "MessageEvent",
        "ActionEvent",
        "ObservationEvent",
        "ConversationStateUpdateEvent",
        "AgentErrorEvent",
    ]:
        if kind not in by_kind:
            continue
        entries = by_kind[kind]
        lats = sorted([e["persist_ms"] for e in entries])
        sizes = sorted([e["size_bytes"] for e in entries])
        n = len(lats)
        print(
            f"  {kind:<35} {n:>5} {lats[n//2]:>9.3f}ms "
            f"{statistics.mean(lats):>9.3f}ms {lats[int(n*0.95)]:>9.3f}ms "
            f"{sizes[n//2]:>8,}B"
        )

    all_lats = sorted([r["persist_ms"] for r in all_persist])
    all_sizes = sorted([r["size_bytes"] for r in all_persist])
    n = len(all_lats)
    print(f"  {'-'*80}")
    print(
        f"  {'ALL EVENTS':<35} {n:>5} {all_lats[n//2]:>9.3f}ms "
        f"{statistics.mean(all_lats):>9.3f}ms {all_lats[int(n*0.95)]:>9.3f}ms "
        f"{all_sizes[n//2]:>8,}B"
    )

    # Per action cycle
    print(f"\n--- Per Action Cycle (Action + Observation) ---")
    cycle_persists = [s["mean_cycle_persist_ms"] for s in conv_summaries if s["n_cycles"] > 0]
    print(f"  Median per-cycle persist time:  {statistics.median(cycle_persists):.2f}ms")
    print(f"  Mean per-cycle persist time:    {statistics.mean(cycle_persists):.2f}ms")

    # Save
    with open(args.output, "w") as f:
        json.dump({"per_event": all_persist, "conversations": conv_summaries}, f, indent=2)
    print(f"\nRaw data saved to {args.output}")


if __name__ == "__main__":
    main()
