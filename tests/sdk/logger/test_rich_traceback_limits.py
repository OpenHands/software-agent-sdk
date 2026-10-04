"""Run pathological logging in a subprocess so a regression cannot hang pytest."""

import io
import logging
import os
import subprocess
import sys
import textwrap

import pytest
from rich.console import Console

from openhands.sdk.logger._rich_handler import (
    _MAX_EXCEPTIONS,
    _MAX_FRAMES,
    BoundedRichHandler,
    _can_render,
)


@pytest.mark.parametrize("shape", ["context_cycle", "cause_cycle", "deep", "shared"])
def test_default_logging_bounds_exception_graph(shape):
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
            **os.environ,
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


def test_exception_budget_boundary():
    error = RuntimeError("leaf")
    for _ in range(_MAX_EXCEPTIONS - 1):
        outer = RuntimeError("outer")
        outer.__cause__ = error
        error = outer
    assert _can_render(error, None)
    outer = RuntimeError("over budget")
    outer.__cause__ = error
    assert not _can_render(outer, None)


def test_frame_budget_boundary():
    from types import TracebackType

    frame = sys._getframe()
    traceback = None
    for _ in range(_MAX_FRAMES):
        traceback = TracebackType(traceback, frame, frame.f_lasti, frame.f_lineno)
    error = RuntimeError("deep stack")
    assert _can_render(error, traceback)
    traceback = TracebackType(traceback, frame, frame.f_lasti, frame.f_lineno)
    assert not _can_render(error, traceback)


@pytest.mark.parametrize("rich_tracebacks", [True, False])
def test_ordinary_exception_keeps_traceback(rich_tracebacks):
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
    error = RuntimeError("cyclic")
    error.__context__ = error
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
    assert error.__context__ is error


def test_hidden_context_and_small_shared_group_remain_renderable():
    error = RuntimeError("hidden cycle")
    error.__context__ = error
    error.__suppress_context__ = True
    assert _can_render(error, None)
    error.__cause__ = error
    assert _can_render(error, None)
    child = ValueError("shared")
    assert _can_render(ExceptionGroup("small", [child, child]), None)


@pytest.mark.parametrize("mode", ["json", "ci"])
def test_machine_logging_keeps_standard_handler(monkeypatch, mode):
    from openhands.sdk.logger import logger as logger_module

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(logger_module, "ENV_JSON", mode == "json")
    monkeypatch.setattr(logger_module, "IN_CI", mode == "ci")
    logger_module.setup_logging(level=root.level, log_to_file=False)
    assert len(root.handlers) == 1
    assert type(root.handlers[0]) is logging.StreamHandler
