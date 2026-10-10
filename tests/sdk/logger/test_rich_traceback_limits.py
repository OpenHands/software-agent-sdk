"""Run pathological logging in a subprocess so a regression cannot hang pytest."""

import copy
import io
import json
import logging
import os
import subprocess
import sys
import textwrap
from types import TracebackType

import pytest
from rich.console import Console
from rich.logging import RichHandler

from openhands.sdk.logger._rich_handler import (
    _MAX_EXCEPTION_VISITS,
    _MAX_FRAMES,
    BoundedRichHandler,
    _can_render,
)


@pytest.mark.parametrize("shape", ["context_cycle", "cause_cycle", "deep", "shared"])
def test_default_logging_bounds_exception_graph(shape, tmp_path):
    script = textwrap.dedent("""
        import logging
        import sys
        from openhands.sdk.logger import setup_logging

        setup_logging()
        error = RuntimeError("outer")
        shape = sys.argv[1]
        if shape == "context_cycle":
            error.__context__ = error
        elif shape == "cause_cycle":
            other = ValueError("inner")
            error.__cause__ = other
            other.__cause__ = error
        elif shape == "deep":
            for _ in range(2000):
                outer = RuntimeError("outer")
                outer.__cause__ = error
                error = outer
        else:
            for _ in range(20):
                error = ExceptionGroup("shared", [error, error])
        logging.getLogger("regression").error(
            "subscription failed", exc_info=(type(error), error, None)
        )
        print("logging returned")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, shape],
        env={
            **{
                key: os.environ[key]
                for key in ("PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP", "TMPDIR")
                if key in os.environ
            },
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "COLUMNS": "120",
            "DEBUG": "false",
            "LOG_AUTO_CONFIG": "true",
            "LOG_RICH_TRACEBACKS": "true",
            "LOG_JSON": "false",
            "LOG_TO_FILE": "false",
            "LOG_LEVEL": "INFO",
            "DEBUG_LLM": "false",
            "CI": "false",
            "GITHUB_ACTIONS": "",
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "logging returned" in result.stdout
    assert "subscription failed" in result.stderr
    assert "traceback omitted" in result.stderr
    assert len(result.stderr) < 4096


def test_preflight_covers_context_used_by_standard_formatter():
    formatted = []

    class TrackedError(Exception):
        def __str__(self):
            formatted.append(True)
            return "context was formatted"

    context = TrackedError()
    for _ in range(_MAX_EXCEPTION_VISITS):
        outer = RuntimeError("context chain")
        outer.__cause__ = context
        context = outer
    shared_cause = ValueError("shared cause")
    error = RuntimeError("outer")
    error.__cause__ = shared_cause
    error.__context__ = context
    error.__suppress_context__ = False
    group = ExceptionGroup("root", [error, shared_cause])
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=120), rich_tracebacks=True
    )
    handler.emit(
        logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "formatter probe",
            (),
            (type(group), group, None),
        )
    )
    assert not formatted, "The standard formatter traversed an unchecked context"
    assert "traceback omitted" in output.getvalue()


def test_exception_budget_boundary():
    error = RuntimeError("leaf")
    for _ in range(_MAX_EXCEPTION_VISITS - 1):
        outer = RuntimeError("outer")
        outer.__cause__ = error
        error = outer
    assert _can_render(error, None)
    outer = RuntimeError("over budget")
    outer.__cause__ = error
    assert not _can_render(outer, None)


def _frames(count):
    frame = sys._getframe()
    traceback = None
    for _ in range(count):
        traceback = TracebackType(traceback, frame, frame.f_lasti, frame.f_lineno)
    return traceback


@pytest.mark.parametrize("split_across_causes", [False, True])
def test_frame_budget_boundary_and_handler_omission(split_across_causes):
    inner_count = _MAX_FRAMES // 2 if split_across_causes else 0
    inner = ValueError("inner").with_traceback(_frames(inner_count))
    outer = RuntimeError("outer").with_traceback(_frames(_MAX_FRAMES - inner_count))
    outer.__cause__ = inner
    assert _can_render(outer, outer.__traceback__)
    frame = sys._getframe()
    outer.__traceback__ = TracebackType(
        outer.__traceback__, frame, frame.f_lasti, frame.f_lineno
    )
    assert not _can_render(outer, outer.__traceback__)
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=120), rich_tracebacks=True
    )
    handler.emit(
        logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "aggregate frames",
            (),
            (type(outer), outer, outer.__traceback__),
        )
    )
    assert "aggregate frames" in output.getvalue()
    assert "RuntimeError: traceback omitted" in output.getvalue()


@pytest.mark.parametrize("rich_tracebacks", [True, False])
def test_ordinary_exception_keeps_traceback(rich_tracebacks, monkeypatch):
    if not rich_tracebacks:
        monkeypatch.setattr(
            "openhands.sdk.logger._rich_handler._can_render",
            lambda *_: pytest.fail("Disabled Rich tracebacks must bypass preflight"),
        )
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=120), rich_tracebacks=rich_tracebacks
    )
    try:
        raise ValueError("ordinary failure")
    except ValueError as error:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed %s",
            ("request",),
            (type(error), error, error.__traceback__),
        )
    handler.emit(record)
    rendered = output.getvalue()
    assert "failed request" in rendered
    assert "ValueError: ordinary failure" in rendered
    assert "Traceback" in rendered
    assert "traceback omitted" not in rendered


def test_omission_does_not_modify_shared_record_or_exception():
    child = ValueError("child")
    error = ExceptionGroup("oversized", [child] * _MAX_EXCEPTION_VISITS)
    error.__cause__ = child
    error.__context__ = child
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "failed %s",
        ("request",),
        (type(error), error, None),
        sinfo="cached stack",
    )
    record.exc_text = "cached traceback"
    before = record.__dict__.copy()
    output = io.StringIO()
    handler = BoundedRichHandler(
        console=Console(file=output, width=120), rich_tracebacks=True
    )
    handler.emit(record)
    assert "failed request" in output.getvalue()
    assert "traceback omitted" in output.getvalue()
    assert "cached" not in output.getvalue()
    assert record.__dict__ == before
    assert error.exceptions == (child,) * _MAX_EXCEPTION_VISITS
    assert error.__cause__ is child
    assert error.__context__ is child


def test_hidden_context_and_small_shared_group_remain_renderable():
    error = RuntimeError("hidden cycle")
    error.__context__ = error
    error.__suppress_context__ = True
    assert _can_render(error, None)
    error.__cause__ = error
    assert _can_render(error, None)
    child = ValueError("shared")
    assert _can_render(ExceptionGroup("small", [child, child]), None)


@pytest.mark.parametrize(
    "shape", ["raised", "cause", "context", "suppressed", "group", "shared"]
)
def test_normal_output_matches_unmodified_rich(shape):
    inner = ValueError("inner details")
    outer = RuntimeError("outer details")
    if shape == "raised":
        try:
            try:
                raise inner
            except ValueError as cause:
                raise outer from cause
        except RuntimeError:
            pass
        assert outer.__traceback__ is not None
        assert inner.__traceback__ is not None
    elif shape == "cause":
        outer.__cause__ = inner
    elif shape == "context":
        outer.__context__ = inner
    elif shape == "suppressed":
        outer.__context__ = inner
        outer.__suppress_context__ = True
    elif shape == "group":
        outer = ExceptionGroup("group", [outer, inner])
    else:
        outer = ExceptionGroup("shared", [inner, inner])
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "failed %s",
        ("request",),
        (type(outer), outer, outer.__traceback__),
    )
    outputs = []
    for handler_type in (RichHandler, BoundedRichHandler):
        output = io.StringIO()
        handler = handler_type(
            console=Console(file=output, width=120),
            rich_tracebacks=True,
            show_time=False,
            show_path=False,
        )
        handler.emit(copy.copy(record))
        outputs.append(output.getvalue())
    assert outputs[0] == outputs[1]
    assert "traceback omitted" not in outputs[1]


@pytest.mark.parametrize("mode", ["json", "ci"])
def test_machine_logging_keeps_standard_handler(monkeypatch, mode):
    from openhands.sdk.logger import logger as logger_module

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(logger_module, "ENV_JSON", mode == "json")
    monkeypatch.setattr(logger_module, "IN_CI", mode == "ci")
    logger_module.setup_logging(level=root.level, log_to_file=False)
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    output = io.StringIO()
    handler.setStream(output)
    error = ValueError("ordinary failure")
    handler.emit(
        logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed %s",
            ("request",),
            (type(error), error, None),
        )
    )
    rendered = json.loads(output.getvalue())
    assert rendered["message"] == "failed request"
    assert "ValueError: ordinary failure" in rendered["exc_info"]
