"""Drive the agent step loop the way a program does, one scenario per mode.
The model is the SDK's scripted `TestLLM` (or, where cost or the wire format
matters, a real `LLM` against the local endpoint in `S02/fake_provider.py`).

Usage: loop.py MODE
  status          status transitions seen by a callback, re-run, new message
  budget          max_budget_per_run with a priced model
  thresholds      custom stuck_detection_thresholds
  reasoning-only  a reply with only reasoning gets a nudge
  parallel        tool_concurrency_limit, order, a failing call, finish truncation
  locks           resource keys, undeclared tools, declared-safe tools
  tool-exception  an exception inside a tool executor
  message-while-running  send_message from another thread mid-run
  async           arun() releases the state lock during the LLM call
  image-non-vision  images to a model without vision
  image-input     images to a vision model (OpenAI and Anthropic wire)
  ask-agent       a side question while and after the run
  structured-output  response_schema on terminal and finish
  context-overflow  context-window errors with and without a condenser
"""

import asyncio
import base64
import io
import sys
import threading
import time

from pydantic import BaseModel, Field, SecretStr
from S01.sleeper import TIMELINE, overlapped
from S02.fake_provider import FakeProvider
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation.impl.local_conversation import LocalConversation
from openhands.sdk.event import (
    ActionEvent,
    AgentErrorEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.event.conversation_error import ConversationErrorEvent
from openhands.sdk.llm import ImageContent, Message, MessageToolCall, TextContent
from openhands.sdk.testing import TestLLM
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
WORKSPACE = str(run_paths()["workspace"])
out: dict = {"ok": True, "mode": mode}


def conversation_for(agent: Agent, **kw) -> Conversation:
    return Conversation(agent=agent, workspace=WORKSPACE, visualizer=None, **kw)


def kinds(conversation: Conversation) -> list[str]:
    return [type(e).__name__ for e in conversation.state.events]


def error_codes(conversation: Conversation) -> list:
    return [
        e.code
        for e in conversation.state.events
        if isinstance(e, ConversationErrorEvent)
    ]


def sleeper_call(label: str, seconds: float = 0.5, **kw) -> dict:
    return {"tool": "sleeper", "args": {"label": label, "seconds": seconds, **kw}}


def run_sleepers(calls: list[dict], limit: int) -> tuple[Conversation, float]:
    TIMELINE.clear()
    agent = Agent(
        llm=make_llm([{"tools": calls}, {"finish": "done"}]),
        tools=[Tool(name="SleeperTool")],
        tool_concurrency_limit=limit,
    )
    conversation = conversation_for(agent)
    conversation.send_message("Run the sleepers.")
    started = time.monotonic()
    conversation.run()
    return conversation, round(time.monotonic() - started, 2)


if mode == "status":
    seen: list[str] = []

    class Slow(TestLLM):
        def completion(self, *args, **kwargs):
            time.sleep(0.5)
            return super().completion(*args, **kwargs)

    llm = Slow.from_messages(
        __import__("sdk_verify").script_messages(
            [
                {"tool": "terminal", "args": {"command": "echo hi"}},
                {"text": "All done."},
            ]
        ),
        usage_id="agent",
    )
    conversation = conversation_for(
        Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    )
    out["initial"] = conversation.state.execution_status.value
    conversation.send_message("Say hi.")
    thread = threading.Thread(target=conversation.run)
    thread.start()
    while thread.is_alive():  # a program watching the status from another thread
        status = conversation.state.execution_status.value
        if not seen or seen[-1] != status:
            seen.append(status)
        time.sleep(0.05)
    thread.join()
    out["observed"] = seen
    out["after_run"] = conversation.state.execution_status.value
    calls_before = llm._call_count
    conversation.run()  # a finished conversation returns at once, no LLM call
    out["rerun_llm_calls"] = llm._call_count - calls_before
    out["after_rerun"] = conversation.state.execution_status.value
    conversation.send_message("One more thing.")
    out["after_new_message"] = conversation.state.execution_status.value
    conversation.close()

elif mode == "budget":
    price = {
        "input_cost_per_token": 0.001,
        "output_cost_per_token": 0.002,
    }  # 0.14 per call
    echo = {
        "tool": "terminal",
        "args": {"command": "echo spend", "security_risk": "LOW"},
    }
    results = {}
    for name, script, budget in (
        ("over_budget", [echo, echo, echo, {"finish": "done"}], 0.2),
        ("finish_over_budget", [{"finish": "done at once"}], 0.1),
    ):
        with FakeProvider(script) as server:
            llm = LLM(
                model="openai/gpt-4o-mini",
                base_url=server.url,
                api_key=SecretStr("sk-test"),
                usage_id="agent",
                num_retries=1,
                **price,
            )
            agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
            try:
                conversation_for(agent, max_budget_per_run=budget).close()
                results["factory_accepts_budget"] = True
            except TypeError as exc:
                results["factory_accepts_budget"] = False
                results["factory_error"] = str(exc)[:200]
            # LocalConversation takes it; Conversation(...) builds one for a local
            # workspace.
            conversation = LocalConversation(
                agent=agent,
                workspace=WORKSPACE,
                visualizer=None,
                max_budget_per_run=budget,
            )
            conversation.send_message("Spend.")
            conversation.run()
            results[name] = {
                "status": conversation.state.execution_status.value,
                "codes": error_codes(conversation),
                "llm_calls": len(server.requests),
                "cost": round(
                    conversation.conversation_stats.get_combined_metrics().accumulated_cost,
                    4,
                ),
            }
            conversation.close()
    out |= results

elif mode == "thresholds":
    ls = {"tool": "terminal", "args": {"command": "ls"}}
    results = {}
    for name, thresholds in (("default", None), ("custom", {"action_observation": 2})):
        llm = make_llm([ls, ls, ls, {"finish": "never stuck"}])
        conversation = conversation_for(
            Agent(llm=llm, tools=[Tool(name=TerminalTool.name)]),
            stuck_detection_thresholds=thresholds,
        )
        conversation.send_message("List files.")
        conversation.run()
        results[name] = {
            "status": conversation.state.execution_status.value,
            "llm_calls": llm._call_count,
        }
        conversation.close()
    out |= results

elif mode == "reasoning-only":
    finish = MessageToolCall(
        id="call_f", name="finish", arguments='{"message": "done"}', origin="completion"
    )
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant", content=[], reasoning_content="I am only thinking."
            ),
            Message(
                role="assistant", content=[TextContent(text="")], tool_calls=[finish]
            ),
        ],
        usage_id="agent",
    )
    conversation = conversation_for(Agent(llm=llm, tools=[]))
    conversation.send_message("Do it.")
    conversation.run()
    nudges = [
        e
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "environment"
    ]
    out |= {
        "status": conversation.state.execution_status.value,
        "llm_calls": llm._call_count,
        "nudges": len(nudges),
        "nudge_text": nudges[0].llm_message.content[0].text if nudges else None,
    }
    conversation.close()

