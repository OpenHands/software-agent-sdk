import threading

import pytest

from openhands.sdk.llm.cost_budget import BudgetReservation, CostBudget


class _Costs:
    """A mutable stand-in for a conversation's recorded-cost source."""

    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_reserve_denies_when_it_would_exceed_limit():
    budget = CostBudget(1.0, _Costs())

    assert budget.try_reserve(0.4) is not None
    assert budget.try_reserve(0.4) is not None
    # 0.4 + 0.4 + 0.4 > 1.0
    assert budget.try_reserve(0.4) is None
    assert budget.reserved == pytest.approx(0.8)


def test_settle_records_actual_cost_and_frees_capacity():
    budget = CostBudget(1.0, _Costs())

    reservation = budget.try_reserve(0.5)
    assert reservation is not None
    assert budget.reserved == pytest.approx(0.5)

    # The provider charged less than the worst-case reservation.
    budget.settle(reservation, 0.2)
    assert budget.reserved == 0.0
    assert budget.spent == pytest.approx(0.2)
    assert budget.live_total == pytest.approx(0.2)

    # Freed capacity is usable again.
    assert budget.try_reserve(0.7) is not None


def test_live_total_includes_in_flight_reservations():
    budget = CostBudget(10.0, _Costs())
    budget.start_run()
    seed = budget.try_reserve(0.0)
    assert seed is not None
    budget.settle(seed, 3.0)
    reservation = budget.try_reserve(2.0)
    assert reservation is not None
    assert budget.live_total == pytest.approx(5.0)


def test_start_run_seeds_spend_from_recorded_cost():
    costs = _Costs(4.0)
    budget = CostBudget(10.0, costs)
    budget.start_run()
    assert budget.spent == pytest.approx(4.0)
    # 4.0 recorded + 7.0 attempted > 10.0
    assert budget.try_reserve(7.0) is None
    assert budget.try_reserve(6.0) is not None


def test_child_shares_reservation_pool_but_bounds_its_own_spend():
    parent_costs = _Costs()
    child_costs = _Costs()
    parent = CostBudget(10.0, parent_costs)
    parent.start_run()
    child = parent.child(1.0, child_costs)

    # Child spend draws on the shared pool.
    reservation = child.try_reserve(0.9)
    assert reservation is not None
    child_costs.value = 0.9
    child.settle(reservation, 0.9)
    assert parent.reserved == pytest.approx(0.0)
    assert parent.live_total == pytest.approx(0.9)

    # The child's own tighter ceiling now refuses more even though the parent
    # has plenty of capacity left.
    assert child.try_reserve(0.5) is None
    assert parent.try_reserve(0.5) is not None


def test_concurrent_reservations_cannot_overshoot():
    """A shared budget admits at most the calls that fit, even in parallel."""
    budget = CostBudget(1.0, _Costs())
    barrier = threading.Barrier(8)
    granted: list[BudgetReservation] = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        reservation = budget.try_reserve(0.25)
        if reservation is not None:
            with lock:
                granted.append(reservation)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 8 x 0.25 = 2.0 against a 1.0 ceiling: exactly four should be admitted.
    assert len(granted) == 4
    assert budget.reserved == pytest.approx(1.0)


def test_denial_detail_names_the_limit_and_attempted_call():
    budget = CostBudget(1.0, _Costs())
    budget.start_run()
    seed = budget.try_reserve(0.0)
    assert seed is not None
    budget.settle(seed, 0.9)
    detail = budget.denial_detail(0.5)
    assert "$1.0000" in detail
    assert "$0.9000" in detail
    # 0.9 recorded + 0.5 attempted.
    assert "$1.4000" in detail


def test_non_positive_limit_is_rejected():
    with pytest.raises(ValueError):
        CostBudget(0.0, _Costs())
    with pytest.raises(ValueError):
        CostBudget(1.0, _Costs()).child(0.0, _Costs())
