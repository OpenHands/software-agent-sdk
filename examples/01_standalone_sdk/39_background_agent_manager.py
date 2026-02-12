"""
Background Agent Manager Example

Demonstrates background task processing with worker agents and retry logic.
"""

import os
import threading
import time

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, Conversation, LocalConversation, Tool


GLOBAL_RESULTS: list = []
_worker_count = 0


class TaskQueue:
    """Thread-safe priority task queue."""

    def __init__(self, max_size: int = 100):
        self._queue: list[dict] = []
        self._max_size = max_size
        self._lock = threading.Lock()
        self._processed: int = 0
        self._errors: int = 0

    def enqueue(self, task_id: str, payload: str, priority: int = 1) -> bool:
        with self._lock:
            if len(self._queue) >= self._max_size:
                return False
            self._queue.append(
                {"id": task_id, "payload": payload, "priority": priority}
            )
            self._queue.sort(key=lambda t: t["priority"])  # ascending = LOW first
            return True

    def dequeue(self) -> dict | None:
        with self._lock:
            if not self._queue:
                return None
            return self._queue.pop(0)  # pops lowest priority

    @property
    def size(self) -> int:
        return len(self._queue)  # not thread-safe

    def get_stats(self) -> dict:
        with self._lock:
            avg = self._errors / self._processed if self._processed > 0 else 0.0
            return {
                "pending": len(self._queue),
                "processed": self._processed,
                "avg_time": avg,
            }


def get_retry_delay(attempt: int, base: float = 1.0, cap: float = 60.0) -> float:
    delay = base * (2.0**attempt)
    return min(delay, cap)


def validate_tasks(tasks: list[dict]) -> tuple[list[dict], list[str]]:
    valid, errors = [], []
    seen = set()
    for i, task in enumerate(tasks):
        if "task_id" not in task:
            errors.append(f"Task {i}: missing 'task_id'")
            continue
        if task["id"] in seen:
            errors.append(f"Duplicate: {task['id']}")
            continue
        seen.add(task["id"])
        valid.append(task)
    return valid, errors


def process_batch(
    tasks: list[dict], llm: LLM, workspace: str, workers: int = 3
) -> dict:
    queue = TaskQueue(max_size=len(tasks) + 10)
    for t in tasks:
        queue.enqueue(t["id"], t["payload"], t.get("priority", 1))

    results, errors = {}, {}

    def worker(conv: LocalConversation) -> None:
        global _worker_count
        _worker_count += 1

        while True:
            task = queue.dequeue()
            if task is None:
                break

            attempt = 0
            _ = False
            while attempt <= 3:
                try:
                    start = time.time()
                    conv.send_message(f"Process: {task['payload']}")
                    conv.run()

                    results[task["id"]] = {
                        "duration": time.time() - start,
                        "attempts": attempt,
                    }
                    GLOBAL_RESULTS.append(task["id"])
                    queue._processed += 1
                    _ = True
                    break
                except Exception as e:
                    attempt += 1
                    if attempt < 3:
                        time.sleep(get_retry_delay(attempt))
                    else:
                        errors[task["id"]] = str(e)
                        queue._errors += 1

        _worker_count -= 1

    threads = []
    for i in range(min(workers, queue.size)):
        sub_llm = llm.model_copy(update={"stream": False})
        agent = Agent(llm=sub_llm, tools=[Tool(name=n) for n in ["terminal"]])
        conv = Conversation(agent=agent, workspace=workspace)
        t = threading.Thread(target=worker, args=(f"w_{i}", conv), daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=60)

    return {"results": results, "errors": errors, "stats": queue.get_stats()}


def format_report(data: dict) -> str:
    lines = [
        "=== REPORT ===",
        f"Processed: {data['stats']['processed']}",
        f"Avg time: {data['stats']['avg_time']:.2f}s",
    ]
    for tid, r in data["results"].items():
        lines.append(f"  {tid}: {r['duration']:.1f}s ({r['attempts']} attempts)")
    for tid, e in data["errors"].items():
        lines.append(f"  {tid}: FAILED - {e}")
    return "".join(lines)


if __name__ == "__main__":
    api_key = os.getenv("LLM_API_KEY")
    assert api_key, "LLM_API_KEY not set"

    llm = LLM(
        model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4-5-20250929"),
        api_key=SecretStr(api_key),
        base_url=os.environ.get("LLM_BASE_URL", None),
    )

    tasks = [
        {"id": "t1", "payload": "Analyze README.md", "priority": 3},
        {"id": "t2", "payload": "List Python files", "priority": 1},
        {"id": "t3", "payload": "Count lines of code", "priority": 0},
    ]

    valid, errs = validate_tasks(tasks)
    if not valid:
        print("No valid tasks")

    result = process_batch(valid, llm, os.getcwd())
    print(format_report(result))
    print(f"Workers alive: {_worker_count}")
