"""Define, register and use custom tools the way example 02 does
(`programs/S17/mytools.py` holds the definitions).

Usage: custom_tools.py conversation|registry|schemas|agent_init

- conversation: a scripted agent uses `word_count` and the two tools of
  `notebook_tool_set` (params `name`, `delay=1`), sends bad arguments and an
  unknown tool name, makes three `add_note` and three `list_notes` calls in
  one response each (`tool_concurrency_limit=4`), then the program calls
  `conversation.execute_tool` directly.
- registry: `register_tool`, `resolve_tool`, naming, auto-registration on
  import, `Tool` specs, usability, the catalog and module qualnames.
- schemas: what the agent sends the model, captured at a local
  OpenAI-compatible endpoint, plus the Responses and MCP views, action
  serialization, `py_type` and output coercion.
- agent_init: built-in tools via `include_default_tools`, duplicate names,
  and a custom `finish`.
"""

import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from pydantic import Field
from S14.toolkit import converse, tool_results
from S17 import mytools
from sdk_verify import emit

from openhands.sdk import Action, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor, register_tool


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


capture = Capture()
logging.getLogger("openhands").addHandler(capture)
mode = sys.argv[1]
result: dict = {"ok": True, "mode": mode}


def error_of(fn) -> str:
    try:
        fn()
    except Exception as exc:  # what a program sees
        return f"{type(exc).__name__}: {exc}"[:300]
    return "no error"


if mode == "conversation":
    from S17.mytools import ListNotesAction, WordCountAction

    from openhands.sdk.event import ActionEvent

    Path("doc.txt").write_text("one two three four five\n")
    add = {"tool": "add_note", "args": {"text": "first"}}
    script = [
        {"tool": "word_count", "args": {"path": "doc.txt"}},
        {"tool": "word_count", "args": {"path": "missing.txt"}},
        {"tool": "word_count", "args": {"file": "doc.txt"}},
        {"tool": "no_such_tool", "args": {}},
        {
            "tools": [
                add,
                {
                    "tool": "add_note",
                    "args": {"text": "second", "priority": "high", "tags": ["a", "b"]},
                },
                {
                    "tool": "add_note",
                    "args": {
                        "text": "third",
                        "outline": {
                            "title": "root",
                            "children": [{"title": "leaf", "children": []}],
                        },
                    },
                },
            ]
        },
        {"tools": [{"tool": "list_notes", "args": {}}] * 3},
        {"finish": "notes taken"},
    ]
    conversation = converse(
        [
            Tool(name=mytools.WordCountTool.name),
            Tool(name="notebook_tool_set", params={"name": "conv", "delay": 1}),
        ],
        script,
        agent_kwargs={"tool_concurrency_limit": 4},
    )
    rows = [r for r in tool_results(conversation) if r["tool"] != "finish"]
    direct = conversation.execute_tool("list_notes", ListNotesAction(contains="high"))
    direct_count = conversation.execute_tool(
        "word_count", WordCountAction(path="doc.txt")
    )
    notebook = mytools.NOTEBOOKS["conv"]

    def overlapped(label: str) -> bool:
        spans = sorted((s, e) for name, s, e in notebook.calls if name == label)
        return any(b[0] < a[1] for a, b in zip(spans, spans[1:]))

    actions = [e for e in conversation.state.events if isinstance(e, ActionEvent)]
    spec_dump = [t.model_dump(mode="json") for t in conversation.agent.tools]
    result.update(
        {
            "status": conversation.state.execution_status.value,
            "tools_map": sorted(conversation.agent.tools_map),
            "results": [
                {k: r[k] for k in ("tool", "kind", "is_error", "text")} for r in rows
            ],
            "adds_overlapped": overlapped("add_note"),
            "lists_overlapped": overlapped("list_notes"),
            "notes": notebook.notes,
            "direct_text": " ".join(
                c.text for c in direct.to_llm_content if isinstance(c, TextContent)
            ),
            "direct_kind": type(direct).__name__,
            "direct_count_words": getattr(direct_count, "words", None),
            "action_kinds": sorted(
                {type(a.action).__name__ for a in actions if a.action is not None}
            ),
            "agent_tool_specs": spec_dump,
        }
    )
    for row in result["results"]:
        row["text"] = row["text"][:300]
    conversation.close()

