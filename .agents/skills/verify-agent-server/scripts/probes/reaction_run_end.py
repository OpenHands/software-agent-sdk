"""Reactions that race the end of a run: every success must be saved once.

Run through ``control-agent-server exec`` (it sets AGENT_SERVER_URL and
SESSION_API_KEY), from the repository root:

    python3 .agents/skills/verify-agent-server/scripts/probes/reaction_run_end.py \
        fire <conversation-id> <receipts.json> [--end finish|pause] [--text T]
    python3 .agents/skills/verify-agent-server/scripts/probes/reaction_run_end.py \
        check <conversation-id> <receipts.json>

``fire`` starts a run with a user message and, from four threads, posts the
person's reactions (add and remove, alternating) while the run goes and for
two seconds after it ends: on its own (``finish``) or by ``POST .../pause``
(``pause``; give the agent a task of several tool steps with ``--text``,
since a pause lands between steps). Each 200 response is a receipt: the
saved event's id. It prints how many receipts landed while the conversation
was running and after.

``check`` reads the whole event log and fails unless every receipt is there
exactly once, on the active branch (its parent chain reaches the first
event), and in the order the server returned them per thread. Run it again
after ``control-agent-server restart``.

The probe uses real scheduling, not barriers: ``running_receipts`` of 0
means the race was not exercised; report that as not exercised. The exact
boundary inside ``LocalConversation.execute_tool`` has a deterministic
regression test in ``tests/tools/reaction/test_reaction_tools.py``.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request


BASE = os.environ["AGENT_SERVER_URL"].rstrip("/")
KEY = os.environ.get("SESSION_API_KEY", "")
EMOJI = ["eyes", "+1", "tada", "heart"]


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if KEY:
        request.add_header("X-Session-API-Key", KEY)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def status(cid: str) -> str:
    return call("GET", f"/api/conversations/{cid}")[1].get("execution_status", "")


def all_events(cid: str) -> list[dict]:
    events: list[dict] = []
    page_id = None
    while True:
        query = "limit=100" + (f"&page_id={page_id}" if page_id else "")
        _, body = call("GET", f"/api/conversations/{cid}/events/search?{query}")
        events.extend(body.get("items", []))
        page_id = body.get("next_page_id")
        if not page_id:
            return events


DEFAULT_TEXT = "Count from 1 to 40 in words, one number per line, then stop."


def fire(cid: str, receipts_path: str, end: str, text: str) -> dict:
    code, _ = call(
        "POST",
        f"/api/conversations/{cid}/events",
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"QA_F37_RUN_END {text}",
                }
            ],
            "run": True,
        },
    )
    if code != 200:
        return {"ok": False, "error": f"send answered {code}"}
    target = next(
        e["id"]
        for e in reversed(all_events(cid))
        if e["kind"] == "MessageEvent" and e.get("source") == "user"
    )
    stop = threading.Event()
    lock = threading.Lock()
    receipts: list[dict] = []

    def worker(index: int) -> None:
        emoji = EMOJI[index]
        removed = False
        while not stop.is_set():
            running = status(cid) == "running"
            code, body = call(
                "POST",
                f"/api/conversations/{cid}/reactions",
                {"emoji": emoji, "target_event_id": target, "removed": removed},
            )
            if code == 200:
                with lock:
                    receipts.append(
                        {"thread": index, "id": body["id"], "running": running}
                    )
                removed = not removed

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 240
    if end == "pause":
        while status(cid) != "running" and time.monotonic() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        call("POST", f"/api/conversations/{cid}/pause")
    while status(cid) == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(2.0)
    stop.set()
    for thread in threads:
        thread.join()
    with open(receipts_path, "w") as file:
        json.dump(receipts, file)
    running = sum(1 for r in receipts if r["running"])
    final_status = status(cid)
    # A pause that came after the run had finished did not end it.
    ended_as_asked = final_status == ("paused" if end == "pause" else "finished")
    return {
        "ok": True,
        "end": end,
        "final_status": final_status,
        "receipts": len(receipts),
        "running_receipts": running,
        "exercised": running > 0 and ended_as_asked,
    }


def check(cid: str, receipts_path: str) -> dict:
    with open(receipts_path) as file:
        receipts = json.load(file)
    events = all_events(cid)
    by_id: dict[str, list[dict]] = {}
    for event in events:
        by_id.setdefault(event["id"], []).append(event)
    missing = [r["id"] for r in receipts if r["id"] not in by_id]
    duplicated = [i for i, found in by_id.items() if len(found) > 1]
    # Active branch: follow parent_id from the last event back to the root.
    on_branch: set[str] = set()
    cursor: str | None = events[-1]["id"] if events else None
    while cursor and cursor not in on_branch:
        on_branch.add(cursor)
        cursor = by_id[cursor][0].get("parent_id") if cursor in by_id else None
    off_branch = [r["id"] for r in receipts if r["id"] not in on_branch]
    position = {e["id"]: i for i, e in enumerate(events)}
    out_of_order = []
    for index in {r["thread"] for r in receipts}:
        mine = [position.get(r["id"], -1) for r in receipts if r["thread"] == index]
        if mine != sorted(mine):
            out_of_order.append(index)
    ok = not (missing or duplicated or off_branch or out_of_order)
    return {
        "ok": ok,
        "receipts": len(receipts),
        "missing": missing,
        "duplicated": duplicated,
        "off_branch": off_branch,
        "out_of_order_threads": out_of_order,
    }


def main() -> int:
    mode, cid, receipts_path = sys.argv[1:4]
    end, text = "finish", DEFAULT_TEXT
    if "--end" in sys.argv:
        end = sys.argv[sys.argv.index("--end") + 1]
    if "--text" in sys.argv:
        text = sys.argv[sys.argv.index("--text") + 1]
    result = (
        fire(cid, receipts_path, end, text)
        if mode == "fire"
        else check(cid, receipts_path)
    )
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
