"""Per-conversation cost budget with pre-call reservation.

A conversation budget must not merely compare "spent so far" before a call:
concurrent callers (parallel delegates, subagents under one budget) all read
the same total and pass, and a single call can still overshoot by its own
worst case. ``CostBudget`` therefore *reserves* each call's worst-case cost
before it is sent and settles it once the call completes, releasing the
remainder. Reserved + settled is a live upper bound on what the conversation
can owe, which also answers "how much has this conversation accumulated so far"
while it is still running.

Settled cost is carried on a shared ledger, so a parent and its subagents
contend on one ceiling even though a subagent's metrics join the parent only
after it finishes. The ledger is seeded once per run from the conversation's
combined metrics, which bounds spend across every LLM in the conversation --
agent, condenser, fallback, and already-synced subagents -- without
double-counting across runs.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass


class _Ledger:
    """Shared settled/reserved totals for one budget tree, guarded by one lock."""

    __slots__ = ("lock", "settled", "reserved")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.settled = 0.0
        self.reserved = 0.0


@dataclass(frozen=True)
class BudgetReservation:
    """A reservation of ``amount`` USD held against a :class:`CostBudget`."""

    budget: CostBudget
    amount: float


class CostBudget:
    """A cost ceiling (USD) enforced by pre-call reservation.

    ``limit`` bounds the combined cost of the conversation (and, for a shared
    tree, of its subagents). The ledger is shared by derived child views, each
    of which honours its own ``limit`` over the same running total.
    """

    def __init__(self, limit: float, cost_source: Callable[[], float]) -> None:
        if limit <= 0:
            raise ValueError("Cost budget limit must be strictly positive")
        self._limit = limit
        self._cost_source = cost_source
        self._ledger = _Ledger()

    def child(self, limit: float, cost_source: Callable[[], float]) -> CostBudget:
        """Derive a subagent view with its own (usually smaller) ceiling.

        Shares the parent's ledger, so the whole tree contends on one running
        total, but reads the recorded cost source from the child conversation
        and enforces the child's own limit as well.
        """
        if limit <= 0:
            raise ValueError("Cost budget limit must be strictly positive")
        view = CostBudget.__new__(CostBudget)
        view._limit = limit
        view._cost_source = cost_source
        view._ledger = self._ledger
        return view

    @property
    def limit(self) -> float:
        return self._limit

    @property
    def spent(self) -> float:
        """Cost settled so far (excludes in-flight work), floored at 0."""
        with self._ledger.lock:
            return max(0.0, self._ledger.settled)

    @property
    def reserved(self) -> float:
        with self._ledger.lock:
            return self._ledger.reserved

    @property
    def live_total(self) -> float:
        """Live upper bound on cost: settled + in-flight reservations."""
        with self._ledger.lock:
            return self._ledger.settled + self._ledger.reserved

    def start_run(self) -> None:
        """Seed the ledger from recorded cost so each run gets a fresh allowance.

        Called at the start of a top-level run; a run that inherits a parent's
        budget shares the ledger and must not re-seed it mid-flight.
        """
        with self._ledger.lock:
            self._ledger.settled = self._cost_source()
            self._ledger.reserved = 0.0

    def try_reserve(self, amount: float) -> BudgetReservation | None:
        """Atomically reserve ``amount`` or return ``None`` if it would exceed.

        The comparison and the reservation happen under one lock, so parallel
        callers cannot all pass the same check.
        """
        if amount < 0:
            raise ValueError("Reservation amount cannot be negative")
        with self._ledger.lock:
            if self._ledger.settled + self._ledger.reserved + amount > self._limit:
                return None
            self._ledger.reserved += amount
            return BudgetReservation(self, amount)

    def settle(self, reservation: BudgetReservation, actual: float) -> None:
        """Release a reservation and add the call's actual recorded cost."""
        with self._ledger.lock:
            self._ledger.reserved = max(0.0, self._ledger.reserved - reservation.amount)
            self._ledger.settled += max(0.0, actual)

    def exceeded_detail(self) -> str | None:
        """Error detail if the live total has reached the ceiling, else ``None``."""
        if self.live_total < self._limit:
            return None
        return self.denial_detail(0.0)

    def denial_detail(self, amount: float) -> str:
        """Message for a reservation of ``amount`` that would exceed the ceiling."""
        return self._limit_detail(attempted=self.live_total + amount)

    def _limit_detail(self, attempted: float | None = None) -> str:
        # Report the larger of recorded cost and the live bound so the message
        # reflects in-flight spend when it is the reason for the denial.
        accumulated = max(self.spent, self.live_total)
        detail = (
            f"Agent reached maximum budget limit (${self._limit:.4f}); "
            f"accumulated cost ${accumulated:.4f}."
        )
        if attempted is not None:
            detail += f" Next call would need ${attempted:.4f}."
        return detail


__all__ = ["BudgetReservation", "CostBudget"]
