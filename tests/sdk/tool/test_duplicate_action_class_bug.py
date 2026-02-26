"""Reproduce issue #2199: duplicate dynamic Action wrapper classes.

When subagent threads concurrently call ``create_action_type_with_risk``
or ``_create_action_type_with_summary`` on the same input, a TOCTOU race
on the module-level cache can create two distinct class objects with the
same ``__name__``, causing ``_get_checked_concrete_subclasses(Action)``
to raise ``ValueError("Duplicate class definition ...")``.

The fix is a ``threading.Lock`` around the check-and-create in both functions.
"""

from __future__ import annotations

import gc
import threading

from pydantic import Field

from openhands.sdk.tool import Action
from openhands.sdk.tool.tool import (
    _action_types_with_risk,
    _action_types_with_summary,
    _create_action_type_with_summary,
    create_action_type_with_risk,
)
from openhands.sdk.utils.models import _get_checked_concrete_subclasses


# Must live at module scope (Pydantic rejects <locals> classes).
class _Bug2199Action(Action):
    cmd: str = Field(description="test")


def test_concurrent_risk_wrapper_no_duplicates(request):
    """Many threads wrapping the same type must all get the same class object."""
    saved_risk = dict(_action_types_with_risk)

    def _cleanup():
        _action_types_with_risk.clear()
        _action_types_with_risk.update(saved_risk)
        gc.collect()

    request.addfinalizer(_cleanup)

    results: list[type] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(create_action_type_with_risk(_Bug2199Action))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(id(r) for r in results)) == 1, "All threads must get the same class"
    _get_checked_concrete_subclasses(Action)


def test_concurrent_summary_wrapper_no_duplicates(request):
    """Same race test for _create_action_type_with_summary."""
    saved_risk = dict(_action_types_with_risk)
    saved_summary = dict(_action_types_with_summary)

    def _cleanup():
        _action_types_with_risk.clear()
        _action_types_with_risk.update(saved_risk)
        _action_types_with_summary.clear()
        _action_types_with_summary.update(saved_summary)
        gc.collect()

    request.addfinalizer(_cleanup)

    with_risk = create_action_type_with_risk(_Bug2199Action)
    results: list[type] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        results.append(_create_action_type_with_summary(with_risk))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(id(r) for r in results)) == 1, "All threads must get the same class"
    _get_checked_concrete_subclasses(Action)
