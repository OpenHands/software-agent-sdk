#!/usr/bin/env python3
"""
Benchmark: Storage growth across all evaluation conversations.

Analyzes the on-disk footprint of persisted event logs from a full
SWE-Bench evaluation run. Reports conversation size distribution and
storage composition by event type.

Usage:
    python bench_storage_growth.py --eval-dir <path-to-eval-run>
"""
import argparse
import json
import os
import statistics
import tarfile
import tempfile
import shutil


def analyze_conversation(tarpath: str) -> dict | None:
    tmpdir = tempfile.mkdtemp(prefix="bench_storage_")
    try:
        with tarfile.open(tarpath, "r:gz") as tf:
            tf.extractall(tmpdir, filter="data")

        events_dir = None
        for root, _, _ in os.walk(tmpdir):
            if os.path.basename(root) == "events":
                events_dir = root
                break
        if not events_dir:
            return None

        files = sorted(f for f in os.listdir(events_dir) if f.endswith(".json"))
        if not files:
            return None

        by_kind = {}
        total_bytes = 0
        for fname in files:
            path = os.path.join(events_dir, fname)
            size = os.path.getsize(path)
            total_bytes += size

            with open(path) as f:
                content = f.read()
            try:
                kind = json.loads(content).get("kind", "unknown")
            except Exception:
                kind = "unknown"

            if kind not in by_kind:
                by_kind[kind] = {"count": 0, "total_bytes": 0}
            by_kind[kind]["count"] += 1
            by_kind[kind]["total_bytes"] += size

        return {
            "n_events": len(files),
            "total_bytes": total_bytes,
            "by_kind": by_kind,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Benchmark storage growth across evaluation conversations")
    parser.add_argument("--eval-dir", required=True, help="Path to evaluation run directory (contains conversations/)")
    parser.add_argument("--output", default="bench_storage_growth_results.json", help="Output JSON file path")
    args = parser.parse_args()

    conv_dir = os.path.join(args.eval_dir, "conversations")
    tarballs = sorted(os.listdir(conv_dir))
    print(f"Analyzing all {len(tarballs)} conversations...")

    all_convs = []
    for i, tarname in enumerate(tarballs):
        instance_id = tarname.replace(".tar.gz", "")
        tarpath = os.path.join(conv_dir, tarname)

        conv = analyze_conversation(tarpath)
        if not conv:
            continue

        conv["instance_id"] = instance_id
        all_convs.append(conv)

        if (i + 1) % 50 == 0:
            print(f"  Processed {i+1}/{len(tarballs)}...")

    print(f"\n  Analyzed {len(all_convs)} conversations total")

    # --- Conversation Size Distribution ---
    print(f"\n{'='*70}")
    print("1. Conversation Size Distribution")
    print(f"{'='*70}")
    n_events_all = sorted([c["n_events"] for c in all_convs])
    sizes_kb = sorted([c["total_bytes"] / 1024 for c in all_convs])
    n = len(n_events_all)
    print(f"  Events per conversation:")
    print(
        f"    Min={n_events_all[0]}  P25={n_events_all[n//4]}  "
        f"Median={n_events_all[n//2]}  P75={n_events_all[3*n//4]}  "
        f"Max={n_events_all[-1]}"
    )
    print(f"    Mean={statistics.mean(n_events_all):.1f}  Stdev={statistics.stdev(n_events_all):.1f}")
    print(f"  Storage per conversation:")
    print(
        f"    Min={sizes_kb[0]:.1f}KB  Median={sizes_kb[n//2]:.1f}KB  "
        f"P75={sizes_kb[3*n//4]:.1f}KB  P95={sizes_kb[int(n*0.95)]:.1f}KB  "
        f"Max={sizes_kb[-1]:.1f}KB"
    )

    # --- Storage Composition ---
    print(f"\n{'='*70}")
    print("2. Storage Composition by Event Type")
    print(f"{'='*70}")
    global_kinds = {}
    for c in all_convs:
        for kind, data in c["by_kind"].items():
            if kind not in global_kinds:
                global_kinds[kind] = {"count": 0, "total_bytes": 0}
            global_kinds[kind]["count"] += data["count"]
            global_kinds[kind]["total_bytes"] += data["total_bytes"]

    total_all_bytes = sum(v["total_bytes"] for v in global_kinds.values())
    total_all_events = sum(v["count"] for v in global_kinds.values())

    print(f"  {'Event Type':<35} {'Count':>7} {'%Events':>8} {'TotalMB':>9} {'%Storage':>9} {'AvgKB':>8}")
    print(f"  {'-'*78}")
    for kind in sorted(global_kinds, key=lambda k: global_kinds[k]["total_bytes"], reverse=True):
        d = global_kinds[kind]
        pct_events = d["count"] / total_all_events * 100
        pct_storage = d["total_bytes"] / total_all_bytes * 100
        avg_kb = d["total_bytes"] / d["count"] / 1024
        print(
            f"  {kind:<35} {d['count']:>7} {pct_events:>7.1f}% {d['total_bytes']/1024/1024:>8.1f}MB "
            f"{pct_storage:>8.1f}% {avg_kb:>7.2f}KB"
        )
    print(f"  {'-'*78}")
    print(f"  {'TOTAL':<35} {total_all_events:>7} {'100.0':>7}% {total_all_bytes/1024/1024:>8.1f}MB")

    # Save
    output = {
        "n_conversations": len(all_convs),
        "conversation_sizes": {
            "events": {
                "min": n_events_all[0],
                "p25": n_events_all[n // 4],
                "median": n_events_all[n // 2],
                "p75": n_events_all[3 * n // 4],
                "max": n_events_all[-1],
                "mean": statistics.mean(n_events_all),
            },
            "storage_kb": {
                "min": sizes_kb[0],
                "median": sizes_kb[n // 2],
                "p95": sizes_kb[int(n * 0.95)],
                "max": sizes_kb[-1],
            },
        },
        "storage_composition": {
            kind: {
                "count": global_kinds[kind]["count"],
                "total_bytes": global_kinds[kind]["total_bytes"],
                "pct_storage": global_kinds[kind]["total_bytes"] / total_all_bytes * 100,
            }
            for kind in global_kinds
        },
    }
    with open(args.output, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
