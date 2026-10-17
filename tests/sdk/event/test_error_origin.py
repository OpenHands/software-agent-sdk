"""Origin and grouping-key derivation stays privacy-safe and stable."""

import re
import subprocess
import sys

import openhands.sdk.event.error_origin as error_origin
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.event.error_classification import (
    AGENT_OUTCOME,
    ErrorClassification,
    FailureKind,
)
from openhands.sdk.event.error_origin import (
    grouping_key_for,
    origin_from_exception,
)
from openhands.sdk.event.llm_convertible import AgentErrorEvent


_MESSAGE = "MSGMARKER-9f3a summarize the confidential memo"
_PATH = "/home/alice/private/repo/main.py"
_SECRET = "sk-ant-api03-ORIGINSECRET"
_LOCAL = "LOCALMARKER-9f3a"
_ORIGIN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")


def _define(module: str, source: str) -> dict[str, object]:
    namespace: dict[str, object] = {"__name__": module}
    exec(source, namespace)
    return namespace


def _raise_marked(module: str, function: str) -> BaseException:
    namespace = _define(
        module,
        f"def {function}():\n"
        f"    leaked = {_LOCAL!r}\n"
        f"    raise TypeError({_MESSAGE!r} + ' ' + {_PATH!r} + ' ' + {_SECRET!r})\n",
    )
    raiser = namespace[function]
    assert callable(raiser)
    try:
        raiser()
    except TypeError as exc:
        return exc
    raise AssertionError("expected TypeError")


def _assert_clean(origin: str | None, grouping_key: str | None) -> None:
    assert origin is not None
    assert grouping_key is not None
    assert _ORIGIN_RE.fullmatch(origin)
    assert _KEY_RE.fullmatch(grouping_key)
    blob = f"{origin} {grouping_key}"
    for marker in (_MESSAGE, _PATH, _SECRET, _LOCAL, "MSGMARKER", "LOCALMARKER"):
        assert marker not in blob


def test_origin_excludes_message_path_secret_and_locals() -> None:
    exc = _raise_marked("openhands.sdk._origin_privacy", "boom")
    origin = origin_from_exception(exc)
    event = ConversationErrorEvent(
        source="environment",
        code="TypeError",
        detail=str(exc),
        origin=origin,
    )
    _assert_clean(event.origin, event.grouping_key)
    assert event.origin == "openhands.sdk._origin_privacy.boom"
    assert event.classification is not None
    assert event.classification.kind == FailureKind.INTERNAL


def test_same_function_ignores_line_number_and_repeats() -> None:
    namespace = _define(
        "openhands.sdk._origin_lines",
        "def same(flag):\n"
        "    if flag:\n"
        "        raise TypeError('one')\n"
        "    raise TypeError('two')\n",
    )
    raiser = namespace["same"]
    assert callable(raiser)

    def capture(flag: bool) -> str | None:
        try:
            raiser(flag)
        except TypeError as exc:
            return grouping_key_for(origin_from_exception(exc), "TypeError")
        raise AssertionError("expected TypeError")

    first = capture(True)
    second = capture(False)
    again = capture(True)
    assert first is not None
    assert first == second == again


def test_library_raise_is_attributed_to_the_openhands_caller() -> None:
    inner = _define(
        "vendor.deep.mod",
        "def boom():\n    raise TypeError('library')\n",
    )
    outer = _define(
        "openhands.sdk._origin_caller",
        "def caller():\n    boom()\n",
    )
    outer["boom"] = inner["boom"]
    caller = outer["caller"]
    assert callable(caller)
    try:
        caller()
    except TypeError as exc:
        origin = origin_from_exception(exc)
    else:
        raise AssertionError("expected TypeError")
    assert origin == "openhands.sdk._origin_caller.caller"


def _raise_in(module: str, function: str) -> BaseException:
    namespace = {"__name__": module}
    exec(f"def {function}():\n    raise TypeError('x')\n", namespace)
    raiser = namespace[function]
    assert callable(raiser)
    try:
        raiser()
    except TypeError as exc:
        return exc
    raise AssertionError("expected TypeError")


