"""Scenario A: FIFOLock waiters vs. the shared default executor.

Conversation A runs a step with 2 sequential `terminal` tool calls (`sleep S`).
While the first tool runs (A's arun holds the state FIFOLock on the loop
thread), K WebSocket clients connect to A. Each connect snapshots state in the
default executor and blocks on the FIFOLock. Meanwhile a probe hits a *different*
idle conversation B's event search (lock-free, but also uses the default
executor) and /alive (pure loop).

Reports: did A finish, how long, probe latencies on B and /alive.

usage: bench_lock_starvation.py <cpu_count> <K> [sleep_s] [label]
"""

import asyncio
import json
import statistics
import sys
import time

import httpx
import websockets

from harness import Stack, log


async def probe(stack: Stack, path: str, stop: asyncio.Event, out: list, tag: str):
    async with httpx.AsyncClient(base_url=stack.base) as c:
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                r = await c.get(path, timeout=5)
                ok = r.status_code == 200
            except Exception:
                ok = False
            dt = time.perf_counter() - t0
            out.append((time.monotonic(), dt, ok))
            await asyncio.sleep(0.05)


async def ws_client(stack: Stack, cid: str, opened: list, stop: asyncio.Event):
    t0 = time.perf_counter()
    try:
        async with websockets.connect(
            f"{stack.ws_base}/sockets/events/{cid}", open_timeout=60
        ) as ws:
            # First frame = initial state snapshot (needs the FIFOLock).
            await asyncio.wait_for(ws.recv(), timeout=60)
            opened.append(time.perf_counter() - t0)
            while not stop.is_set():
                with contextlib_suppress():
                    await asyncio.wait_for(ws.recv(), timeout=0.5)
    except Exception as e:  # noqa: BLE001
        opened.append(("err", type(e).__name__))


class contextlib_suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is not None and issubclass(exc[0], (asyncio.TimeoutError, TimeoutError))


def summarize(samples, t_from, t_to):
    w = [dt for (t, dt, ok) in samples if t_from <= t <= t_to]
    fails = sum(1 for (t, dt, ok) in samples if t_from <= t <= t_to and not ok)
    if not w:
        return {"n": 0}
    return {
        "n": len(w),
        "p50_ms": round(statistics.median(w) * 1000, 1),
        "max_ms": round(max(w) * 1000, 1),
        "fails": fails,
    }


async def main():
    cpu = int(sys.argv[1])
    K = int(sys.argv[2])
    sleep_s = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    label = sys.argv[4] if len(sys.argv) > 4 else "run"
    ntools = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    async with Stack(cpu_count=cpu) as st:
        b = await st.create("SLEEP 0")
        assert await st.wait_status(b, {"finished"}, 60), "B did not finish"
        a = await st.create(f"SLEEP {sleep_s} x{ntools}")
        assert await st.wait_status(a, {"running"}, 30), "A did not start"
        await asyncio.sleep(1.0)  # first tool is now running; A holds the lock

        stop = asyncio.Event()
        b_samples: list = []
        alive_samples: list = []
        probes = [
            asyncio.create_task(probe(st, f"/api/conversations/{b}/events/search?limit=5", stop, b_samples, "B")),
            asyncio.create_task(probe(st, "/alive", stop, alive_samples, "alive")),
        ]
        t_start = time.monotonic()
        opened: list = []
        clients = [asyncio.create_task(ws_client(st, a, opened, stop)) for _ in range(K)]

        timeout = sleep_s * 2 + 25
        done = await st.wait_status(a, {"finished", "error"}, timeout)
        t_end = time.monotonic()
        a_secs = round(t_end - t_start, 2)
        recheck = None
        if not done:
            await asyncio.sleep(60)
            recheck = await st.status(a)
        # let ws clients get their snapshots if unblocked
        await asyncio.sleep(1.0)
        stop.set()
        for t in clients + probes:
            t.cancel()
        await asyncio.gather(*clients, *probes, return_exceptions=True)

        ok_open = [x for x in opened if isinstance(x, float)]
        result = {
            "label": label,
            "cpu_count": cpu,
            "default_executor_workers": min(32, cpu + 4),
            "K_ws_clients": K,
            "tool_sleep_s": sleep_s,
            "A_final_status": done or "STUCK (timeout)",
            "A_secs_from_storm": a_secs if done else f">{timeout}",
            "A_status_60s_later": recheck,
            "tools_in_step": ntools,
            "ws_snapshots_received": len(ok_open),
            "ws_snapshot_p50_s": round(statistics.median(ok_open), 2) if ok_open else None,
            "probe_B_event_search": summarize(b_samples, t_start, t_end),
            "probe_alive": summarize(alive_samples, t_start, t_end),
        }
        print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
