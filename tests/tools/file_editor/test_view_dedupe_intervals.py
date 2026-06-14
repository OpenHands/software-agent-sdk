"""SDK-6 (v2): interval-coverage dedupe for `file_editor view`.

The v1 design (per-path set of response hashes) only caught exact-byte
repeats. Trajectory analysis of run `27293667611` (Nemotron 550B on
SWE-Bench Verified) showed the dominant waste pattern is OVERLAPPING but
non-identical `view_range`s of the same file:

  `sklearn/impute/_iterative.py` viewed 17×, 15 distinct ranges, all
  clustered around three regions:

    cluster A: (110, 130) (110, 135) (110, 185) (115, 122) (115, 132) (115, 135)
    cluster B: (270, 300) (270, 340) (274, 295) (290, 350) (294, 340)
    cluster C: (560, 650) (565, 640) (605, 630)

  v1 hash-based dedupe caught 2/17 hits — only the two exact repeats.
  v2 interval-coverage dedupe is designed to catch ~12/17 on the same
  trajectory by recognizing each subsequent cluster member as ≥70%
  covered by the union of earlier views.

These tests pin both the new overlap-aware behavior and the unit-level
correctness of the interval algebra it relies on. The end-to-end behavior
preserved from v1 (cache invalidation on every edit command, no dedupe on
errors, per-path isolation, directories handled, hint stays small) lives
in `test_view_dedupe.py` and is not duplicated here.
"""

from pathlib import Path

import pytest

from openhands.tools.file_editor.editor import FileEditor


_DEDUPE_MARKER = "[file_editor view dedupe]"


def _view(editor: FileEditor, path: Path, view_range: list[int] | None = None):
    return editor(command="view", path=str(path), view_range=view_range)


# --------------------------------------------------------------------------
# Unit tests for the interval algebra
# --------------------------------------------------------------------------


class TestMergeIntervals:
    """`_merge_intervals` must produce a sorted, disjoint list. Coverage
    accounting relies on the merged invariant, so any bug here cascades."""

    def test_empty(self):
        assert FileEditor._merge_intervals([]) == []

    def test_single(self):
        assert FileEditor._merge_intervals([(5, 10)]) == [(5, 10)]

    def test_disjoint(self):
        assert FileEditor._merge_intervals([(1, 5), (10, 15)]) == [
            (1, 5),
            (10, 15),
        ]

    def test_unsorted_input_is_sorted(self):
        assert FileEditor._merge_intervals([(10, 15), (1, 5)]) == [
            (1, 5),
            (10, 15),
        ]

    def test_overlapping(self):
        assert FileEditor._merge_intervals([(1, 10), (5, 15)]) == [(1, 15)]

    def test_abutting(self):
        # (1, 5) ∪ (6, 10) — abutting on a line index means contiguous span.
        assert FileEditor._merge_intervals([(1, 5), (6, 10)]) == [(1, 10)]

    def test_nested(self):
        assert FileEditor._merge_intervals([(1, 100), (20, 30)]) == [(1, 100)]

    def test_realistic_cluster(self):
        # The cluster A pattern from the Nemotron trace, scrambled.
        merged = FileEditor._merge_intervals(
            [(115, 132), (110, 130), (115, 135), (115, 122), (110, 185), (110, 135)]
        )
        # Everything should collapse into a single interval.
        assert merged == [(110, 185)]


class TestCoverageFraction:
    """`_coverage_fraction` is the dedupe decision input. It must round-trip
    cleanly on standard cases and clamp to [0, 1]."""

    @pytest.mark.parametrize(
        "requested,seen,expected",
        [
            # No prior views → 0.
            ((1, 10), [], 0.0),
            # Identical → 1.
            ((1, 10), [(1, 10)], 1.0),
            # Strict subset → 1.
            ((3, 7), [(1, 10)], 1.0),
            # Strict superset → ratio.
            ((1, 100), [(1, 50)], 0.5),
            # Disjoint → 0.
            ((1, 10), [(20, 30)], 0.0),
            # Partial overlap.
            ((1, 10), [(5, 15)], 6 / 10),  # lines 5..10 overlap → 6 lines / 10
            # Multiple seen intervals.
            ((1, 100), [(1, 30), (70, 100)], 0.61),
        ],
    )
    def test_cases(self, requested, seen, expected):
        got = FileEditor._coverage_fraction(requested, seen)
        assert got == pytest.approx(expected, abs=1e-9)


