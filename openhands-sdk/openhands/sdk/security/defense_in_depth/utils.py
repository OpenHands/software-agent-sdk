"""Extraction and normalization for action-boundary security analysis.

Before an agent action can be classified as safe or dangerous, two
things need to happen: the right content must be extracted from the
ActionEvent (extraction), and encoding tricks that hide dangerous
commands must be neutralized (normalization).

Extraction controls the attack surface. Fields not extracted are
invisible to every downstream layer. Two corpora are maintained:
the *executable corpus* (what the agent will do) and the *text corpus*
(what it thought about). Shell-destructive patterns only see the
first; injection patterns see both.

Normalization collapses invisible characters, control codes, and
fullwidth substitutions so that ``r\\u200bm`` matches ``rm`` and
``\\uff52\\uff4d`` matches ``rm`` before any pattern is tested.

These are internal helpers (underscore-prefixed, not re-exported).
"""

from __future__ import annotations

import json
import re
import shlex
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
    field boundaries for segment-aware rail evaluation.

    RecursionError is NOT caught here -- it propagates to
    _extract_exec_segments() which falls back to scanning the raw
    arguments string. Returning [] would silently drop all leaves,
    creating a false-negative path for deeply nested payloads.
    """
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


def _split_shell_segments(command: str) -> list[str]:
    """Split shell text on top-level command operators.

    We split only on real command separators/operators: ``;``, ``&&``, ``||``,
    and ``|``. Splits are ignored inside single/double quotes and for escaped
    characters (``\\;``, ``\\|``). This is intentionally lightweight; it is not
    a full POSIX shell parser.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0

    def _flush() -> None:
        text = "".join(current).strip()
        if text:
            segments.append(text)
        current.clear()

    while i < len(command):
        ch = command[i]

        if escaped:
            current.append(ch)
            escaped = False
            i += 1
            continue

        if ch == "\\":
            current.append(ch)
            escaped = True
            i += 1
            continue

        if not in_double and ch == "'":
            in_single = not in_single
            current.append(ch)
            i += 1
            continue

        if not in_single and ch == '"':
            in_double = not in_double
            current.append(ch)
            i += 1
            continue

        if not in_single and not in_double:
            nxt = command[i + 1] if i + 1 < len(command) else ""
            if ch == ";":
                _flush()
                i += 1
                continue
            if ch == "&" and nxt == "&":
                _flush()
                i += 2
                continue
            if ch == "|" and nxt == "|":
                _flush()
                i += 2
                continue
            if ch == "|":
                _flush()
                i += 1
                continue

        current.append(ch)
        i += 1

    _flush()
    return segments


_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _tokenize_shell_segment(segment: str) -> list[str]:
    """Tokenize one shell segment with a safe fallback."""
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        # Unterminated quote or malformed shell text: preserve best-effort scan.
        return segment.split()


def _is_redirection_token(token: str) -> bool:
    """Return True for common redirection token forms.

    Covers both split forms (``2>``, ``>&``) and compact forms (``2>&1``,
    ``>/tmp/x``). These tokens should not affect destructive flag detection.
    """
    if not token:
        return False
    if re.fullmatch(r"\d*(?:>>?|<<?|<>|>&|<&)", token):
        return True
    if re.fullmatch(r"\d+>&\d+", token):
        return True
    if token.startswith((">", "<")):
        return True
    if token[:1].isdigit() and len(token) > 1 and token[1] in {">", "<"}:
        return True
    return False


def _segment_has_rm_recursive_force(tokens: list[str]) -> bool:
    """Detect ``rm`` with both recursive and force flags in one segment.

    Strategy:
    - command must start with ``rm`` (optionally after env assignments)
    - flags may appear in any order and position after the command
    - positional args and redirections are ignored
    - supports combined short flags (``-rf``, ``-fr``, ``-rfx``), and
      long flags (``--recursive``, ``--force``)
    """
    idx = 0
    while idx < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[idx]):
        idx += 1

    if idx >= len(tokens):
        return False

    command = tokens[idx].lower()
    if command != "rm" and not command.endswith("/rm"):
        return False

    has_recursive = False
    has_force = False

    for token in tokens[idx + 1 :]:
        if token == "--":
            break
        if _is_redirection_token(token):
            continue

        if token.startswith("--"):
            if token == "--recursive":
                has_recursive = True
            elif token == "--force":
                has_force = True
            continue

        if token.startswith("-") and token != "-":
            flags = token[1:]
            if "r" in flags or "R" in flags:
                has_recursive = True
            if "f" in flags:
                has_force = True

    return has_recursive and has_force


def _has_rm_recursive_force(command: str) -> bool:
    """Return True if any top-level segment runs ``rm`` with recursive+force.

    This avoids fixed-width regex windows and scans arbitrarily long segments
    in linear time. It is intentionally conservative and does not attempt a
    full shell grammar (command substitution, alias expansion, functions).
    """
    for segment in _split_shell_segments(command):
        if _segment_has_rm_recursive_force(_tokenize_shell_segment(segment)):
            return True
    return False


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
    # U+2028 (line separator) and U+2029 (paragraph separator) are NOT
    # stripped here -- they are whitespace-like and should be collapsed
    # by the \s+ stage, not deleted. Deleting them merges tokens and
    # can bypass word-boundary regex detectors.
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
    """Collapse encoding evasions so dangerous commands match their patterns.

    An attacker can make ``rm`` not look like ``rm`` to a regex engine
    while still looking like ``rm`` to a shell: zero-width characters,
    fullwidth ASCII, bidi controls, and null bytes all achieve this.
    This function neutralizes those techniques in four stages:

    1. **Null bytes** -- prevent C-level string truncation.
    2. **Invisible characters** -- strip ~200+ chars across zero-width,
       format/control, bidi, variation selectors, tag block, C0, C1.
       (Informed by navi-sanitize, MIT, inlined without dependency.)
    3. **NFKC** -- fullwidth ``\\uff52\\uff4d`` becomes ASCII ``rm``.
    4. **Whitespace collapse** -- NFKC may produce new whitespace.

    Does NOT cover Cyrillic homoglyphs or combining-mark evasion
    (documented as strict xfails, deferred to follow-up).
    """
    # Stage 1: Null bytes
    text = text.replace("\x00", "")

    # Stage 2: Invisible characters (compiled regex)
    text = _INVISIBLE_RE.sub("", text)

    # Stage 3: NFKC normalization
    text = unicodedata.normalize("NFKC", text)

    # Stage 4: Collapse whitespace
    return re.sub(r"\s+", " ", text)