elif mode == "parallel":
    calls = [sleeper_call(f"s{i}", 1.0) for i in range(4)]
    sequential, seq_secs = run_sleepers(calls, 1)
    out["sequential_seconds"] = seq_secs
    parallel, par_secs = run_sleepers(calls, 4)
    out["parallel_seconds"] = par_secs
    out["parallel_overlap"] = overlapped("s0", "s3")
    out["observation_order"] = [
        e.observation.label
        for e in parallel.state.events
        if isinstance(e, ObservationEvent) and e.tool_name == "sleeper"
    ]
    mixed, _ = run_sleepers(
        [
            sleeper_call("ok1", 0.2),
            sleeper_call("boom", 0.2, fail=True),
            sleeper_call("ok2", 0.2),
        ],
        3,
    )
    out["with_failure"] = {
        "status": mixed.state.execution_status.value,
        "results": [
            f"{type(e).__name__}:"
            f"{
                getattr(getattr(e, 'observation', None), 'label', '')
                or getattr(e, 'tool_name', '')
            }"
            for e in mixed.state.events
            if isinstance(e, ObservationEvent | AgentErrorEvent)
        ],
    }
    # A finish call in the middle of a batch drops the calls after it.
    llm = make_llm(
        [
            {
                "tools": [
                    sleeper_call("before", 0.1),
                    {"tool": "finish", "args": {"message": "stop here"}},
                    sleeper_call("after", 0.1),
                ]
            }
        ]
    )
    TIMELINE.clear()
    agent = Agent(llm=llm, tools=[Tool(name="SleeperTool")], tool_concurrency_limit=2)
    truncated = conversation_for(agent)
    truncated.send_message("Go.")
    truncated.run()
    out["finish_batch"] = {
        "status": truncated.state.execution_status.value,
        "actions": [
            e.tool_name for e in truncated.state.events if isinstance(e, ActionEvent)
        ],
        "ran": [t["label"] for t in TIMELINE],
    }
    try:
        Agent(llm=make_llm([]), tools=[], tool_concurrency_limit=0)
        out["limit_zero_rejected"] = False
    except Exception:
        out["limit_zero_rejected"] = True

