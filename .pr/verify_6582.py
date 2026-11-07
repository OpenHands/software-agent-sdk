"""Deterministic, dependency-free reproduction of OpenHands#6582.

Mirrors the three production pieces involved in the race, transcribed from
upstream main d685c34666:

* ``ConversationState._stamp_parent_id`` (state.py:331) -- an event with no
  parent is stamped with the *current* active leaf.
* ``ConversationState.append_event`` (state.py:342) -- stamps, appends and
  advances HEAD under the state lock.
* ``LocalConversation._arun`` -- releases the state lock during LLM I/O while
  ``_arun_task`` stays alive; ``navigate_to`` can take the lock in that window.

Threads + Events make the interleaving deterministic instead of timing-based.
This is a source-level reproduction, not the repository's pytest suite.

Run:  python verify_ohsdk_6582.py
"""

import sys
import threading


class ConversationRunInProgressError(RuntimeError):
    pass


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        # Turn 1 finished; Turn 2's user message is HEAD.
        self.leaf = "user2"
        self.events = [
            ("user1", None),
            ("agent1", "user1"),
            ("user2", "agent1"),
        ]
        self.arun_task_alive = False

    def _stamp_parent_id(self, event: tuple[str, str | None]) -> tuple[str, str | None]:
        if event[1] is not None:
            return event
        return (event[0], self.leaf)

    def append_event(self, event: tuple[str, str | None]) -> tuple[str, str | None]:
        stamped = self._stamp_parent_id(event)
        self.events.append(stamped)
        self.leaf = stamped[0]
        return stamped


def navigate_before(state: State, event_id: str) -> None:
    """Upstream: no guard, HEAD moves while the run is in flight."""
    with state.lock:
        state.leaf = event_id


def navigate_after(state: State, event_id: str) -> None:
    """Patched: reject re-rooting while _arun_task is alive."""
    with state.lock:
        if state.arun_task_alive:
            raise ConversationRunInProgressError(
                "Cannot navigate while the conversation is running"
            )
        state.leaf = event_id


def turn2(
    state: State,
    started: threading.Event,
    proceed: threading.Event,
    out: dict,
) -> None:
    with state.lock:
        state.arun_task_alive = True
    # _arun releases the state lock here while the LLM completion is pending.
    started.set()
    assert proceed.wait(5), "navigation never released the LLM barrier"
    with state.lock:
        out["agent2"] = state.append_event(("agent2", None))
    state.arun_task_alive = False


def scenario(navigate):
    state = State()
    started, proceed = threading.Event(), threading.Event()
    out: dict = {}
    nav_error: list[str] = []

    t = threading.Thread(target=turn2, args=(state, started, proceed, out))
    t.start()
    assert started.wait(5), "Turn 2 never started"

    try:
        navigate(state, "user1")
    except ConversationRunInProgressError as exc:
        nav_error.append(str(exc))

    proceed.set()
    t.join(5)
    assert not t.is_alive(), "Turn 2 did not finish"
    return state, out["agent2"], nav_error


def main() -> int:
    checks = 0
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str) -> None:
        nonlocal checks
        checks += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: {detail}")
        if not ok:
            failures.append(label)

    print("=== BEFORE (upstream): concurrent navigation during Turn 2 ===")
    state_before, agent2_before, err_before = scenario(navigate_before)
    print(f"  leaf after navigate : {state_before.leaf}")
    print(f"  agent2 parent_id    : {agent2_before[1]}")
    check(
        "bug reproduced: agent2 attached to Turn 1",
        agent2_before[1] == "user1",
        f"parent={agent2_before[1]} (expected user1 for the broken case)",
    )
    check("navigation was silently accepted", not err_before, f"errors={err_before}")

    print("\n=== AFTER (patched): concurrent navigation during Turn 2 ===")
    state_after, agent2_after, err_after = scenario(navigate_after)
    print(f"  leaf after navigate : {state_after.leaf}")
    print(f"  agent2 parent_id    : {agent2_after[1]}")
    print(f"  navigation error    : {err_after}")
    check(
        "navigation rejected while run is active",
        len(err_after) == 1 and "running" in err_after[0],
        f"errors={err_after}",
    )
    check(
        "agent2 keeps the correct Turn 2 parent",
        agent2_after[1] == "user2",
        f"parent={agent2_after[1]} (expected user2)",
    )
    check(
        "HEAD still advanced to the new agent event",
        state_after.leaf == "agent2",
        f"leaf={state_after.leaf}",
    )

    print("\n=== CONTROL: navigation outside a run still works ===")
    idle = State()
    try:
        navigate_after(idle, "user1")
        ok = idle.leaf == "user1"
        detail = f"leaf={idle.leaf}"
    except ConversationRunInProgressError as exc:
        ok, detail = False, f"unexpected rejection: {exc}"
    check("idle navigation not regressed", ok, detail)

    print("\n=== MUTATION: drop the guard -> regression must come back ===")
    state_mut, agent2_mut, err_mut = scenario(navigate_before)
    check("reverted guard again corrupts lineage", agent2_mut[1] == "user1", f"parent={agent2_mut[1]}")
    check("reverted guard again accepts navigation", not err_mut, f"errors={err_mut}")

    print(f"\n{checks - len(failures)}/{checks} checks passed")
    if failures:
        print("FAILED:", ", ".join(failures))
        return 1
    print("RESULT: race reproduced, fix restores lineage, mutation is caught")
    return 0


if __name__ == "__main__":
    sys.exit(main())