# --------------------------------------------------------------------------
# End-to-end behaviour: overlap-aware dedupe
# --------------------------------------------------------------------------


def _multiline(n: int) -> str:
    """Predictable n-line file. Big enough that the dedupe hint is clearly
    smaller than the corresponding view payload."""
    return "\n".join(f"line_{i}" for i in range(1, n + 1)) + "\n"


def test_subset_view_range_is_deduped(tmp_path: Path) -> None:
    """The fix that motivates the redesign: viewing [1, 100] then [10, 50]
    must hint, because every line in [10, 50] was already shown by the
    earlier broader view."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(200))

    first = _view(editor, f, view_range=[1, 100])
    second = _view(editor, f, view_range=[10, 50])

    assert _DEDUPE_MARKER not in first.text
    assert _DEDUPE_MARKER in second.text
    # The hint should name the seen range so the model can navigate around it.
    assert "1-100" in second.text


def test_growing_range_within_seen_is_deduped(tmp_path: Path) -> None:
    """Real Nemotron pattern (cluster A): [110, 185] then [115, 135]. The
    second is fully inside the first and must hint."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(300))

    _view(editor, f, view_range=[110, 185])
    second = _view(editor, f, view_range=[115, 135])

    assert _DEDUPE_MARKER in second.text


def test_cluster_pattern_from_real_trace_is_mostly_deduped(tmp_path: Path) -> None:
    """Reproduce one of the actual Nemotron clusters end-to-end.

    Sequence: (270, 300) → (290, 350) → (270, 340) → (274, 295) → (294, 340).

    Expected behavior under 70% threshold:
      1. (270, 300) → real (no prior views).
      2. (290, 350) → real. Overlaps (270, 300) by only 11/61 ≈ 18%,
         and the new lines 301-350 are genuinely new content. After
         this, seen = [(270, 350)].
      3. (270, 340) → hint (100% inside (270, 350)).
      4. (274, 295) → hint (100% inside).
      5. (294, 340) → hint (100% inside).

    v1 (hash-based) caught 0 of these — no exact repeats existed. v2
    catches 3 / 4 follow-ups while correctly letting the genuinely-new
    second view through. The 75% follow-up dedupe rate on this real-trace
    cluster is the headline win that motivates the redesign."""
    editor = FileEditor()
    f = tmp_path / "iterative.py"
    f.write_text(_multiline(700))

    cluster = [(270, 300), (290, 350), (270, 340), (274, 295), (294, 340)]
    results = [_view(editor, f, view_range=list(r)).text for r in cluster]

    pattern = [("hint" if _DEDUPE_MARKER in r else "real") for r in results]
    assert pattern == ["real", "real", "hint", "hint", "hint"], (
        f"Unexpected dedupe pattern for known Nemotron cluster: {pattern}. "
        f"v2 should let the first two through (genuinely new content) and "
        f"dedupe the last three (fully inside the seen union)."
    )


def test_low_overlap_not_deduped(tmp_path: Path) -> None:
    """Truly disjoint follow-up must NOT dedupe — the model is asking for
    genuinely new content."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(500))

    _view(editor, f, view_range=[1, 100])
    second = _view(editor, f, view_range=[200, 300])

    assert _DEDUPE_MARKER not in second.text


def test_below_threshold_overlap_not_deduped(tmp_path: Path) -> None:
    """Partial-but-below-threshold overlap returns fresh content. With
    threshold=0.7 and seen=[1, 100], a request for [80, 200] covers
    21/121 ≈ 17% → must NOT dedupe."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(300))

    _view(editor, f, view_range=[1, 100])
    second = _view(editor, f, view_range=[80, 200])

    assert _DEDUPE_MARKER not in second.text


def test_above_threshold_overlap_is_deduped(tmp_path: Path) -> None:
    """Just-above-threshold overlap returns the hint. seen=[1, 100],
    requested [50, 110] covers 51/61 ≈ 84% → dedupe."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(200))

    _view(editor, f, view_range=[1, 100])
    second = _view(editor, f, view_range=[50, 110])

    assert _DEDUPE_MARKER in second.text


def test_seen_intervals_grow_monotonically(tmp_path: Path) -> None:
    """A sequence of disjoint reads grows the seen-set; once their union
    covers a later request, dedupe fires. Asserts the seen set is being
    accumulated, not replaced."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(500))

    _view(editor, f, view_range=[1, 100])
    _view(editor, f, view_range=[200, 300])
    _view(editor, f, view_range=[400, 500])
    # Now seen ⊇ [1-100] ∪ [200-300] ∪ [400-500]. A request for [50, 250]
    # covers 51 lines in [1, 100] + 51 lines in [200, 300] = 102/201 = ~51%
    # — below threshold, so NOT deduped (the gap 101-199 isn't covered).
    middling = _view(editor, f, view_range=[50, 250])
    assert _DEDUPE_MARKER not in middling.text
    # But a request for [10, 90] is 100% inside [1, 100] → deduped.
    inside = _view(editor, f, view_range=[10, 90])
    assert _DEDUPE_MARKER in inside.text