elif mode == "registry":
    from openhands.sdk.tool import (
        is_tool_available,
        is_tool_usable,
        list_registered_tools,
        list_tool_catalog,
        resolve_tool,
    )
    from openhands.sdk.tool.registry import (
        get_tool_module_qualnames,
        list_usable_tools,
        seal_tool_catalog,
        unseal_tool_catalog,
    )

    conversation = converse([], [], run=False)
    conversation._ensure_agent_ready()
    state = conversation.state

    # register_tool: what it accepts
    result["callable_factory"] = error_of(
        lambda: register_tool("from_lambda", lambda **_kw: [])
    )
    result["empty_name"] = error_of(lambda: register_tool("  ", mytools.WordCountTool))
    instance = mytools.WordCountTool.create(state)[0]
    register_tool("wc_instance", instance)
    result["instance_same_object"] = (
        resolve_tool(Tool(name="wc_instance"), state)[0] is instance
    )
    result["instance_with_params"] = error_of(
        lambda: resolve_tool(Tool(name="wc_instance", params={"x": 1}), state)
    )
    before = len(capture.lines)
    register_tool(mytools.WordCountTool.name, mytools.WordCountTool)
    result["duplicate_warning"] = capture.lines[before:]

    # resolve_tool
    result["unregistered"] = error_of(
        lambda: resolve_tool(Tool(name="no_such_tool"), state)
    )
    result["builtin_finish"] = [
        type(t).__name__ for t in resolve_tool(Tool(name="finish"), state)
    ]
    tools = resolve_tool(Tool(name="notebook_tool_set", params={"name": "reg"}), state)
    result["tool_set_names"] = [t.name for t in tools]
    result["tool_set_shares_notebook"] = (
        tools[0].executor.notebook is tools[1].executor.notebook
    )
    result["params_reached_create"] = "reg" in mytools.NOTEBOOKS
    result["word_count_root_is_workspace"] = str(
        resolve_tool(Tool(name="word_count"), state)[0].executor.root
    ) == str(state.workspace.working_dir)
    result["create_rejects_unknown_param"] = error_of(
        lambda: resolve_tool(Tool(name="word_count", params={"colour": "red"}), state)
    )
    verdict = {
        "type": "object",
        "properties": {"verdict": {"type": "string"}},
        "required": ["verdict"],
    }
    with_schema = resolve_tool(
        Tool(name="word_count", params={"response_schema": verdict}), state
    )
    result["response_schema_fields"] = sorted(
        with_schema[0].to_openai_tool()["function"]["parameters"]["properties"]
    )
    result["response_schema_on_set"] = error_of(
        lambda: resolve_tool(
            Tool(name="notebook_tool_set", params={"response_schema": verdict}), state
        )
    )

    # naming
    result["names"] = {
        cls.__name__: cls.name
        for cls in (
            mytools.WordCountTool,
            mytools.NotebookToolSet,
            mytools.ShoutTool,
            mytools.AddNoteTool,
            mytools.GatedTool,
        )
    }

    # auto-registration: importing a built-in tool module registers it
    result["glob_before_import"] = "glob" in list_registered_tools()
    import openhands.tools.glob  # noqa: F401

    result["glob_after_import"] = "glob" in list_registered_tools()
    result["glob_resolves"] = [t.name for t in resolve_tool(Tool(name="glob"), state)]

    # Tool specs
    spec = Tool(name="notebook_tool_set", params={"name": "x", "delay": 0.5})
    result["spec_json"] = spec.model_dump_json()
    result["spec_round_trip"] = Tool.model_validate_json(spec.model_dump_json()) == spec
    source = {"name": "y"}
    copied = Tool(name="notebook_tool_set", params=source)
    source["name"] = "changed"
    result["spec_params_copied"] = copied.params == {"name": "y"}
    result["spec_reassign"] = error_of(lambda: setattr(copied, "params", {"name": "z"}))
    result["spec_empty_name"] = error_of(lambda: Tool(name=""))

    # usability
    os.environ.pop("GATED_TOOL_USABLE", None)
    result["gated_usable_off"] = is_tool_usable("gated")
    result["gated_listed_off"] = "gated" in list_usable_tools()
    os.environ["GATED_TOOL_USABLE"] = "1"
    result["gated_usable_on"] = is_tool_usable("gated")
    os.environ.pop("GATED_TOOL_USABLE")
    before = len(capture.lines)
    result["broken_usable"] = is_tool_usable("broken_check")
    result["broken_warning"] = capture.lines[before:][:1]
    result["available"] = {
        "finish": is_tool_available("finish"),
        "gated": is_tool_available("gated"),
        "gated_unchecked": is_tool_available("gated", check_usable=False),
        "no_such_tool": is_tool_available("no_such_tool"),
    }

    # catalog
    catalog = {e.name: e.model_dump() for e in list_tool_catalog()}
    result["catalog_word_count"] = catalog.get("word_count")
    result["catalog_gated_usable"] = catalog.get("gated", {}).get("usable")
    names = [e.name for e in list_tool_catalog()]
    result["catalog_switch_llm_count"] = names.count("switch_llm")
    result["catalog_has_class_names"] = (
        "SwitchLLMTool" in names or "FinishTool" in names
    )
    result["catalog_finish"] = catalog.get("finish")
    result["catalog_router"] = catalog.get("route_task_to_model")
    result["catalog_unchecked_all_usable"] = all(
        e.usable for e in list_tool_catalog(check_usable=False)
    )
    result["catalog_default_set"] = sorted(
        n for n, e in catalog.items() if e["in_default_set"]
    )
    seal_tool_catalog()
    register_tool("late_tool", mytools.WordCountTool)
    result["late_while_sealed"] = "late_tool" in {e.name for e in list_tool_catalog()}
    result["late_resolves_while_sealed"] = [
        t.name for t in resolve_tool(Tool(name="late_tool"), state)
    ]
    unseal_tool_catalog()
    result["late_after_unseal"] = "late_tool" in {e.name for e in list_tool_catalog()}

    # module qualnames
    qualnames = get_tool_module_qualnames()
    result["qualname_word_count"] = qualnames.get("word_count")
    result["qualname_glob"] = qualnames.get("glob")
    qualnames["word_count"] = "tampered"
    result["qualnames_is_copy"] = (
        get_tool_module_qualnames().get("word_count") == "S17.mytools"
    )
    conversation.close()