elif mode == "locks":
    run_sleepers(
        [
            sleeper_call("a", 0.6, resource="file:/x"),
            sleeper_call("b", 0.6, resource="file:/x"),
        ],
        4,
    )
    out["same_key_overlap"] = overlapped("a", "b")
    run_sleepers(
        [
            sleeper_call("c", 0.6, resource="file:/x"),
            sleeper_call("d", 0.6, resource="file:/y"),
        ],
        4,
    )
    out["different_keys_overlap"] = overlapped("c", "d")
    run_sleepers(
        [
            sleeper_call("e", 0.6, resource="undeclared"),
            sleeper_call("f", 0.6, resource="undeclared"),
        ],
        4,
    )
    out["undeclared_overlap"] = overlapped("e", "f")
    run_sleepers([sleeper_call("g", 0.6), sleeper_call("h", 0.6)], 4)
    out["declared_no_keys_overlap"] = overlapped("g", "h")

elif mode == "tool-exception":
    llm = make_llm([sleeper_call("boom", 0.1, fail=True), {"finish": "carried on"}])
    conversation = conversation_for(Agent(llm=llm, tools=[Tool(name="SleeperTool")]))
    conversation.send_message("Try it.")
    conversation.run()
    errors = [e for e in conversation.state.events if isinstance(e, AgentErrorEvent)]
    out |= {
        "status": conversation.state.execution_status.value,
        "agent_errors": len(errors),
        "error_text": errors[0].error[:200] if errors else None,
        "error_tool": errors[0].tool_name if errors else None,
        "kinds": kinds(conversation),
    }
    conversation.close()

elif mode == "message-while-running":

    class Recording(TestLLM):
        """Records the last user text each LLM call saw."""

        def completion(self, messages, *args, **kwargs):
            users = [m for m in messages if m.role == "user"]
            seen_by_call.append(
                "".join(getattr(c, "text", "") for c in users[-1].content)
                if users
                else ""
            )
            return super().completion(messages, *args, **kwargs)

    seen_by_call: list[str] = []
    sleep = {"tool": "terminal", "args": {"command": "sleep 2 && echo slept"}}
    llm = Recording.from_messages(
        [
            m
            for m in __import__("sdk_verify").script_messages(
                [sleep, {"finish": "handled both"}]
            )
        ],
        usage_id="agent",
    )
    conversation = conversation_for(
        Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    )
    conversation.send_message("Sleep for two seconds.")
    thread = threading.Thread(target=conversation.run)
    thread.start()
    time.sleep(1.0)
    status_mid_run = conversation.state.execution_status.value
    conversation.send_message("Also: remember the word pineapple.")
    thread.join(timeout=60)
    user_texts = [
        e.llm_message.content[0].text
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    ]
    out |= {
        "status_mid_run": status_mid_run,
        "status": conversation.state.execution_status.value,
        "user_messages": user_texts,
        "second_call_saw": seen_by_call[1] if len(seen_by_call) > 1 else None,
        "llm_calls": len(seen_by_call),
    }
    conversation.close()

