"""Regex checks used by ``generate_atr_patterns.py``.

Two jobs, both on Python's own ``re`` parse tree:

- ``convert``: bring an ATR regex in line with ``pattern.py``'s constraints
  (no inline case flag, no unbounded wildcard/negated/group span, ``\\b``
  anchoring) or say why it cannot be.
- ``time_patterns``: worst-case search time on adversarial input, as a share
  of a budget calibrated against the patterns ``pattern.py`` already ships.

Input generation is adapted from ATR's ``scripts/gate-redos.py`` (same release,
MIT): quantifiers pumped past their minimum and the tail
broken so the match fails after maximal work, then stretched to
``REDOS_INPUT_CHARS``. Timing uses stdlib ``re`` rather than the ``regex``
module, which optimizes away several of the shapes being hunted, in child
processes that are killed when a search does not return.
"""

from __future__ import annotations

import importlib
import multiprocessing as mp
import os
import queue
import re
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from openhands.sdk.security.defense_in_depth.pattern import (
    DEFAULT_HIGH_PATTERNS,
    DEFAULT_INJECTION_HIGH_PATTERNS,
    DEFAULT_INJECTION_MEDIUM_PATTERNS,
    DEFAULT_MEDIUM_PATTERNS,
)


# The stdlib regex parser; private and without type stubs, so typed as Any.
sre: Any = importlib.import_module("re._constants")
sre_parse: Any = importlib.import_module("re._parser")

REDOS_INPUT_CHARS = 50_000
# Budget = this multiple of the slowest built-in pattern's worst search. 28 was
# picked on one machine (Apple M4, Python 3.13.12) for ATR v4.1.5: the slowest
# kept condition used about 0.57 to 0.84 of the budget across two HIGH-only
# reruns (about 0.7 with the earlier HIGH and MEDIUM set) and the fastest
# condition dropped for time used about 2.4 (1.05 to 1.7 before), so a condition
# near either edge can flip on other hardware. Revisit when regenerating from a newer
# release.
REDOS_NATIVE_FACTOR = 28
REDOS_WALL_SECONDS = 10.0
REDOS_STARTUP_SECONDS = 180.0
REDOS_REPEATS = 3
REDOS_CHUNK = 8
WORKERS = max(1, (os.cpu_count() or 2) - 1)
PUMPS = (2, 3, 4, 8, 16)
GENERIC_FILLERS = ("a", " ", "a ", "-", "=", "*", "{", '"', "<", ">", "\n", "0", "!")
NATIVE_PATTERNS = tuple(
    p
    for p, _, _ in (
        *DEFAULT_HIGH_PATTERNS,
        *DEFAULT_MEDIUM_PATTERNS,
        *DEFAULT_INJECTION_HIGH_PATTERNS,
        *DEFAULT_INJECTION_MEDIUM_PATTERNS,
    )
)

_LEADING_CASE_FLAG = re.compile(r"^\(\?i\)")
_REPEATS = (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT)
_ANCHOR_ATS = (sre.AT_BOUNDARY, sre.AT_BEGINNING, sre.AT_BEGINNING_STRING)
_NEGATED_CATEGORIES = (
    sre.CATEGORY_NOT_WORD,
    sre.CATEGORY_NOT_DIGIT,
    sre.CATEGORY_NOT_SPACE,
)


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def convert(source: str) -> tuple[str | None, str | None]:
    """Return ``(pattern, None)`` or ``(None, reason)`` for one ATR regex."""
    pattern = _LEADING_CASE_FLAG.sub("", source)
    try:
        re.compile(pattern, re.IGNORECASE)
        tree = sre_parse.parse(pattern, re.IGNORECASE)
    except re.error as exc:
        return None, f"does not compile under Python re: {exc}"
    if _unbounded_span(tree):
        return None, "unbounded wildcard, negated-class or group span"
    return _anchored(pattern, _first(list(tree), {"other"})), None


def _anchored(pattern: str, opening: set[str]) -> str:
    """Stop a match from starting inside a word, as pattern.py's ``\\b`` does.

    Only for openings classified on ASCII: ``\\b`` before a CJK character
    would never match inside unspaced text. When some branches open on
    punctuation, ``(?:\\b|(?!\\w))`` is ``\\b`` for word branches and a no-op
    for the others.
    """
    if "word" not in opening or not opening <= {"word", "nonword", "anchored"}:
        return pattern
    if "nonword" in opening:
        return rf"(?:\b|(?!\w))(?:{pattern})"
    return rf"\b(?:{pattern})"


def _char_kind(op: Any, av: Any) -> str:
    if op is sre.LITERAL:
        char = chr(av)
        if char.isascii():
            return "word" if char.isalnum() or char == "_" else "nonword"
        return "other"
    if op is sre.IN:
        kinds = {_class_item_kind(item_op, item_av) for item_op, item_av in av}
        return kinds.pop() if len(kinds) == 1 else "other"
    return "other"


