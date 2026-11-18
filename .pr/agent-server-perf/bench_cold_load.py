"""Scenario B: cold-loading a long conversation stalls the whole event loop.

build:   bench_cold_load.py build <steps> <bytes>     -> fixtures/conv-<steps>-<bytes>/
measure: bench_cold_load.py measure <steps> <bytes> <reps> [label]

measure starts a fresh server on a copy of the fixture, probes /alive every
5 ms, then issues one events/search request that forces EventService.start()
(LocalConversation construction -> full event replay). Reports load latency
and the worst /alive latency seen during the load (= loop stall).
"""

import asyncio
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx

from harness import HERE, Stack, log

FIX = HERE / "fixtures"


async def build(steps: int, nbytes: int):
    async with Stack() as st:
        cid = await st.create(f"LOOP {steps} BYTES {nbytes}")
        t0 = time.monotonic()
        s = await st.wait_status(cid, {"finished", "error"}, 3600)
        log("built", cid, s, round(time.monotonic() - t0, 1), "s")
        r = await st.client.get(f"/api/conversations/{cid}/events/count")
        n_events = r.json()
    dst = FIX / f"conv-{steps}-{nbytes}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(st.tmp / "work", dst, symlinks=True)
    (dst / "meta.bench.json").write_text(json.dumps({"id": cid, "events": n_events}))
    print(json.dumps({"built": steps, "events": n_events, "id": cid}))


async def measure_once(steps: int, nbytes: int) -> dict:
    src = FIX / f"conv-{steps}-{nbytes}"
    meta = json.loads((src / "meta.bench.json").read_text())
    tmp = Path(tempfile.mkdtemp(prefix="ohbench-cold-"))
    shutil.copytree(src, tmp / "work", symlinks=True)
    async with Stack(tmp=tmp) as st:
        await asyncio.sleep(1.0)
        samples: list[float] = []
        stop = asyncio.Event()

        async def probe():
            async with httpx.AsyncClient(base_url=st.base) as c:
                while not stop.is_set():
                    t0 = time.perf_counter()
                    await c.get("/alive", timeout=60)
                    samples.append(time.perf_counter() - t0)
                    await asyncio.sleep(0.005)

        pt = asyncio.create_task(probe())
        await asyncio.sleep(0.3)
        base_n = len(samples)
        t0 = time.perf_counter()
        r = await st.client.get(
            f"/api/conversations/{meta['id']}/events/search?limit=1", timeout=300
        )
        load = time.perf_counter() - t0
        r.raise_for_status()
        await asyncio.sleep(0.2)
        stop.set()
        await pt
        during = samples[base_n:]
        # warm request after load, for contrast
        t0 = time.perf_counter()
        await st.client.get(f"/api/conversations/{meta['id']}/events/search?limit=1")
        warm = time.perf_counter() - t0
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "events": meta["events"],
        "cold_load_s": round(load, 3),
        "warm_search_ms": round(warm * 1000, 1),
        "alive_max_ms_during_load": round(max(during) * 1000, 1),
        "alive_p50_ms_during_load": round(statistics.median(during) * 1000, 1),
    }


async def main():
    mode = sys.argv[1]
    steps = int(sys.argv[2])
    if mode == "build":
        await build(steps, int(sys.argv[3]))
        return
    nbytes = int(sys.argv[3])
    reps = int(sys.argv[4])
    label = sys.argv[5] if len(sys.argv) > 5 else "run"
    runs = [await measure_once(steps, nbytes) for _ in range(reps)]
    out = {
        "label": label,
        "steps": steps,
        "events": runs[0]["events"],
        "cold_load_s_median": statistics.median(r["cold_load_s"] for r in runs),
        "alive_max_ms_median": statistics.median(r["alive_max_ms_during_load"] for r in runs),
        "warm_search_ms_median": statistics.median(r["warm_search_ms"] for r in runs),
        "runs": runs,
    }
    print(json.dumps(out))


if __name__ == "__main__":
    asyncio.run(main())
