#!/usr/bin/env python3
"""Benchmark: concurrent GET /api/conversations/search under load.

Measures:
  - p50/p99 request latency
  - event-loop responsiveness: the max observed gap between two adjacent
    responses to a tight stream of lightweight /server_info probes. Large
    gaps indicate the single event-loop thread stalled (e.g. in GC).

Usage: conversation_search_latency.py <base_url> <session_key> [durations]
"""

import argparse
import statistics  # noqa: F401 (kept for interactive reuse)
import threading
import time
import urllib.request


def make_request(url, key):
    return urllib.request.Request(url, headers={"X-Session-API-Key": key})


def pct(values, p):
    s = sorted(values)
    i = min(len(s) - 1, int(len(s) * p))
    return s[i]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("key")
    ap.add_argument("--search-concurrency", type=int, default=12)
    ap.add_argument("--duration", type=float, default=10.0)
    a = ap.parse_args()

    key = a.key
    search_url = (
        f"{a.base}/api/conversations/search?limit=20&sort_order=UPDATED_AT_DESC"
    )
    probe_url = f"{a.base}/server_info"

    latencies = []
    probe_gaps = []
    lock = threading.Lock()
    stop = time.monotonic() + a.duration

    def search_loop():
        while time.monotonic() < stop:
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(make_request(search_url, key), timeout=10):
                    pass
            except Exception:
                pass
            t1 = time.perf_counter()
            with lock:
                latencies.append(t1 - t0)

    def probe_loop():
        # fire probes as fast as possible; gaps = event-loop stalls
        prev = None
        while time.monotonic() < stop:
            try:
                with urllib.request.urlopen(make_request(probe_url, key), timeout=10):
                    pass
            except Exception:
                pass
            t1 = time.perf_counter()
            if prev is not None:
                with lock:
                    probe_gaps.append(t1 - prev)
            prev = t1

    threads = []
    for _ in range(a.search_concurrency):
        t = threading.Thread(target=search_loop)
        t.start()
        threads.append(t)
    p = threading.Thread(target=probe_loop)
    p.start()
    threads.append(p)
    for t in threads:
        t.join()

    print(f"search_requests={len(latencies)} concurrency={a.search_concurrency}")
    print(
        "search_latency_ms  "
        f"p50={pct(latencies, 0.5) * 1000:.2f}  "
        f"p99={pct(latencies, 0.99) * 1000:.2f}  "
        f"max={max(latencies) * 1000:.2f}"
    )
    if probe_gaps:
        print(f"probe_responses={len(probe_gaps)}")
        print(
            "event_loop_gap_ms  "
            f"p50={pct(probe_gaps, 0.5) * 1000:.2f}  "
            f"p99={pct(probe_gaps, 0.99) * 1000:.2f}  "
            f"max={max(probe_gaps) * 1000:.2f}"
        )


if __name__ == "__main__":
    main()