elif mode == "schemas":
    from pydantic import SecretStr
    from S16 import fake_openai
    from S17.mytools import AddNoteAction, Section

    from openhands.sdk import LLM, Agent, Conversation
    from openhands.sdk.tool.schema import py_type

    base_url = fake_openai.serve("Nothing to do.")
    llm = LLM(
        model="openai/fake-model",
        api_key=SecretStr("local"),
        base_url=base_url,
        usage_id="agent",
        num_retries=0,
    )
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name="word_count"),
            Tool(name="notebook_tool_set", params={"name": "schemas"}),
        ],
    )
    conversation = Conversation(agent=agent, workspace=str(Path.cwd()), visualizer=None)
    conversation.send_message("Say hello.")
    conversation.run()
    sent = fake_openai.requests[0].get("tools", []) if fake_openai.requests else []
    by_name = {t["function"]["name"]: t["function"] for t in sent}
    result["status"] = conversation.state.execution_status.value
    result["sent_tools"] = sorted(by_name)
    for name in ("add_note", "list_notes", "word_count"):
        params = by_name.get(name, {}).get("parameters", {})
        result[f"{name}_properties"] = list(params.get("properties", {}))
        result[f"{name}_required"] = params.get("required", [])
    add_params = by_name.get("add_note", {}).get("parameters", {})
    result["add_note_priority_enum"] = (
        add_params.get("properties", {}).get("priority", {}).get("enum")
    )
    add_json = json.dumps(add_params)
    result["add_note_has_ref"] = "$ref" in add_json
    result["add_note_outline_mentions_children"] = "children" in add_json
    result["sent_has_kind"] = any(
        "kind" in f.get("parameters", {}).get("properties", {})
        for f in by_name.values()
    )

    tools = conversation.agent.tools_map
    responses = tools["add_note"].to_responses_tool(add_security_risk_prediction=True)
    result["responses_tool"] = {
        "type": responses["type"],
        "name": responses["name"],
        "strict": responses["strict"],
        "properties": list(responses["parameters"]["properties"]),
    }
    mcp = tools["add_note"].to_mcp_tool()
    result["mcp_keys"] = sorted(mcp)
    result["mcp_properties"] = list(mcp["inputSchema"].get("properties", {}))
    result["mcp_annotations"] = (
        mcp["annotations"].model_dump()
        if hasattr(mcp["annotations"], "model_dump")
        else mcp["annotations"]
    )
    result["list_notes_resources"] = list(
        tools["list_notes"].declared_resources(None).keys
    )
    result["add_note_resources"] = list(tools["add_note"].declared_resources(None).keys)
    result["word_count_resources_declared"] = (
        tools["word_count"].declared_resources(None).declared
    )
    conversation.close()

    # serialization: actions are a discriminated union by `kind`
    action = AddNoteAction(
        text="x", outline=Section(title="r", children=[Section(title="c")])
    )
    dumped = action.model_dump(mode="json")
    result["action_kind"] = dumped.get("kind")
    result["action_json_kind"] = json.loads(action.model_dump_json()).get("kind")
    back = Action.model_validate(dumped)
    result["action_round_trip_type"] = type(back).__name__
    # Validation should leave the caller's dict alone, so it can be validated again.
    result["validate_keeps_input_kind"] = "kind" in dumped
    result["validate_same_dict_again"] = error_of(lambda: Action.model_validate(dumped))
    result["action_round_trip_equal"] = back == action
    result["action_frozen"] = error_of(lambda: setattr(action, "text", "y"))
    tool = tools["word_count"]
    copy = tool.set_executor(mytools.WordCountExecutor("/"))
    result["set_executor_returns_copy"] = (
        copy is not tool and tool.executor is not copy.executor
    )
    result["tool_frozen"] = error_of(lambda: setattr(tool, "description", "changed"))

    # py_type
    specs = {
        "string": {"type": "string"},
        "integer": {"type": "integer"},
        "number": {"type": "number"},
        "boolean": {"type": "boolean"},
        "object": {"type": "object"},
        "array_of_int": {"type": "array", "items": {"type": "integer"}},
        "nullable_string": {"type": ["string", "null"]},
        "mixed": {"type": ["string", "integer"]},
    }
    result["py_types"] = {
        k: str(py_type(v)).replace("typing.", "") for k, v in specs.items()
    }

    # output coercion and missing executors
    class PlainAction(Action):
        y: int = Field(description="y")

    class DictExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):  # noqa: ARG002
            return {
                "kind": "WordCountObservation",
                "words": action.y,
                "path": "dict.txt",
            }

    class ListExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):  # noqa: ARG002
            return [1, 2, 3]

    class WrongExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):  # noqa: ARG002
            return mytools.NotesObservation(notes=["not a word count"])

    class LooseTool(ToolDefinition[PlainAction, Observation]):
        @classmethod
        def create(cls, conv_state=None, **params) -> Sequence["LooseTool"]:  # noqa: ARG003
            return []

    loose = LooseTool(
        description="d",
        action_type=PlainAction,
        observation_type=None,
        executor=DictExecutor(),
    )
    coerced = loose(PlainAction(y=7))
    result["dict_coerced_type"] = type(coerced).__name__
    result["dict_coerced_text"] = "".join(
        c.text for c in coerced.to_llm_content if isinstance(c, TextContent)
    )
    result["list_result"] = error_of(
        lambda: loose.set_executor(ListExecutor())(PlainAction(y=1))
    )
    no_exec = mytools.WordCountTool(
        description="d",
        action_type=mytools.WordCountAction,
        observation_type=mytools.WordCountObservation,
    )
    result["no_executor_call"] = error_of(
        lambda: no_exec(mytools.WordCountAction(path="x"))
    )
    result["no_executor_as_executable"] = error_of(no_exec.as_executable)
    typed = no_exec.set_executor(WrongExecutor())
    result["wrong_observation_type"] = error_of(
        lambda: typed(mytools.WordCountAction(path="x"))
    )

