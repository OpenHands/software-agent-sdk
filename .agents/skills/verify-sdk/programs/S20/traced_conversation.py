"""Trace a conversation to a local OTLP collector, the way a program sends
traces to its own collector: set the endpoint variables before importing the
SDK, run an agent that uses the terminal (one call, then two in parallel),
and read what the collector received.

Usage: traced_conversation.py MODE
  args      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT; observability settings passed
            to Conversation(...) (user id, tags, metadata, span name)
  env       the same through OPENHANDS_OBSERVABILITY_* variables only, with the
            parent span context from init_laminar_for_external()
  laminar   LMNR_PROJECT_API_KEY with a self-hosted Laminar URL (the local
            collector) over HTTP, LMNR_INSTRUMENTS with one bad name
  model     a real LLM object (litellm) against a local OpenAI-compatible
            endpoint that reports cost and cached tokens; then the utility
            calls generate_title() and ask_agent()
  delegate  a sub-agent conversation started from inside a tool span with
            detached_delegate_context()
  off       no endpoint: nothing is traced and lmnr is never imported
  live      as `args` without settings, with the run's real model
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

from otlp import Collector, ancestors, summarize


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "S18"))
mode = sys.argv[1]
collector = Collector().__enter__()
if mode == "laminar":
    port = collector.endpoint.split(":")[-1].split("/")[0]
    os.environ.update(
        {
            "LMNR_PROJECT_API_KEY": "lmnr-verify-key",
            "LMNR_BASE_URL": "http://127.0.0.1",
            "LMNR_HTTP_PORT": port,
            "LMNR_FORCE_HTTP": "true",
            "LMNR_INSTRUMENTS": "litellm,not-an-instrument",
        }
    )
elif mode != "off":
    os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = collector.endpoint
    os.environ["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] = "http/protobuf"
    os.environ["OTEL_EXPORTER"] = "otlp_http"

warnings_seen: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            warnings_seen.append(record.getMessage())


logging.getLogger("openhands").addHandler(_Capture())
started = time.monotonic()
import openhands.sdk  # noqa: E402,F401


import_seconds = time.monotonic() - started

from sdk_verify import emit, make_llm, run_paths  # noqa: E402

from openhands.sdk import Agent, Conversation, Tool  # noqa: E402
from openhands.sdk.observability import init_laminar_for_external  # noqa: E402
from openhands.sdk.observability.laminar import (  # noqa: E402
    should_enable_observability,
)
from openhands.tools.terminal import TerminalTool  # noqa: E402


lmnr_imported_at_start = "lmnr" in sys.modules
result: dict = {"ok": True, "mode": mode}
script = [
    {"text": "One.", "tool": "terminal", "args": {"command": "echo one"}},
    {
        "tools": [
            {"tool": "terminal", "args": {"command": "echo two"}},
            {"tool": "terminal", "args": {"command": "echo three"}},
        ]
    },
    {"finish": "traced"},
]
fake = None
if mode == "model":
    from servers import FakeLLMServer

    fake = FakeLLMServer(script, cost=0.0125, cached_tokens=40).__enter__()
    llm = fake.llm()
elif mode == "live":
    llm = make_llm()  # the run's real model; exit 3 without its key
else:
    llm = make_llm(script)
agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)], tool_concurrency_limit=2)

kwargs: dict = {}
parent_trace = None
if mode == "args":
    kwargs = dict(
        user_id="user-7",
        tags={"team": "infra"},
        observability_metadata={"ticket": "OPS-1", "attempt": 2},
        observability_tags=["nightly", "verify"],
        observability_span_name="nightly-run",
    )
elif mode == "env":
    from lmnr import Laminar

    # A webhook handler's span, handed to the conversation through the env as
    # an automation runner does.
    with Laminar.start_as_current_span("webhook-handler"):
        ctx = init_laminar_for_external()
        parent_trace = str(ctx.trace_id).replace("-", "") if ctx else None
        os.environ["OPENHANDS_OBSERVABILITY_PARENT_SPAN_CONTEXT"] = (
            Laminar.serialize_span_context() if ctx else ""
        )
    result["external_context"] = ctx is not None
    os.environ["OPENHANDS_OBSERVABILITY_METADATA"] = json.dumps(
        {"automation.id": "auto-9"}
    )
    os.environ["OPENHANDS_OBSERVABILITY_SPAN_NAME"] = "env-span"
elif mode == "off":
    result["external_context"] = init_laminar_for_external()

workspace = str(run_paths()["workspace"])
if mode == "delegate":
    from lmnr import Laminar

    from openhands.sdk.observability.laminar import detached_delegate_context

    parent = Conversation(agent=agent, workspace=workspace, visualizer=None)
    parent.send_message("delegate")
    parent.run()
    # What the task tool does: inside its TOOL span (carrying the tool call
    # id), start the sub-agent's conversation detached from that trace.
    with Laminar.start_as_current_span("task", span_type="TOOL"):
        Laminar.set_trace_metadata({"tool_call_id": "call_task_1"})
        with detached_delegate_context() as link:
            child = Conversation(
                agent=Agent(llm=make_llm([{"finish": "child done"}]), tools=[]),
                workspace=workspace,
                visualizer=None,
                observability_metadata=dict(link),
                observability_tags=["delegate"],
            )
            child.send_message("sub task")
            child.run()
            child.close()
    result["link_keys"] = sorted(link)
    conversation = parent
else:
    conversation = Conversation(
        agent=agent, workspace=workspace, visualizer=None, **kwargs
    )

cid = str(conversation.id)
if mode == "live":
    conversation.send_message("Run `echo one` in the terminal, then finish.")
    conversation.run()
elif mode != "delegate":
    conversation.send_message("trace this")
    conversation.run()
result["status"] = str(conversation.state.execution_status.value)
if mode == "model":
    result["title"] = conversation.generate_title(llm=fake.llm(usage_id="title"))
    result["answer"] = conversation.ask_agent("What did you run?")
    result["metrics_cost"] = (
        conversation.state.stats.get_combined_metrics().accumulated_cost
    )
conversation.close()

if mode != "off":
    from lmnr import Laminar

    Laminar.flush()
    collector.wait_for(lambda spans: any(s["name"] == "conversation" for s in spans))

spans = collector.spans
roots = [s for s in spans if s["name"] == "conversation"]
root = next(
    (
        s
        for s in roots
        if s["attributes"].get("lmnr.association.properties.session_id") == cid
    ),
    None,
)
tools = [
    s
    for s in spans
    if s["attributes"].get("lmnr.span.type") == "TOOL" and s["name"] != "task"
]
terminal = [s for s in tools if s["name"] == "TerminalAction"]
steps = [s for s in spans if s["name"] == "agent.step"]
llm_spans = [s for s in spans if s["attributes"].get("lmnr.span.type") == "LLM"]
outer_llm = [s for s in llm_spans if s["name"].startswith("llm.")]
tree = summarize(spans)
root_attrs = root["attributes"] if root else {}
META = "lmnr.association.properties.metadata."


def ops(name: str) -> list:
    return sorted(
        {
            str(s["attributes"].get(META + "openhands.operation"))
            for s in spans
            if s["name"] == name
        }
    )


result.update(
    {
        "enabled": should_enable_observability(),
        "import_seconds": round(import_seconds, 2),
        "lmnr_imported_at_start": lmnr_imported_at_start,
        "lmnr_imported": "lmnr" in sys.modules,
        "otlp_posts": len(collector.requests),
        "auth_header": next(
            (
                r["headers"].get("Authorization") or r["headers"].get("authorization")
                for r in collector.requests
            ),
            None,
        ),
        "warnings": [
            w for w in warnings_seen if "LMNR" in w or "instrument" in w.lower()
        ][:3],
        "summary": tree,
        "root": {
            "found": root is not None,
            "is_trace_root": bool(root) and root["parent"] is None,
            "attributes": {
                k: v for k, v in root_attrs.items() if not k.startswith("lmnr.span.")
            },
        },
        # Flat copies of dotted attribute keys, so checks can name them.
        "root_user_id": root_attrs.get("lmnr.association.properties.user_id"),
        "root_session_id_is_conversation": root_attrs.get(
            "lmnr.association.properties.session_id"
        )
        == cid,
        "root_attr_ticket": root_attrs.get("ticket", root_attrs.get(META + "ticket")),
        "root_meta_ticket": root_attrs.get(META + "ticket"),
        "root_automation_id": root_attrs.get(
            "automation.id", root_attrs.get(META + "automation.id")
        ),
        "root_tag_team": root_attrs.get("conversation.tags.team"),
        "root_tags": root_attrs.get("lmnr.association.properties.tags"),
        "parent_trace_joined": bool(root)
        and parent_trace is not None
        and root["trace_id"] == parent_trace,
        "root_has_parent": bool(root) and root["parent"] is not None,
        "conversation_traces": len({s["trace_id"] for s in roots}),
        "steps": len(steps),
        "tool_spans": len(tools),
        "terminal_spans": len(terminal),
        "tool_call_ids": sorted(
            str(s["attributes"].get(META + "tool_call_id", "")) for s in tools
        ),
        "terminal_input_is_action": all(
            "echo" in str(s["attributes"].get("lmnr.span.input", "")) for s in terminal
        ),
        "tools_under_step": bool(tools)
        and all("agent.step" in ancestors(spans, s) for s in tools),
        "parallel_terminal_parents": len({s["parent"] for s in terminal}),
        "llm_spans": len(outer_llm),
        "llm_costs": sorted(
            {s["attributes"].get("gen_ai.usage.cost") for s in outer_llm} - {None}
        ),
        "llm_cache_read": sorted(
            {
                s["attributes"].get("gen_ai.usage.cache_read_input_tokens")
                for s in outer_llm
            }
            - {None}
        ),
        "llm_under_step": sum(
            1 for s in outer_llm if "agent.step" in ancestors(spans, s)
        ),
        "llm_under_conversation": sum(
            1 for s in outer_llm if "conversation" in ancestors(spans, s)
        ),
        "title_operation": ops("conversation.generate_title"),
        "ask_operation": ops("conversation.ask_agent"),
    }
)
if mode == "delegate":
    task = next((s for s in spans if s["name"] == "task"), None)
    child_root = next((s for s in roots if s is not root), None)
    result["delegate"] = {
        "task_span": task is not None,
        "child_root_found": child_root is not None,
        "child_new_trace": bool(task and child_root)
        and child_root["trace_id"] != task["trace_id"],
        "child_is_trace_root": bool(child_root) and child_root["parent"] is None,
        "child_parent_trace_ok": bool(task and child_root)
        and str(
            child_root["attributes"].get(META + "delegate.parent_trace_id", "")
        ).replace("-", "")
        == task["trace_id"],
        "child_tool_call_id": child_root["attributes"].get(META + "tool_call_id")
        if child_root
        else None,
    }
emit(result)
if fake is not None:
    fake.__exit__()
collector.__exit__()
