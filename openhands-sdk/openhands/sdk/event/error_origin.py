"""Sanitized failure origin and stable grouping key for error events.

Conversation and agent error events carry two diagnostic fields in addition
to the user-facing classification in :mod:`openhands.sdk.event.error_classification`:

* ``origin`` — the module and function that raised the failure, or the
  innermost OpenHands frame when the raise site is outside this repo.
* ``grouping_key`` — a process-stable digest of that origin plus the error
  code, so the same site and error type group together.

Privacy and stability rules (this is the only place these fields are derived):

* The exception message is never read. ``str(exc)`` is not used.
* The traceback is never formatted. :func:`iter_traceback_frames` walks
  ``exc.__traceback__`` and reads only ``f_globals["__name__"]``,
  ``f_code.co_qualname``, ``f_code.co_name``, and ``tb_lineno``. It does not
  read ``f_locals`` and does not call :mod:`traceback`, because
  ``traceback.FrameSummary`` loads the source line from disk.
* Line numbers are not part of ``origin`` or ``grouping_key``. Two failures
  in the same function stay together when only the line changes.
* Values that are not dotted identifiers are dropped. That excludes exception
  text, absolute paths, secrets, and other user data. There is no fixed list
  of allowed origins; the grammar below is the vocabulary.
* Identifiers match ``^[A-Za-z_][A-Za-z0-9_]*([.:][A-Za-z_][A-Za-z0-9_]*)*$``
  and are at most 96 characters. A function qualname containing ``<locals>``
  or ``<lambda>`` is rejected; the plain code name is used when that is safe.
  An origin that still does not fit is omitted. It is not shortened to a
  shared suffix: two modules can share that suffix and would then group as
  one failure.
* ``ConversationErrorEvent`` derives ``grouping_key`` from ``origin`` and
  ``code`` when the caller does not supply a valid key. ``AgentErrorEvent``
  does not: its ``error`` string is the message, so the producer passes the
  digest of the exception class name and the model only checks the hex form.
* OpenHands frames keep their full module path. Any other frame keeps only
  its top-level package, so an unfamiliar dependency cannot grow cardinality
  or leak a deep import path.
* The grouping key is blake2s (16-byte digest, 32 hex chars) over
  ``origin|code``. The code is the event's ``code`` for conversation errors
  and the exception class ``__name__`` for agent errors. A code that is not
  an identifier is replaced with ``UnknownError`` before hashing, so prose
  never enters the digest. The digest is unsalted and does not include a
  schema version, so the same origin and code hash identically in every
  process.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from types import TracebackType
from typing import Final


FIRST_PARTY_ROOT: Final[str] = __name__.split(".", 1)[0]
UNKNOWN_ERROR_CLASS: Final[str] = "UnknownError"
_SAFE_IDENTIFIER_MAX_LEN: Final[int] = 96
_SAFE_IDENTIFIER_RE: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*([.:][A-Za-z_][A-Za-z0-9_]*)*$"
)
_GROUPING_KEY_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
_MAX_YIELDED_FRAMES: Final[int] = 256
_MAX_TRACE_LINKS: Final[int] = 2048


def safe_identifier(value: object, *, default: str = UNKNOWN_ERROR_CLASS) -> str:
    """Coerce to a dotted identifier, or ``default`` if it doesn't fit."""
    if not isinstance(value, str):
        return default
    candidate = value.strip()
    if len(candidate) > _SAFE_IDENTIFIER_MAX_LEN:
        return default
    return candidate if _SAFE_IDENTIFIER_RE.match(candidate) else default


def _accepted(value: str) -> str | None:
    if not value or safe_identifier(value, default="") != value:
        return None
    return value


@dataclass(frozen=True, slots=True)
class TraceFrame:
    """One traceback frame reduced to non-identifying names."""

    module: str
    qualname: str | None
    name: str | None
    lineno: int


def iter_traceback_frames(
    tb: TracebackType | None, *, max_links: int = _MAX_TRACE_LINKS
) -> Iterator[TraceFrame]:
    """Walk a traceback without reading messages, locals, or source lines.

    At most 256 frames are yielded. A link with no module name is skipped and
    does not consume that budget, so a nameless frame cannot hide a later
    OpenHands frame. The walk still stops after ``max_links`` links, including
    links that are not yielded. The default cap is 2048, and a larger request
    is clamped to that cap.
    """
    current = tb
    yielded = 0
    links = 0
    link_limit = min(max_links, _MAX_TRACE_LINKS)
    while current is not None and yielded < _MAX_YIELDED_FRAMES and links < link_limit:
        links += 1
        module = current.tb_frame.f_globals.get("__name__")
        if isinstance(module, str) and module:
            code = current.tb_frame.f_code
            yield TraceFrame(
                module=module,
                qualname=code.co_qualname or None,
                name=code.co_name or None,
                lineno=current.tb_lineno,
            )
            yielded += 1
        current = current.tb_next


def _is_first_party(module: str) -> bool:
    return module == FIRST_PARTY_ROOT or module.startswith(f"{FIRST_PARTY_ROOT}.")


def _function_token(frame: TraceFrame) -> str | None:
    for candidate in (frame.qualname, frame.name):
        if candidate is None:
            continue
        accepted = _accepted(candidate.strip())
        if accepted is not None:
            return accepted
    return None


def _join_origin(module: str, function: str | None) -> str | None:
    module_token = _accepted(module.strip())
    if module_token is None:
        return None
    if function is None:
        return module_token
    combined = f"{module_token}.{function}"
    if _accepted(combined) == combined:
        return combined
    return None


def origin_from_exception(exc: BaseException) -> str | None:
    """Return the sanitized module and function for ``exc``, or None.

    Prefers the innermost OpenHands frame so a library raise is attributed to
    the SDK call that reached it. When the traceback never enters OpenHands,
    the innermost frame is kept with its module collapsed to the top-level
    package. Returns None when the exception was never raised.
    """
    frames = list(iter_traceback_frames(exc.__traceback__))
    if not frames:
        return None
    first_party = [frame for frame in frames if _is_first_party(frame.module)]
    if first_party:
        frame = first_party[-1]
        module = frame.module
    else:
        frame = frames[-1]
        module = frame.module.split(".", 1)[0]
    return _join_origin(module, _function_token(frame))


def grouping_key_for(origin: str | None, code: str) -> str | None:
    """Hash a sanitized origin and error code. None when origin is unusable."""
    if _accepted(origin or "") != origin or origin is None:
        return None
    code_token = _accepted(code.strip()) if isinstance(code, str) else None
    if code_token is None:
        code_token = UNKNOWN_ERROR_CLASS
    preimage = f"{origin}|{code_token}"
    return hashlib.blake2s(preimage.encode("utf-8"), digest_size=16).hexdigest()


def validated_origin(value: object) -> str | None:
    """Drop an origin that is not a dotted identifier."""
    if not isinstance(value, str):
        return None
    return _accepted(value.strip())


def validated_grouping_key(value: object) -> str | None:
    """Drop a grouping key that is not a 32-char blake2s hex digest."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if _GROUPING_KEY_RE.fullmatch(candidate):
        return candidate
    return None


@dataclass(frozen=True, slots=True)
class ExceptionDiagnostics:
    """Origin and grouping key for an exception that has no event ``code``."""

    origin: str | None
    grouping_key: str | None


def diagnostics_from_exception(exc: BaseException) -> ExceptionDiagnostics:
    """Diagnostics keyed by the exception class name, not its message."""
    origin = origin_from_exception(exc)
    return ExceptionDiagnostics(
        origin=origin,
        grouping_key=grouping_key_for(origin, type(exc).__name__),
    )
