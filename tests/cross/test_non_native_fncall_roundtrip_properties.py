"""Property-based round trip of the non-native function-calling path.

For models without native tool calling, the agent shows each past tool call
to the model as ``<function=...><parameter=...>`` text, and parses the
model's reply back into a tool call. These tests check the round trip the
agent actually runs: a valid action is rendered in the exact format the
system prompt teaches, parsed back, repaired, and validated. The result
should be the same action.

Past calls matter as much as the new reply: ``post_response_prompt_mock``
parses the whole history again on every turn, so a past call that does not
survive the round trip (for example one made by a native tool-calling model
before ``switch_llm``) raises on every later turn.
"""

import json
from typing import Any

import pytest
from hypothesis import example, given, strategies as st
from litellm import ChatCompletionToolParam
from pydantic import BaseModel, ValidationError

from openhands.sdk.agent.utils import (
    fix_malformed_tool_arguments,
    normalize_tool_call,
    parse_tool_call_arguments,
)
from openhands.sdk.llm.mixins.fn_call_converter import (
    convert_fncall_messages_to_non_fncall_messages,
    convert_non_fncall_messages_to_fncall_messages,
)
from openhands.tools.delegate.definition import DelegateAction
from openhands.tools.file_editor.definition import FileEditorAction
from openhands.tools.task_tracker.definition import TaskTrackerAction
from openhands.tools.terminal.definition import TerminalAction


ACTIONS: dict[str, type[BaseModel]] = {
    "terminal": TerminalAction,
    "file_editor": FileEditorAction,
    "delegate": DelegateAction,
    "task_tracker": TaskTrackerAction,
}

TOOLS: list[ChatCompletionToolParam] = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": action.to_mcp_schema(),  # type: ignore[attr-defined]
        },
    }
    for name, action in ACTIONS.items()
]

# Any text a model could emit, minus lone surrogates (not valid UTF-8). Random
# characters almost never spell the format's own tags, so half the pieces are
# drawn from the tokens the converter's regexes look for.
_TOKENS = [
    "<function=",
    "</function>",
    "<parameter=",
    "</parameter>",
    "<",
    ">",
    "=",
    "/",
    "\n",
    " ",
    "None",
    "true",
    "[]",
    "{}",
    '"',
    "EXECUTION RESULT of [",
    "]:\n",
]
text = st.lists(
    st.sampled_from(_TOKENS) | st.characters(exclude_categories=("Cs",)),
    max_size=12,
).map("".join)
# The same text without leading or trailing whitespace. The converter strips
# every parameter value (pinned in #6348), so this strategy keeps that known
# bug from hiding the others.
trimmed_text = text.map(str.strip)


def _arguments(s: st.SearchStrategy[str]) -> st.SearchStrategy[tuple[str, dict]]:
    terminal = st.fixed_dictionaries(
        {"command": s},
        optional={
            "is_input": st.booleans(),
            "reset": st.booleans(),
            "timeout": st.floats(min_value=0, max_value=1e6),
        },
    )
    file_editor = st.fixed_dictionaries(
        {
            "command": st.sampled_from(
                ["view", "create", "str_replace", "insert", "undo_edit"]
            ),
            "path": s,
        },
        optional={
            "file_text": s,
            "old_str": s,
            "new_str": s,
            "insert_line": st.integers(min_value=0, max_value=10_000),
            "view_range": st.lists(st.integers(-1, 10_000), min_size=2, max_size=2),
        },
    )
    delegate = st.fixed_dictionaries(
        {"command": st.sampled_from(["spawn", "delegate"])},
        optional={
            "ids": st.lists(s, max_size=3),
            "agent_types": st.lists(s, max_size=3),
            "tasks": st.dictionaries(s, s, max_size=3),
        },
    )
    task_tracker = st.fixed_dictionaries(
        {
            "command": st.sampled_from(["view", "plan"]),
            "task_list": st.lists(
                st.fixed_dictionaries(
                    {
                        "title": s,
                        "notes": s,
                        "status": st.sampled_from(["todo", "in_progress", "done"]),
                    }
                ),
                max_size=3,
            ),
        }
    )
    return st.one_of(
        st.tuples(st.just("terminal"), terminal),
        st.tuples(st.just("file_editor"), file_editor),
        st.tuples(st.just("delegate"), delegate),
        st.tuples(st.just("task_tracker"), task_tracker),
    )


def _round_trip(tool_name: str, arguments: dict[str, Any]) -> BaseModel:
    """Render a tool call as non-native text and parse it back like the agent."""
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Do the task."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "toolu_01",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ],
        },
    ]
    as_text = convert_fncall_messages_to_non_fncall_messages(messages, TOOLS)
    parsed = convert_non_fncall_messages_to_fncall_messages(as_text, TOOLS)
    function = parsed[-1]["tool_calls"][0]["function"]

    args = parse_tool_call_arguments(function["arguments"])
    name, args = normalize_tool_call(function["name"], args, ACTIONS.keys())
    action_type = ACTIONS[name]
    args = fix_malformed_tool_arguments(args, action_type)
    return action_type.model_validate(args)


def _expected(tool_name: str, arguments: dict[str, Any]) -> BaseModel | None:
    try:
        return ACTIONS[tool_name].model_validate(arguments)
    except ValidationError:
        return None


def _has_no_tag(s: str) -> bool:
    return not any(
        t in s for t in ("<function=", "</function", "<parameter=", "</parameter")
    )


def _check_round_trip(call: tuple[str, dict[str, Any]]) -> None:
    tool_name, arguments = call
    expected = _expected(tool_name, arguments)
    if expected is not None:
        assert _round_trip(tool_name, arguments) == expected


@given(_arguments(trimmed_text.filter(_has_no_tag)))
def test_rendered_tool_call_without_tag_text_parses_back(call):
    _check_round_trip(call)


@pytest.mark.xfail(
    strict=True,
    reason="the parser strip()s every parameter value (#6348, fix in #6451)",
)
@given(_arguments(text.filter(_has_no_tag)))
@example(("terminal", {"command": "\n"}))
def test_rendered_tool_call_keeps_edge_whitespace(call):
    _check_round_trip(call)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the text format has no escaping: a value holding '</function>' or "
        "'</parameter>' is cut short, and since the whole history is parsed "
        "again on every turn, one such past call fails every later turn"
    ),
)
@given(_arguments(trimmed_text))
@example(("terminal", {"command": "</function>"}))
@example(("terminal", {"command": "</parameter>"}))
@example(("file_editor", {"command": "view", "path": "</parameter><parameter="}))
def test_rendered_tool_call_with_tag_text_parses_back(call):
    _check_round_trip(call)
