"""Extraction and normalization utilities for defense-in-depth security analysis.

Extraction determines what gets scanned (the attack surface). Normalization
collapses encoding evasions before pattern matching.

These are internal helpers -- underscore-prefixed, not re-exported from
__init__.py. Tests import them via direct module path.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from openhands.sdk.event import ActionEvent
from openhands.sdk.logger import get_logger


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum characters extracted from an ActionEvent before normalization and
# pattern matching. Bounds regex runtime and memory, but content beyond this
# limit is invisible to the analyzer.
_EXTRACT_HARD_CAP = 30_000


# ---------------------------------------------------------------------------
# Extraction: whitelisted fields only
# ---------------------------------------------------------------------------


def _walk_json_strings(obj: Any) -> list[str]:
    """Recursively collect leaf strings from a parsed JSON structure.

    Walking to leaves and returning each as a separate segment preserves
    field boundaries for segment-aware rail evaluation. Catches
    RecursionError to defend against JSON bomb payloads.
    """
    try:
        if isinstance(obj, str):
            return [obj]
        if isinstance(obj, dict):
            parts: list[str] = []
            for v in obj.values():
                parts.extend(_walk_json_strings(v))
            return parts
        if isinstance(obj, list):
            parts = []
            for item in obj:
                parts.extend(_walk_json_strings(item))
            return parts
    except RecursionError:
        return []
    return []


def _extract_exec_segments(action: ActionEvent) -> list[str]:
    """Extract segments from fields that describe what the agent will *do*.

    Only executable fields: tool_name, tool_call.name, tool_call.arguments
    (JSON leaf strings). Shell/permission/exec patterns and policy rails
    scan this corpus exclusively.
    """
    segments: list[str] = []
    total = 0

    def _add(text: str) -> None:
        nonlocal total
        remaining = _EXTRACT_HARD_CAP - total
        if remaining <= 0:
            return
        if len(text) > remaining:
            text = text[:remaining]
        segments.append(text)
        total += len(text)

    if action.tool_name:
        _add(action.tool_name)

    if action.tool_call:
        if action.tool_call.name:
            _add(action.tool_call.name)
        if action.tool_call.arguments:
            try:
                parsed = json.loads(action.tool_call.arguments)
                for leaf in _walk_json_strings(parsed):
                    _add(leaf)
            except (json.JSONDecodeError, TypeError, RecursionError):
                _add(action.tool_call.arguments)

    return segments


def _extract_text_segments(action: ActionEvent) -> list[str]:
    """Extract segments from fields that describe what the agent *thought*.

    Thought, reasoning_content, and summary are only scanned for injection
    and social-engineering patterns, never for shell-destructive patterns.
    """
    segments: list[str] = []
    total = 0

    def _add(text: str) -> None:
        nonlocal total
        remaining = _EXTRACT_HARD_CAP - total
        if remaining <= 0:
            return
        if len(text) > remaining:
            text = text[:remaining]
        segments.append(text)
        total += len(text)

    for t in action.thought:
        if t.text:
            _add(t.text)

    if action.reasoning_content:
        _add(action.reasoning_content)

    if action.summary:
        _add(action.summary)

    return segments


def _extract_segments(action: ActionEvent) -> list[str]:
    """Extract all segments (executable + reasoning) from an ActionEvent."""
    return _extract_exec_segments(action) + _extract_text_segments(action)


def _extract_content(action: ActionEvent) -> str:
    """Flat string from all fields -- the all-field scanning surface."""
    return " ".join(_extract_segments(action))[:_EXTRACT_HARD_CAP]


def _extract_exec_content(action: ActionEvent) -> str:
    """Flat string from executable fields only -- the shell-pattern surface."""
    return " ".join(_extract_exec_segments(action))[:_EXTRACT_HARD_CAP]


# ---------------------------------------------------------------------------
# Invisible character definitions
#
# Expanded from the original 14-codepoint set to cover ~200+ invisible
# characters across 9 categories. Informed by navi-sanitize (_invisible.py,
# MIT, Project-Navi/navi-sanitize) -- logic inlined, no dependency.
#
# Same defensive category as the original zero-width stripping, just more
# complete. Compiled into a single regex for performance.
# ---------------------------------------------------------------------------

# Zero-width characters
_ZERO_WIDTH: set[str] = {
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\u2060",  # word joiner
    "\ufeff",  # BOM / zero-width no-break space
    "\u180e",  # Mongolian vowel separator
}

# Format and control characters (invisible or near-invisible)
_FORMAT_CHARS: set[str] = {
    "\u00ad",  # soft hyphen
    "\u034f",  # combining grapheme joiner
    "\u2009",  # thin space
    "\u200a",  # hair space
    "\u2028",  # line separator
    "\u2029",  # paragraph separator
    "\ufff9",  # interlinear annotation anchor
    "\ufffa",  # interlinear annotation separator
    "\ufffb",  # interlinear annotation terminator
    "\ufffc",  # object replacement character
    "\u2061",  # function application (invisible)
    "\u2062",  # invisible times
    "\u2063",  # invisible separator
    "\u2064",  # invisible plus
    "\u206a",  # inhibit symmetric swapping (deprecated)
    "\u206b",  # activate symmetric swapping (deprecated)
    "\u206c",  # inhibit Arabic form shaping (deprecated)
    "\u206d",  # activate Arabic form shaping (deprecated)
    "\u206e",  # national digit shapes (deprecated)
    "\u206f",  # nominal digit shapes (deprecated)
    "\u2800",  # braille pattern blank
    "\u1680",  # Ogham space mark
    "\u115f",  # Hangul Choseong filler
    "\u1160",  # Hangul Jungseong filler
    "\u3164",  # Hangul filler
    "\uffa0",  # Halfwidth Hangul filler
    "\u061c",  # Arabic letter mark
}

# Bidirectional override/isolate characters
_BIDI_CHARS: set[str] = {
    "\u202a",  # LRE
    "\u202b",  # RLE
    "\u202c",  # PDF
    "\u202d",  # LRO
    "\u202e",  # RLO
    "\u2066",  # LRI
    "\u2067",  # RLI
    "\u2068",  # FSI
    "\u2069",  # PDI
}

# Mongolian Free Variation Selectors
_MONGOLIAN_FVS: set[str] = {
    "\u180b",
    "\u180c",
    "\u180d",
    "\u180f",
}

# Ranges compiled into regex character classes
_VARIATION_SELECTOR_RANGE = (0xFE00, 0xFE0F)  # VS1-VS16
_VARIATION_SELECTOR_SUPP_RANGE = (0xE0100, 0xE01EF)  # VS17-VS256
_TAG_BLOCK_RANGE = (0xE0000, 0xE007F)  # Unicode Tag block
_C0_RANGES = [(0x0001, 0x0008), (0x000B, 0x000C), (0x000E, 0x001F)]
_DEL = "\x7f"  # DEL character -- not in C0 or C1 but equally invisible
_C1_RANGE = (0x0080, 0x009F)

# Build single compiled regex for all invisible characters
_INVISIBLE_PATTERN = (
    # Individual char sets
    "["
    + "".join(sorted(_ZERO_WIDTH))
    + "]"
    + "|["
    + "".join(sorted(_FORMAT_CHARS))
    + "]"
    + "|["
    + "".join(sorted(_BIDI_CHARS))
    + "]"
    + "|["
    + "".join(sorted(_MONGOLIAN_FVS))
    + "]"
    # Ranges
    + "|["
    + chr(_VARIATION_SELECTOR_RANGE[0])
    + "-"
    + chr(_VARIATION_SELECTOR_RANGE[1])
    + "]"
    + "|["
    + chr(_TAG_BLOCK_RANGE[0])
    + "-"
    + chr(_TAG_BLOCK_RANGE[1])
    + "]"
    + "|["
    + chr(_VARIATION_SELECTOR_SUPP_RANGE[0])
    + "-"
    + chr(_VARIATION_SELECTOR_SUPP_RANGE[1])
    + "]"
    # C0 controls (excl NUL/TAB/LF/CR)
    + "|["
    + chr(_C0_RANGES[0][0])
    + "-"
    + chr(_C0_RANGES[0][1])
    + "]"
    + "|["
    + chr(_C0_RANGES[1][0])
    + "-"
    + chr(_C0_RANGES[1][1])
    + "]"
    + "|["
    + chr(_C0_RANGES[2][0])
    + "-"
    + chr(_C0_RANGES[2][1])
    + "]"
    # C1 controls
    + "|["
    + chr(_C1_RANGE[0])
    + "-"
    + chr(_C1_RANGE[1])
    + "]"
    # DEL
    + "|"
    + re.escape(_DEL)
)

_INVISIBLE_RE: re.Pattern[str] = re.compile(_INVISIBLE_PATTERN)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Normalize text so encoding evasions collapse before pattern matching.

    Four stages in order:
    1. Strip null bytes (prevent C-level string truncation)
    2. Strip invisible characters (compiled regex, ~200+ chars across
       zero-width, format/control, bidi, variation selectors, tag block,
       C0, C1 -- informed by navi-sanitize, inlined without dependency)
    3. NFKC normalization (fullwidth -> ASCII, ligatures decomposed)
    4. Collapse whitespace (NFKC may produce new whitespace)

    What this does NOT cover (documented limitations, strict xfails):
    - Cyrillic homoglyphs (needs curated confusable map -- deferred)
    - Combining marks (needs diacritic stripping -- lossy for non-Latin)
    """
    # Stage 1: Null bytes
    text = text.replace("\x00", "")

    # Stage 2: Invisible characters (compiled regex)
    text = _INVISIBLE_RE.sub("", text)

    # Stage 3: NFKC normalization
    text = unicodedata.normalize("NFKC", text)

    # Stage 4: Collapse whitespace
    return re.sub(r"\s+", " ", text)