elif mode == "async":
    in_flight = threading.Event()

    class Slow(TestLLM):
        async def acompletion(self, *args, **kwargs):
            in_flight.set()
            await asyncio.sleep(2.0)
            return await super().acompletion(*args, **kwargs)

    llm = Slow.from_messages(
        __import__("sdk_verify").script_messages(
            [
                {"tool": "terminal", "args": {"command": "echo async"}},
                {"finish": "async done"},
            ]
        ),
        usage_id="agent",
    )
    conversation = conversation_for(
        Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    )
    conversation.send_message("Echo async.")
    thread = threading.Thread(target=lambda: asyncio.run(conversation.arun()))
    thread.start()
    in_flight.wait(timeout=30)
    started = time.monotonic()
    with conversation.state:
        events_mid_call = len(conversation.state.events)
        status_mid_call = conversation.state.execution_status.value
    lock_wait = round(time.monotonic() - started, 2)
    conversation.send_message("Note added during the LLM call.")
    thread.join(timeout=60)
    out |= {
        "lock_wait_seconds": lock_wait,
        "status_mid_call": status_mid_call,
        "events_mid_call": events_mid_call,
        "status": conversation.state.execution_status.value,
        "actions": [
            e.tool_name for e in conversation.state.events if isinstance(e, ActionEvent)
        ],
        "user_messages": sum(
            1
            for e in conversation.state.events
            if isinstance(e, MessageEvent) and e.source == "user"
        ),
    }
    conversation.close()

elif mode == "image-non-vision":
    llm = make_llm([])  # any LLM call would raise: the script is empty
    conversation = conversation_for(
        Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    )
    conversation.send_message(
        Message(
            role="user",
            content=[
                TextContent(text="What is in this image?"),
                ImageContent(image_urls=["https://example.com/cat.png"]),
            ],
        )
    )
    conversation.run()
    replies = [
        e.llm_message.content[0].text
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "agent"
    ]
    out |= {
        "vision_active": llm.vision_is_active(),
        "status": conversation.state.execution_status.value,
        "llm_calls": llm._call_count,
        "agent_reply": replies[0][:300] if replies else None,
    }
    conversation.close()

elif mode == "image-input":
    from PIL import Image

    def png(size: int) -> str:
        buffer = io.BytesIO()
        Image.new("RGB", (size, size), (200, 30, 30)).save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    with FakeProvider([{"finish": "a red square"}]) as server:
        llm = LLM(
            model="openai/gpt-4o-mini",
            base_url=server.url,
            api_key=SecretStr("k"),
            usage_id="agent",
        )
        conversation = conversation_for(Agent(llm=llm, tools=[]))
        conversation.send_message(
            Message(
                role="user",
                content=[
                    TextContent(text="Describe these."),
                    ImageContent(image_urls=["https://example.com/cat.png", png(64)]),
                ],
            )
        )
        conversation.run()
        body = server.requests[0]["body"]
        urls = [
            p["image_url"]["url"][:22]
            for m in body["messages"]
            if isinstance(m.get("content"), list)
            for p in m["content"]
            if p.get("type") == "image_url"
        ]
        out["openai"] = {
            "vision_active": llm.vision_is_active(),
            "status": conversation.state.execution_status.value,
            "image_urls": urls,
        }
        conversation.close()

    big = png(2600)
    with FakeProvider([{"text": "many squares"}]) as server:
        llm = LLM(
            model="anthropic/claude-sonnet-4-5-20250929",
            base_url=server.root,
            api_key=SecretStr("k"),
            usage_id="agent",
        )
        llm.completion(
            [
                Message(role="system", content=[TextContent(text="Describe.")]),
                Message(
                    role="user",
                    content=[
                        TextContent(text="21 images"),
                        ImageContent(image_urls=[big] * 21),
                    ],
                ),
            ]
        )
        body = server.requests[0]["body"]
        sizes = []
        for m in body["messages"]:
            for part in m.get("content") if isinstance(m.get("content"), list) else []:
                if part.get("type") == "image":
                    data = base64.b64decode(part["source"]["data"])
                    sizes.append(max(Image.open(io.BytesIO(data)).size))
        out["anthropic_21_images"] = {
            "images": len(sizes),
            "max_side": max(sizes) if sizes else None,
        }
    with FakeProvider([{"text": "few squares"}]) as server:
        llm = LLM(
            model="anthropic/claude-sonnet-4-5-20250929",
            base_url=server.root,
            api_key=SecretStr("k"),
            usage_id="agent",
        )
        llm.completion(
            [
                Message(role="system", content=[TextContent(text="Describe.")]),
                Message(
                    role="user",
                    content=[
                        TextContent(text="2 images"),
                        ImageContent(image_urls=[big] * 2),
                    ],
                ),
            ]
        )
        body = server.requests[0]["body"]
        sizes = [
            max(Image.open(io.BytesIO(base64.b64decode(p["source"]["data"]))).size)
            for m in body["messages"]
            for p in (m.get("content") if isinstance(m.get("content"), list) else [])
            if p.get("type") == "image"
        ]
        out["anthropic_2_images"] = {
            "images": len(sizes),
            "max_side": max(sizes) if sizes else None,
        }