def _class_item_kind(op: Any, av: Any) -> str:
    if op is sre.LITERAL:
        return _char_kind(op, av)
    if op is sre.RANGE:
        kinds = {_char_kind(sre.LITERAL, av[0]), _char_kind(sre.LITERAL, av[1])}
        return kinds.pop() if len(kinds) == 1 else "other"
    if op is sre.CATEGORY and av is sre.CATEGORY_SPACE:
        return "nonword"
    return "other"


def _first(items: list[Any], follow: set[str]) -> set[str]:
    """Kinds of character that can open a match of ``items`` then ``follow``."""
    if not items:
        return follow
    op, av = items[0]
    if op is sre.AT:
        return {"anchored"} if av in _ANCHOR_ATS else _first(items[1:], follow)
    if op in (sre.ASSERT, sre.ASSERT_NOT):
        return _first(items[1:], follow)
    rest = _first(items[1:], follow)
    if op is sre.SUBPATTERN:
        return _first(list(av[-1]), rest)
    if op is sre.ATOMIC_GROUP:
        return _first(list(av), rest)
    if op is sre.BRANCH:
        return set().union(*(_first(list(branch), rest) for branch in av[1]))
    if op in _REPEATS:
        lo, _hi, sub = av
        opening = _first(list(sub), rest)
        return opening | rest if lo == 0 else opening
    return {_char_kind(op, av)}


def _is_narrow_class(sub: Any) -> bool:
    """A single literal or non-negated class: safe to repeat without a bound."""
    items = list(sub)
    if len(items) != 1:
        return False
    op, av = items[0]
    if op is sre.LITERAL:
        return True
    if op is not sre.IN:
        return False
    return all(
        item_op in (sre.LITERAL, sre.RANGE)
        or (item_op is sre.CATEGORY and item_av not in _NEGATED_CATEGORIES)
        for item_op, item_av in av
    )


def _children(op: Any, av: Any) -> list[Any]:
    if op is sre.SUBPATTERN:
        return [av[-1]]
    if op is sre.BRANCH:
        return list(av[1])
    if op in (sre.ASSERT, sre.ASSERT_NOT):
        return [av[1]]
    if op is sre.ATOMIC_GROUP:
        return [av]
    if op in _REPEATS:
        return [av[2]]
    return []


def _unbounded_span(tree: Any) -> bool:
    """True if any unbounded repeat covers more than one narrow class."""
    for op, av in tree:
        if op in _REPEATS and av[1] == sre.MAXREPEAT and not _is_narrow_class(av[2]):
            return True
        if any(_unbounded_span(child) for child in _children(op, av)):
            return True
    return False


# ---------------------------------------------------------------------------
# Adversarial inputs
# ---------------------------------------------------------------------------


def _member(items: Any) -> str:
    for op, av in items:
        if op is sre.LITERAL:
            return chr(av)
        if op is sre.RANGE:
            return chr(av[0])
        if op is sre.CATEGORY:
            return {sre.CATEGORY_DIGIT: "5", sre.CATEGORY_SPACE: " "}.get(av, "a")
    return "a"


def _negated_pick(items: Any) -> str:
    """A character the negated class admits that its neighbours may also use."""
    excluded = {chr(av) for op, av in items if op is sre.LITERAL}
    for candidate in "{}\"'<>[]()|:;,.= \t-*!":
        if candidate not in excluded:
            return candidate
    return "a"


def _pump(tree: Any, k: int, choice: int = 0, depth: int = 0) -> str:
    """A string the pattern almost matches, with quantifiers pumped to ``k``.

    Every alternation takes branch ``choice`` (modulo its width), so iterating
    ``choice`` up to the widest alternation pumps each branch at least once.
    """
    if depth > 12:
        return ""
    out: list[str] = []
    for op, av in tree:
        if op is sre.LITERAL:
            out.append(chr(av))
        elif op is sre.NOT_LITERAL:
            out.append("a" if chr(av) != "a" else "b")
        elif op is sre.ANY:
            out.append("a")
        elif op is sre.IN:
            negated = bool(av) and av[0][0] is sre.NEGATE
            out.append(_negated_pick(av[1:]) if negated else _member(av))
        elif op in _REPEATS:
            lo, hi, sub = av
            reps = max(lo, k) if hi == sre.MAXREPEAT else min(max(lo, k), hi)
            out.append(_pump(sub, k, choice, depth + 1) * min(reps, 40))
        elif op is sre.BRANCH and av[1]:
            branch = av[1][choice % len(av[1])]
            out.append(_pump(branch, k, choice, depth + 1))
        elif op is sre.SUBPATTERN:
            out.append(_pump(av[-1], k, choice, depth + 1))
        elif op is sre.ATOMIC_GROUP:
            out.append(_pump(av, k, choice, depth + 1))
    return "".join(out)