def test_overlong_origins_are_omitted_instead_of_sharing_a_suffix() -> None:
    function = "run"
    pad = "s" * 80
    module_a = f"openhands.alpha.{pad}"
    module_b = f"openhands.beta.{pad}"
    assert len(module_a) <= 96
    assert len(f"{module_a}.{function}") > 96

    origin_a = origin_from_exception(_raise_in(module_a, function))
    origin_b = origin_from_exception(_raise_in(module_b, function))

    assert origin_a is None
    assert origin_b is None


def test_nameless_frames_do_not_hide_a_later_named_frame(monkeypatch) -> None:
    monkeypatch.setattr(error_origin, "_MAX_YIELDED_FRAMES", 2)
    hidden: dict[str, object] = {}
    visible = {"__name__": "openhands.sdk._budget_visible"}
    exec("def visible():\n    raise TypeError('x')\n", visible)
    exec(
        "def hidden():\n"
        "    try:\n"
        "        visible()\n"
        "    except TypeError as exc:\n"
        "        return exc\n",
        hidden,
    )
    hidden["visible"] = visible["visible"]
    raiser = hidden["hidden"]
    assert callable(raiser)
    exc = raiser()
    assert isinstance(exc, TypeError)
    modules = [
        frame.module for frame in error_origin.iter_traceback_frames(exc.__traceback__)
    ]
    assert "openhands.sdk._budget_visible" in modules


def test_conversation_error_keeps_a_supplied_grouping_key() -> None:
    origin = "openhands.sdk.llm.llm.completion"
    supplied = grouping_key_for(origin, "RuntimeError")
    assert supplied is not None
    event = ConversationErrorEvent(
        source="environment",
        code="TypeError",
        detail="x",
        origin=origin,
        grouping_key=supplied,
    )
    assert event.grouping_key == supplied
    assert event.grouping_key != grouping_key_for(origin, "TypeError")


def test_agent_error_does_not_derive_a_key_from_the_message() -> None:
    event = AgentErrorEvent(
        error="TypeError: secret /tmp/hidden",
        tool_name="bash",
        tool_call_id="call-1",
        origin="openhands.sdk.tool.tool.ToolDefinition.__call__",
    )
    assert event.origin == "openhands.sdk.tool.tool.ToolDefinition.__call__"
    assert event.grouping_key is None
    assert event.classification is not None
    assert event.classification.kind == FailureKind.UNKNOWN


def test_unraised_exception_has_no_origin() -> None:
    assert origin_from_exception(TypeError("constructed")) is None
    event = ConversationErrorEvent(
        source="environment",
        code="TypeError",
        detail="constructed",
        origin=origin_from_exception(TypeError("constructed")),
    )
    assert event.origin is None
    assert event.grouping_key is None


def test_unsafe_origin_is_dropped() -> None:
    event = ConversationErrorEvent(
        source="environment",
        code="TypeError",
        detail="x",
        origin=f"{_SECRET} {_PATH}",
        grouping_key=_SECRET,
    )
    assert event.origin is None
    assert event.grouping_key is None
    assert _SECRET not in event.model_dump_json()
    assert _PATH not in event.model_dump_json()


def test_grouping_key_is_stable_across_processes() -> None:
    origin = "openhands.sdk.llm.llm.completion"
    expected = grouping_key_for(origin, "TypeError")
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from openhands.sdk.event.error_origin import grouping_key_for\n"
            "print(grouping_key_for("
            "'openhands.sdk.llm.llm.completion', 'TypeError'))\n",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == expected


def test_explicit_agent_classification_survives_origin() -> None:
    explicit = ErrorClassification(
        kind=FailureKind.AGENT_ACTION, retryable=True, user_action="retry"
    )
    origin = "openhands.sdk.agent.agent._execute_action_event"
    event = AgentErrorEvent(
        error=f"{_SECRET} {_PATH}",
        tool_name="bash",
        tool_call_id="call-1",
        classification=explicit,
        origin=origin,
        grouping_key=grouping_key_for(origin, "ValueError"),
    )
    assert event.classification is explicit
    assert event.classification == AGENT_OUTCOME
    _assert_clean(event.origin, event.grouping_key)