elif mode == "ask-agent":
    from openhands.sdk.llm.exceptions import LLMContextWindowExceedError

    asked: list[int] = []

    class Answering(TestLLM):
        """Answers side questions itself; the agent's turns come from the script.
        The first agent turn is slow, so a question can arrive mid-run."""

        def completion(self, messages, *args, **kwargs):
            last = "".join(getattr(c, "text", "") for c in messages[-1].content)
            if "SIDE-QUESTION" in last:
                asked.append(len(messages))
                if "overflow" in last:
                    raise LLMContextWindowExceedError("context too long")
                return (
                    super()
                    .completion(messages, *args, **kwargs)
                    .model_copy(
                        update={
                            "message": Message(
                                role="assistant",
                                content=[
                                    TextContent(
                                        text=f"answer from {len(messages)} messages"
                                    )
                                ],
                            )
                        }
                    )
                )
            if self._call_count == 0:
                time.sleep(1.5)
            return super().completion(messages, *args, **kwargs)

    script = __import__("sdk_verify").script_messages(
        [{"tool": "terminal", "args": {"command": "echo ask"}}, {"finish": "done"}]
    )
    # Side questions consume a placeholder each (the reply text is replaced).
    placeholder = Message(role="assistant", content=[TextContent(text="")])
    llm = Answering.from_messages(
        [placeholder, *script, placeholder, placeholder], usage_id="agent"
    )
    llm._scripted_responses.popleft()  # keep the agent turns first
    conversation = conversation_for(
        Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    )
    conversation.send_message("Echo ask.")
    thread = threading.Thread(target=conversation.run)
    thread.start()
    time.sleep(0.5)
    events_before = len(conversation.state.events)
    llm._scripted_responses.appendleft(placeholder)
    mid = conversation.ask_agent("SIDE-QUESTION: what are you doing?")
    out["during_run"] = {
        "answer": mid,
        "status": conversation.state.execution_status.value,
    }
    thread.join(timeout=60)
    status_after = conversation.state.execution_status.value
    events_after = len(conversation.state.events)
    after = conversation.ask_agent("SIDE-QUESTION: what did you run?")
    out["after_run"] = {
        "answer": after,
        "events_added": len(conversation.state.events) - events_after,
        "status_before": status_after,
        "status_after": conversation.state.execution_status.value,
    }
    out["no_side_question_in_events"] = not any(
        "SIDE-QUESTION" in str(e) for e in conversation.state.events
    )
    try:
        conversation.ask_agent("SIDE-QUESTION overflow")
        out["context_error_raised"] = None
    except Exception as exc:
        out["context_error_raised"] = type(exc).__name__
    out["run_actions"] = [
        e.tool_name for e in conversation.state.events if isinstance(e, ActionEvent)
    ]
    out["registry"] = conversation.llm_registry.list_usage_ids()
    conversation.close()