elif mode == "agent_init":
    from openhands.sdk.tool.builtins import BUILT_IN_TOOL_CLASSES

    def tool_names(**agent_kwargs) -> list[str] | str:
        try:
            conversation = converse(
                [Tool(name="word_count")] + agent_kwargs.pop("extra", []),
                [{"text": "nothing to do"}],
                agent_kwargs=agent_kwargs,
            )
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:300]
        names = sorted(conversation.agent.tools_map)
        conversation.close()
        return names

    class CustomFinishAction(Action):
        result: str = Field(description="The result")
        success: bool = Field(description="Whether it worked")

    class CustomFinishExecutor(ToolExecutor):
        def __call__(self, action, conversation=None):  # noqa: ARG002
            return mytools.NotesObservation(
                notes=[f"custom finish: {action.result} {action.success}"]
            )

    class CustomFinishTool(
        ToolDefinition[CustomFinishAction, mytools.NotesObservation]
    ):
        name: ClassVar[str] = "finish"

        @classmethod
        def create(cls, conv_state=None, **params) -> Sequence["CustomFinishTool"]:  # noqa: ARG003
            return [
                cls(
                    description="Finish with a result and a success flag.",
                    action_type=CustomFinishAction,
                    observation_type=mytools.NotesObservation,
                    executor=CustomFinishExecutor(),
                )
            ]

    register_tool("custom_finish", CustomFinishTool)
    result["built_in_classes"] = sorted(BUILT_IN_TOOL_CLASSES)
    result["default"] = tool_names()
    result["only_finish"] = tool_names(include_default_tools=["FinishTool"])
    result["none"] = tool_names(include_default_tools=[])
    result["unknown_builtin"] = tool_names(include_default_tools=["NoSuchTool"])
    result["duplicate"] = tool_names(extra=[Tool(name="word_count")])
    conversation = converse(
        [Tool(name="custom_finish")],
        [{"tool": "finish", "args": {"result": "built", "success": True}}],
        agent_kwargs={"include_default_tools": ["ThinkTool"]},
    )
    finish = conversation.agent.tools_map["finish"]
    result["custom_finish_action"] = finish.action_type.__name__
    result["custom_finish_status"] = conversation.state.execution_status.value
    result["custom_finish_results"] = [
        {k: r[k] for k in ("tool", "is_error", "text")}
        for r in tool_results(conversation)
    ]
    runtime = CustomFinishTool.create()[0]
    result["runtime_replace"] = error_of(
        lambda: conversation.agent.add_runtime_tools([runtime])
    )
    conversation.close()

else:
    raise SystemExit(f"unknown mode {mode}")

emit(result)
