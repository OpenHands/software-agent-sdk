"""Keep exception graph traversal bounded before invoking Rich's renderer."""

import copy
import logging
from types import TracebackType

from rich.logging import RichHandler


_MAX_EXCEPTIONS = 32
_MAX_FRAMES = 256


def _can_render(error: BaseException, traceback: TracebackType | None) -> bool:
    pending = [(error, traceback)]
    exceptions = 0
    frames = 0
    while pending:
        error, traceback = pending.pop()
        exceptions += 1
        if exceptions > _MAX_EXCEPTIONS:
            return False
        while traceback is not None:
            frames += 1
            if frames > _MAX_FRAMES:
                return False
            traceback = traceback.tb_next
        if isinstance(error, BaseExceptionGroup):
            if len(error.exceptions) + len(pending) + exceptions > _MAX_EXCEPTIONS:
                return False
            pending.extend((child, child.__traceback__) for child in error.exceptions)
        cause = error.__cause__
        if cause is not None and cause is not error:
            pending.append((cause, cause.__traceback__))
        elif not error.__suppress_context__ and error.__context__ is not None:
            context = error.__context__
            pending.append((context, context.__traceback__))
    return True


class BoundedRichHandler(RichHandler):
    def emit(self, record: logging.LogRecord) -> None:
        if self.rich_tracebacks and record.exc_info:
            _, error, traceback = record.exc_info
            if error is not None and not _can_render(error, traceback):
                # Other handlers must still receive the original exception record.
                record = copy.copy(record)
                record.msg = (
                    f"{record.getMessage()} "
                    f"[{type(error).__name__}: traceback omitted; "
                    "exception graph exceeds rendering limits]"
                )
                record.args = ()
                record.exc_info = None
                record.exc_text = None
                record.stack_info = None
        super().emit(record)
