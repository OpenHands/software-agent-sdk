"""Regression tests for synchronous Rich traceback rendering bounds."""

import io
import logging

import pytest
from rich.console import Console

from openhands.sdk.logger.bounded_rich_handler import BoundedRichHandler


def make_record(error):
    return logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "subscription failed",
        (),
        (type(error), error, error.__traceback__),
    )


@pytest.mark.parametrize(
    "shape", ["self_context", "context_cycle", "cause_cycle", "deep", "group"]
)
def test_pathological_chains_are_omitted_without_mutating_record(shape):
    error = RuntimeError("original")
    if shape == "self_context":
        error.__context__ = error
    elif shape == "context_cycle":
        other = ValueError("other")
        error.__context__ = other
        other.__context__ = error
    elif shape == "cause_cycle":
        other = ValueError("other")
        error.__cause__ = other
        other.__cause__ = error
    elif shape == "deep":
        for _ in range(40):
            outer = RuntimeError("outer")
            outer.__context__ = error
            error = outer
    else:
        error = ExceptionGroup("many", [ValueError("item") for _ in range(40)])
    record = make_record(error)
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=200), rich_tracebacks=True
    )
    handler.emit(record)
    assert "traceback omitted" in output.getvalue()
    assert record.exc_info[1] is error
    assert record.msg == "subscription failed"
    assert record.exc_text is None


def test_ordinary_exception_keeps_rich_traceback():
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=200), rich_tracebacks=True
    )
    try:
        raise ValueError("ordinary error")
    except ValueError as error:
        handler.emit(make_record(error))
    assert "ordinary error" in output.getvalue()
    assert "Traceback" in output.getvalue()
    assert "traceback omitted" not in output.getvalue()


def test_non_rich_mode_is_unchanged():
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=200), rich_tracebacks=False
    )
    handler.emit(make_record(ValueError("ordinary error")))
    assert "ordinary error" in output.getvalue()
    assert "traceback omitted" not in output.getvalue()


def test_cycle_logging_finishes_in_budgeted_subprocess():
    import inspect
    import subprocess
    import sys

    script = """
import importlib.util
import io
import logging
import sys
from rich.console import Console
spec = importlib.util.spec_from_file_location("bounded", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
error = RuntimeError("cycle")
error.__context__ = error
record = logging.LogRecord("test", logging.ERROR, "test.py", 1, "failed", (),
                           (type(error), error, None))
output = io.StringIO()
handler = module.BoundedRichHandler(console=Console(file=output), rich_tracebacks=True)
handler.emit(record)
assert "traceback omitted" in output.getvalue()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, inspect.getfile(BoundedRichHandler)],
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 0, result.stderr