def test_whole_file_view_then_partial_is_deduped(tmp_path: Path) -> None:
    """If the model has already seen the whole file (no `view_range`),
    any subsequent partial view is 100% covered → hint."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(80))

    _view(editor, f)  # whole file
    partial = _view(editor, f, view_range=[10, 30])

    assert _DEDUPE_MARKER in partial.text


def test_partial_first_then_whole_file_is_not_deduped(tmp_path: Path) -> None:
    """If only [1, 30] has been shown and the model then asks for the whole
    100-line file, lines 31-100 are genuinely new (70% uncovered) so the
    full content must be returned."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(100))

    _view(editor, f, view_range=[1, 30])
    whole = _view(editor, f)

    assert _DEDUPE_MARKER not in whole.text


def test_hint_lists_seen_ranges_in_compact_form(tmp_path: Path) -> None:
    """The hint must be actionable: it has to tell the model WHAT ranges
    have already been shown so it can pick an unseen one."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(300))

    _view(editor, f, view_range=[10, 30])
    _view(editor, f, view_range=[100, 150])
    # Force a dedupe by re-requesting an interval inside [10, 30].
    hinted = _view(editor, f, view_range=[15, 25])

    assert _DEDUPE_MARKER in hinted.text
    # Both prior ranges should appear in some form.
    assert "10-30" in hinted.text
    assert "100-150" in hinted.text
    assert str(f) in hinted.text


def test_edit_invalidates_all_seen_intervals(tmp_path: Path) -> None:
    """Any write must clear the entire interval set for the path. After an
    edit, a previously-deduped range must return fresh content."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(100))

    _view(editor, f, view_range=[1, 50])
    # `line_50` is unique in the generated file (line_5 has no `line_50`
    # substring; line_50 only appears once), so str_replace will accept it.
    editor(
        command="str_replace",
        path=str(f),
        old_str="line_50",
        new_str="line_fifty",
    )
    # Same range as before — must return real content, not hint.
    after = _view(editor, f, view_range=[1, 50])
    assert _DEDUPE_MARKER not in after.text
    assert "line_fifty" in after.text


def test_distinct_files_track_independently(tmp_path: Path) -> None:
    editor = FileEditor()
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text(_multiline(100))
    b.write_text(_multiline(100))

    _view(editor, a, view_range=[1, 100])
    # Viewing b for the first time must NOT dedupe even though a's full
    # range has been seen.
    obs_b = _view(editor, b, view_range=[1, 100])
    assert _DEDUPE_MARKER not in obs_b.text


def test_end_minus_one_is_treated_as_eof(tmp_path: Path) -> None:
    """`view_range=[1, -1]` is the documented way to say "to end of file".
    The interval extractor must normalize it to (1, num_lines) so a later
    request for any sub-range is fully covered."""
    editor = FileEditor()
    f = tmp_path / "f.py"
    f.write_text(_multiline(60))

    _view(editor, f, view_range=[1, -1])
    inside = _view(editor, f, view_range=[20, 40])

    assert _DEDUPE_MARKER in inside.text


def test_hint_is_an_order_of_magnitude_smaller(tmp_path: Path) -> None:
    """Real-world payback check: on a representative 1000-line file the
    hint must be < 10% of the original view it replaces. (The hint grows
    only with path length + range string; the view grows with line count.)"""
    editor = FileEditor()
    f = tmp_path / "big.py"
    f.write_text(_multiline(1000))

    first = _view(editor, f)
    second = _view(editor, f, view_range=[100, 200])

    ratio = len(second.text) / len(first.text)
    assert ratio < 0.10, (
        f"Hint ({len(second.text)} chars) should be < 10% of original "
        f"view ({len(first.text)} chars); got ratio={ratio:.2%}"
    )