elif mode == "structured-output":
    from openhands.sdk.tool import register_tool
    from openhands.sdk.tool.builtins.finish import FinishTool

    class CommandRationale(BaseModel):
        purpose: str = Field(description="Why this command is run.")

    class ProjectFacts(BaseModel):
        description: str = Field(description="One line about the project.")
        facts: list[str] = Field(description="Three facts.")

    register_tool("FinishTool", FinishTool)
    llm = make_llm(
        [
            {"tool": "terminal", "args": {"command": "ls", "purpose": "see the files"}},
            {
                "tool": "finish",
                "args": {
                    "message": "done",
                    "description": "A demo project.",
                    "facts": ["one", "two", "three"],
                },
            },
        ]
    )
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name=TerminalTool.name, params={"response_schema": CommandRationale}),
            Tool(name="FinishTool", params={"response_schema": ProjectFacts}),
        ],
        include_default_tools=["ThinkTool"],
    )
    conversation = conversation_for(agent)
    conversation.send_message("Inspect and report.")
    conversation.run()
    events = list(conversation.state.events)
    terminal = agent.tools_map[TerminalTool.name]
    finish = agent.tools_map["finish"]
    facts = finish.parse_last_response(events)
    rationale = terminal.parse_last_response(events)
    out |= {
        "status": conversation.state.execution_status.value,
        "facts_type": type(facts).__name__,
        "facts": facts.facts if facts else None,
        "rationale_type": type(rationale).__name__,
        "rationale_purpose": getattr(rationale, "purpose", None),
        "finish_schema_fields": sorted(
            finish.to_openai_tool()["function"]["parameters"]["properties"]
        ),
    }
    conversation.close()

    class Reserved(BaseModel):
        summary: str

    class Collides(BaseModel):
        command: str

    rejected = {}
    for name, schema in (("reserved_name", Reserved), ("field_collision", Collides)):
        try:
            bad = Agent(
                llm=make_llm([{"finish": "x"}]),
                tools=[
                    Tool(name=TerminalTool.name, params={"response_schema": schema})
                ],
            )
            probe = conversation_for(bad)
            probe.send_message("x")
            probe.run()
            rejected[name] = False
            probe.close()
        except Exception as exc:
            rejected[name] = type(exc).__name__
    out["rejected"] = rejected

elif mode == "context-overflow":
    from openhands.sdk.context.condenser import LLMSummarizingCondenser
    from openhands.sdk.event import CondensationRequest
    from openhands.sdk.llm.exceptions import LLMContextWindowExceedError

    def overflow_run(condenser) -> dict:
        llm = TestLLM.from_messages(
            [LLMContextWindowExceedError("context too long")]
            + __import__("sdk_verify").script_messages([{"finish": "fits now"}]),
            usage_id="agent",
        )
        conversation = conversation_for(Agent(llm=llm, tools=[], condenser=condenser))
        conversation.send_message("Long task.")
        try:
            conversation.run()
            raised = None
        except Exception as exc:
            raised = type(exc).__name__
        result = {
            "status": conversation.state.execution_status.value,
            "raised": raised,
            "condensation_requests": sum(
                isinstance(e, CondensationRequest) for e in conversation.state.events
            ),
            "codes": error_codes(conversation),
        }
        conversation.close()
        return result

    summarizer = make_llm(
        [{"text": "Summary of earlier work."}] * 3, usage_id="condenser"
    )
    out["with_condenser"] = overflow_run(
        LLMSummarizingCondenser(llm=summarizer, max_size=40, keep_first=1)
    )
    out["without_condenser"] = overflow_run(None)

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
