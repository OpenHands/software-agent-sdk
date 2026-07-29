"""Tests for subclass hierarchy caching.

The generation-counter cache in models.py auto-invalidates via
DiscriminatedUnionMixin.__init_subclass__.  These tests verify that the
cache is correct in scenarios that could easily break:
  - basic cache hits
  - auto-invalidation on new subclass definition (including deep hierarchy)
  - auto-invalidation from dynamic type() calls (what tool.py does)
  - _get_checked_concrete_subclasses stays in sync with concrete cache
  - concurrent subclass definition from multiple threads
"""

import threading
from abc import ABC

from openhands.sdk.utils.models import (
    DiscriminatedUnionMixin,
    _get_checked_concrete_subclasses,
    get_known_concrete_subclasses,
)


class _Base(DiscriminatedUnionMixin, ABC):
    pass


class _ConcreteA(_Base):
    x: int = 1


class _ConcreteB(_Base):
    x: int = 2


# Separate hierarchy for _get_checked_concrete_subclasses tests
# (which rejects <locals> classes).
class _CheckedBase(DiscriminatedUnionMixin, ABC):
    pass


class _CheckedA(_CheckedBase):
    x: int = 1


def test_cache_hit():
    """Consecutive calls return the exact same tuple object."""
    first = get_known_concrete_subclasses(_Base)
    second = get_known_concrete_subclasses(_Base)
    assert first is second


def test_returns_tuple():
    """Cached result is a tuple (immutable)."""
    assert isinstance(get_known_concrete_subclasses(_Base), tuple)


def test_auto_invalidates_on_new_subclass():
    """Defining a new direct subclass invalidates the parent's cache."""
    first = get_known_concrete_subclasses(_Base)

    class _ConcreteNew(_Base):
        x: int = 99

    second = get_known_concrete_subclasses(_Base)
    assert first is not second
    assert _ConcreteNew in second


def test_deep_hierarchy_invalidation():
    """A subclass of a subclass still invalidates the root ancestor's cache."""

    class _Mid(_Base, ABC):
        pass

    class _Leaf(_Mid):
        x: int = 42

    result = get_known_concrete_subclasses(_Base)
    assert _Leaf in result

    # Now add a deeper leaf — the _Base cache must see it.
    class _Leaf2(_Mid):
        x: int = 43

    result2 = get_known_concrete_subclasses(_Base)
    assert result2 is not result
    assert _Leaf2 in result2


def test_dynamic_type_invalidates_cache():
    """type() call (what tool.py uses) triggers __init_subclass__."""
    before = get_known_concrete_subclasses(_Base)

    DynClass = type("_DynSubclass", (_Base,), {"__annotations__": {"x": int}})

    after = get_known_concrete_subclasses(_Base)
    assert after is not before
    assert DynClass in after


def test_checked_cache_stays_in_sync():
    """_get_checked_concrete_subclasses invalidates alongside the concrete cache."""
    checked_before = _get_checked_concrete_subclasses(_CheckedBase)
    assert "_CheckedA" in checked_before

    # Dynamically add a module-level subclass so qualname has no <locals>.
    cls = type("_CheckedB", (_CheckedBase,), {"__annotations__": {"x": int}})
    cls.__module__ = __name__
    cls.__qualname__ = "_CheckedB"

    checked_after = _get_checked_concrete_subclasses(_CheckedBase)
    assert checked_after is not checked_before
    assert "_CheckedB" in checked_after


def test_concurrent_subclass_creation():
    """Multiple threads defining subclasses — cache is correct after all finish."""

    class _ThreadBase(_Base, ABC):
        pass

    barrier = threading.Barrier(8)
    created: list[type] = []
    lock = threading.Lock()

    def worker(idx: int) -> None:
        barrier.wait()
        cls = type(
            f"_Thread{idx}",
            (_ThreadBase,),
            {"__annotations__": {"x": int}, "x": idx},
        )
        with lock:
            created.append(cls)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    result = get_known_concrete_subclasses(_ThreadBase)
    for cls in created:
        assert cls in result, f"{cls.__name__} missing from cache result"
