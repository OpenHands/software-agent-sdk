"""Per-conversation cost budget with pre-call reservation.

A conversation budget must not merely compare "spent so far" before a call:
concurrent callers (parallel delegates, subagents under one budget) all read
the same total and pass, and a single call can still overshoot by its own
worst case. ``CostBudget`` therefore *reserves* each call's worst-case cost
before it is sent and settles it once the call completes, releasing the
remainder when actual cost is known. Failed or unknown-cost attempts retain
their bound; these conservative charges are budget usage, not billed cost.
Settled + reserved is a live upper bound on what the conversation can owe.

The ceiling is the conversation's *accumulated* LLM cost (``base_state.json``
owns it), so a run seeds the ledger with cost already recorded. A parent and its
subagents share one reservation tree so they contend on one running total, but
each ceiling is measured against the usage of the *subtree it governs*: a view's
own ceiling sees only itself and its descendants, while an ancestor ceiling sees
everything below it. A subagent's ``max_budget_per_run`` therefore bounds that
subagent's own work, and a delegate cannot spend past its parent's cap even when
it declares a larger budget.

Pre-call reservation only covers the LLM call wrappers that install a
reservation (``LLM.completion``/``acompletion``/...). Agents that delegate to an
external process -- notably :class:`~openhands.sdk.agent.acp_agent.ACPAgent`,
whose prompts never pass through those wrappers -- do not reserve, so a budget on
such a conversation is rejected rather than accepted without pre-call enforcement.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class _Tree:
    """Shared state for one budget tree: one lock, one seed and one generation."""

    __slots__ = ("lock", "generation", "seed")

    def __init__(self, seed: float = 0.0) -> None:
        self.lock = threading.Lock()
        # Cost already accrued before the ledger's current generation, seeded
        # from the conversation's accumulated cost. A view whose ``_generation``
        # lags this value re-seeds from here on next access, so a fresh run
        # starts from recorded spend without walking the whole tree.
        self.seed = seed
        # Bumped by ``reseed``.
        self.generation = 0


@dataclass(frozen=True)
class BudgetReservation:
    """A reservation of ``amount`` USD held against a :class:`CostBudget`."""

    budget: CostBudget
    amount: float


class CostBudget:
    """A cost ceiling (USD) enforced by pre-call reservation.

    ``limit`` bounds the cost accrued in the current run by this view and its
    descendants (see :meth:`reseed`). Derived child views share the tree's lock
    so reservations stay atomic, but each ceiling is compared against the
    subtree it governs rather than the whole tree.
    """

    def __init__(self, limit: float) -> None:
        if limit <= 0:
            raise ValueError("Cost budget limit must be strictly positive")
        self._limit = limit
        self._tree = _Tree()
        self._parent: CostBudget | None = None
        self._generation = 0
        # Settled/reserved budget usage for this view and its descendants.
        self._subtree_settled = 0.0
        self._subtree_reserved = 0.0

    def child(self, limit: float) -> CostBudget:
        """Derive a subagent view with its own (usually smaller) ceiling.

        Shares the parent's lock so the whole tree contends on one running
        total, and enforces its own limit over its own subtree *and* every
        ancestor ceiling over that ancestor's subtree. A child cannot declare a
        larger budget than its parent and spend past the parent's cap.
        """
        if limit <= 0:
            raise ValueError("Cost budget limit must be strictly positive")
        view = CostBudget.__new__(CostBudget)
        view._limit = limit
        view._tree = self._tree
        view._parent = self
        view._generation = self._tree.generation
        view._subtree_settled = 0.0
        view._subtree_reserved = 0.0
        return view

    def _sync(self) -> None:
        """Reset this view's totals if the tree was reseeded. Lock held."""
        generation = self._tree.generation
        if self._generation != generation:
            self._subtree_settled = 0.0
            self._subtree_reserved = 0.0
            self._generation = generation

    @property
    def limit(self) -> float:
        """Tightest ceiling among this view and its ancestors.

        Reported by denial messages so a caller sees the binding cap. Note that
        each ceiling is *enforced* over its own subtree, so this is a reporting
        summary rather than the value compared in :meth:`try_reserve`.
        """
        tightest = self._limit
        node = self._parent
        while node is not None:
            tightest = min(tightest, node._limit)
            node = node._parent
        return tightest

    def _node_total(self, node: CostBudget) -> float:
        """``node``'s subtree total, plus the tree seed for the root view only.

        The seed is cost the owner accrued before the current generation. Only
        the root's ceiling counts it, so a child's own ceiling is not charged
        for the owner's (or a sibling's) earlier spending. Lock held.
        """
        seed = self._tree.seed if node._parent is None else 0.0
        return seed + node._subtree_settled + node._subtree_reserved

    def _breached_limit(self, amount: float) -> float | None:
        """Tightest ceiling ``amount`` would breach, or ``None`` if it fits.

        Walks this view and its ancestors, so a child view cannot admit a call
        that breaches an ancestor's cap. Lock held.
        """
        node: CostBudget | None = self
        while node is not None:
            node._sync()
            if self._node_total(node) + amount > node._limit:
                return node._limit
            node = node._parent
        return None

    @property
    def spent(self) -> float:
        """Settled budget usage in this subtree, including bounds for unknown work."""
        with self._tree.lock:
            self._sync()
            seed = self._tree.seed if self._parent is None else 0.0
            return max(seed, 0.0) + max(0.0, self._subtree_settled)

    @property
    def reserved(self) -> float:
        with self._tree.lock:
            self._sync()
            return self._subtree_reserved

    @property
    def live_total(self) -> float:
        """Live upper bound on subtree cost: settled + in-flight reservations."""
        with self._tree.lock:
            self._sync()
            return max(self._node_total(self), 0.0)

    def reseed(self, seed: float = 0.0) -> None:
        """Start a fresh allowance for the whole tree from ``seed``.

        Bumps the tree generation so every view resets its subtree totals on
        next access. ``seed`` is the cost already accrued before this allowance
        began (the conversation's accumulated cost), so an accumulated-cost
        ceiling keeps counting prior spend.
        """
        with self._tree.lock:
            self._tree.seed = seed
            self._tree.generation += 1

    def try_reserve(self, amount: float) -> BudgetReservation | None:
        """Atomically reserve ``amount`` or return ``None`` if it would exceed.

        The comparison and the reservation happen under one lock, so parallel
        callers cannot all pass the same check. This view's ceiling and every
        ancestor ceiling are checked over their own subtrees, so a child view
        cannot admit a call that breaches a parent's cap.
        """
        if amount < 0:
            raise ValueError("Reservation amount cannot be negative")
        with self._tree.lock:
            if self._breached_limit(amount) is not None:
                return None
            node: CostBudget | None = self
            while node is not None:
                node._subtree_reserved += amount
                node = node._parent
            return BudgetReservation(self, amount)

    def settle(self, reservation: BudgetReservation, actual: float) -> None:
        """Release a reservation and add its settled budget usage.

        The caller supplies recorded cost or retains a conservative bound for
        failed/unknown work. This tree does not replace billing metrics.
        """
        with self._tree.lock:
            node: CostBudget | None = reservation.budget
            while node is not None:
                node._sync()
                node._subtree_reserved = max(
                    0.0, node._subtree_reserved - reservation.amount
                )
                node._subtree_settled += max(0.0, actual)
                node = node._parent

    def exceeded_detail(self) -> str | None:
        """Error detail if the live total has reached a ceiling, else ``None``."""
        with self._tree.lock:
            self._sync()
            total = self._node_total(self)
            limit = self._reached_limit()
        if limit is None:
            return None
        return self._limit_detail(limit, accumulated=max(total, 0.0))

    def _reached_limit(self) -> float | None:
        """Tightest ceiling this subtree's live total has reached. Lock held."""
        node: CostBudget | None = self
        while node is not None:
            node._sync()
            if self._node_total(node) >= node._limit:
                return node._limit
            node = node._parent
        return None

    def denial_detail(self, amount: float) -> str:
        """Message for a reservation of ``amount`` that would exceed a ceiling."""
        with self._tree.lock:
            self._sync()
            total = self._node_total(self)
            limit = self._breached_limit(amount)
        return self._limit_detail(
            limit if limit is not None else self._limit,
            accumulated=max(total, 0.0),
            attempted=max(total, 0.0) + amount,
        )

    def _limit_detail(
        self, limit: float, *, accumulated: float, attempted: float | None = None
    ) -> str:
        detail = (
            f"Agent reached maximum budget limit (${limit:.4f}); "
            f"budget usage ${accumulated:.4f} (settled and reserved)."
        )
        if attempted is not None:
            detail += f" Next call would need ${attempted:.4f}."
        return detail


__all__ = ["BudgetReservation", "CostBudget"]