def _widest(tree: Any) -> int:
    """The largest number of alternatives in any alternation of ``tree``."""
    widths = [1]
    for op, av in tree:
        if op is sre.BRANCH:
            widths.append(len(av[1]))
        widths += [_widest(child) for child in _children(op, av)]
    return max(widths)


def _stretched(base: str, fillers: str) -> Iterator[str]:
    """``base`` alone, repeated to size, and half of it padded with filler."""
    size = REDOS_INPUT_CHARS
    yield base[:-1] + "\x00"
    yield (base[:-1] * (size // len(base) + 1))[:size] + "\x00"
    head = base[: max(1, len(base) // 2)]
    for filler in fillers:
        yield head + filler * (size - len(head)) + "\x00"


def adversarial_inputs(pattern: str) -> Iterator[str]:
    """Long homogeneous runs, then pumped near-misses of every alternative.

    Inputs are raw: whitespace runs, line breaks and NUL survive, which is
    stricter than the analyzer, whose normalization collapses them.
    """
    for filler in GENERIC_FILLERS:
        yield (filler * REDOS_INPUT_CHARS)[:REDOS_INPUT_CHARS]
    tree = sre_parse.parse(pattern, re.IGNORECASE)
    for choice in range(_widest(tree)):
        for k in PUMPS if choice == 0 else PUMPS[-2:]:
            base = _pump(tree, k, choice)
            if base:
                yield from _stretched(base, " a.-" if choice == 0 else " ")


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def _seconds(compiled: re.Pattern[str], text: str) -> float:
    started = time.perf_counter()
    compiled.search(text)
    return time.perf_counter() - started


def _reference() -> tuple[re.Pattern[str], str]:
    """The built-in pattern and adversarial input that are slowest together."""
    slowest = (0.0, re.compile(""), "")
    for pattern in NATIVE_PATTERNS:
        compiled = re.compile(pattern, re.IGNORECASE)
        for text in adversarial_inputs(pattern):
            elapsed = min(_seconds(compiled, text) for _ in range(REDOS_REPEATS))
            if elapsed > slowest[0]:
                slowest = (elapsed, compiled, text)
    return slowest[1], slowest[2]


def _share(
    compiled: re.Pattern[str], text: str, reference: tuple[re.Pattern[str], str]
) -> float:
    """Search time over budget, as the median of back-to-back pairs.

    Timing the reference right next to the target keeps both on the same core
    and clock, which matters on hybrid CPUs; the median drops a pair split by
    a migration.
    """
    shares = sorted(
        _seconds(compiled, text) / (REDOS_NATIVE_FACTOR * _seconds(*reference))
        for _ in range(REDOS_REPEATS)
    )
    return shares[len(shares) // 2]


def _worst_share(pattern: str, reference: tuple[re.Pattern[str], str]) -> float:
    compiled = re.compile(pattern, re.IGNORECASE)
    worst = 0.0
    for text in adversarial_inputs(pattern):
        worst = max(worst, _share(compiled, text, reference))
        if worst > 1:
            break
    return worst


def _time_chunk(items: list[tuple[str, str]], out: Any) -> None:
    reference = _reference()
    out.put(("", 0.0))  # start-up and calibration done
    for key, pattern in items:
        out.put((key, _worst_share(pattern, reference)))


def _run_chunk(chunk: list[tuple[str, str]]) -> dict[str, float]:
    results: dict[str, float] = {}
    out: Any = mp.Queue()
    proc = mp.Process(target=_time_chunk, args=(chunk, out))
    proc.start()
    ready = False
    last_message = time.monotonic()
    while len(results) < len(chunk):
        try:
            key, value = out.get(timeout=0.2)
        except queue.Empty:
            limit = REDOS_WALL_SECONDS if ready else REDOS_STARTUP_SECONDS
            if time.monotonic() - last_message > limit or not proc.is_alive():
                break
            continue
        ready = True
        last_message = time.monotonic()
        if key:
            results[key] = value
    proc.terminate()
    proc.join()
    return results


def time_patterns(items: list[tuple[str, str]]) -> dict[str, float]:
    """Worst search time per key as a budget share; ``inf`` if it hung.

    A child times its chunk in order, so the first key without a result is
    the one that hung; it is recorded and the rest of the chunk re-queued.
    """
    results: dict[str, float] = {}
    pending = [items[i : i + REDOS_CHUNK] for i in range(0, len(items), REDOS_CHUNK)]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        while pending:
            batch, pending = pending, []
            for chunk, got in zip(batch, pool.map(_run_chunk, batch)):
                results.update(got)
                missing = [item for item in chunk if item[0] not in results]
                if missing:
                    results[missing[0][0]] = float("inf")
                    pending += [missing[1:]] if missing[1:] else []
    return results
