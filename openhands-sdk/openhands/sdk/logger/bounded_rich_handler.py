"""Keep pathological exception chains out of Rich's synchronous renderer."""

import copy
import logging

from rich.logging import RichHandler


_MAX_EXCEPTION_CHAIN = 32


def _can_render_exception(error: BaseException) -> bool:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen or len(seen) >= _MAX_EXCEPTION_CHAIN:
            return False
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            if (
                len(current.exceptions) + len(pending) + len(seen)
                > _MAX_EXCEPTION_CHAIN
            ):
                return False
            pending.extend(current.exceptions)
        cause = current.__cause__
        if cause is not None and cause is not current:
            pending.append(cause)
        elif current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)
    return True


class BoundedRichHandler(RichHandler):
    """Render ordinary exceptions, but omit cyclic or oversized exception graphs."""

    def emit(self, record: logging.LogRecord) -> None:
        if self.rich_tracebacks and record.exc_info and record.exc_info[1] is not None:
            if not _can_render_exception(record.exc_info[1]):
                record = copy.copy(record)
                record.msg = (
                    record.getMessage()
                    + " [traceback omitted: cyclic or oversized exception chain]"
                )
                record.args = ()
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None
        super().emit(record)
