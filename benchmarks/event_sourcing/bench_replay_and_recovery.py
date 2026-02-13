#!/usr/bin/env python3
"""
Benchmark: Replay time vs. log size and time-to-recover after failures.

Collects real event payloads from SWE-Bench evaluation traces, builds event
logs of increasing size, and measures:
  - Index rebuild time (directory listing + filename regex parse)
  - Full replay time (read + JSON parse all events)
  - Time-to-recover (full replay + reverse scan for unmatched actions)

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
import tarfile
import tempfile
import time

EVENTS_DIR_NAME = "events"


def extract_conversation(tarpath: str, dest: str) -> str | None:
    with tarfile.open(tarpath, "r:gz") as tf:
        tf.extractall(dest, filter="data")
    for root, _, _ in os.walk(dest):
        if os.path.basename(root) == "events":
            return root
    return None


def read_event_files(events_dir: str) -> list[dict]:
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


def collect_event_pool(eval_dir: str, target_count: int = 2000) -> list[dict]:
    """Collect events from conversation traces until we have enough."""
    conv_dir = os.path.join(eval_dir, "conversations")
    tarballs = sorted(os.listdir(conv_dir))

    all_events = []
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


def benchmark_replay_and_recovery(event_pool: list[dict], n_trials: int = 5):
    """Measure replay time and time-to-recover at increasing log sizes."""
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
                with open(os.path.join(events_dir, ef["filename"]), "w") as f:
                    f.write(ef["json_str"])

            total_bytes = sum(ef["size_bytes"] for ef in events)

            # Index rebuild: list dir + parse filenames
            index_times = []
            for _ in range(n_trials):
                gc.disable()
                t0 = time.perf_counter()
                files = sorted(os.listdir(events_dir))
                json_files = [f for f in files if f.endswith(".json")]
                index = {}
                for fname in json_files:
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

            # Time-to-recover: full replay + reverse scan for unmatched actions
            recovery_times = []
            for _ in range(n_trials):
                gc.disable()
                t0 = time.perf_counter()
                loaded_events = []
                for fname in json_files:
                    path = os.path.join(events_dir, fname)
                    with open(path) as f:
                        loaded_events.append(json.load(f))
                seen_action_ids = set()
                unmatched = []
                for ev in reversed(loaded_events):
                    kind = ev.get("kind", "")
                    if kind == "ObservationEvent":
                        aid = ev.get("action_id")
                        if aid:
                            seen_action_ids.add(aid)
                    elif kind == "ActionEvent":
                        eid = ev.get("id")
                        if eid and eid not in seen_action_ids:
                            unmatched.append(ev)
                t1 = time.perf_counter()
                gc.enable()
                recovery_times.append((t1 - t0) * 1000)

            def stats(times):
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

            print(
                f"  {target:>5} events ({total_bytes/1024:>7.1f}KB): "
                f"index={r['index_rebuild_ms']['median']:.2f}ms  "
                f"replay={r['full_replay_ms']['median']:.2f}ms  "
                f"recover={r['time_to_recover_ms']['median']:.2f}ms"
            )

        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark replay time and time-to-recover vs. log size")
    parser.add_argument("--eval-dir", required=True, help="Path to evaluation run directory (contains conversations/)")
    parser.add_argument("--output", default="bench_replay_and_recovery_results.json", help="Output JSON file path")
    parser.add_argument("--n-trials", type=int, default=5, help="Number of trials per checkpoint (default: 5)")
    args = parser.parse_args()

    print("Collecting real event payloads from traces...")
    event_pool = collect_event_pool(args.eval_dir)

    print(f"\n{'='*70}")
    print("Replay Time and Time-to-Recover vs. Log Size")
    print(f"{'='*70}")
    results = benchmark_replay_and_recovery(event_pool, n_trials=args.n_trials)

    with open(args.output, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
