"""Read and reshape a conversation the way a program does: visualizers, fork
and branches, the goal loop, the final response, titles and tags, and an
interactive terminal session. The model is a scripted `TestLLM` (or a priced
`LLM` against `S02/fake_provider.py` where cost matters); tools are real.

Usage: views.py MODE
  visualizer   default Rich output, None, a class, an instance, a custom one
  fork         fork(): copy, independence, metrics, agent, title, tags, reload
  branch       parent ids, fork(from_event_id=...), navigate_to(...)
  goal         run_goal with a judge LLM: complete, capped, unparsable verdict
  final        get_agent_final_response for finish, text, nothing, both
  title-tags   generate_title (reasoning, explicit llm, fallback), tags
  interactive  feed input to python3 running in the terminal
"""

import io
import json
import sys
import uuid
from contextlib import redirect_stdout

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.event import MessageEvent, ObservationEvent
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
WORKSPACE = str(paths["workspace"])
BASE = paths["run"] / f"views-{mode}"
out: dict = {"ok": True, "mode": mode}
PRICE = {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}  # 0.14 per call


def attempt(fn):
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:300]


def term(command: str, **extra) -> dict:
    return {"tool": "terminal", "args": {"command": command, **extra}}


def conversation_for(script, **kw):
    kw.setdefault("visualizer", None)
    return Conversation(
        agent=Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)]),
        workspace=WORKSPACE,
        **kw,
    )


if mode == "visualizer":
    from openhands.sdk.conversation.visualizer import (
        ConversationVisualizerBase,
        DefaultConversationVisualizer,
    )

    script = [term("echo shown-in-panel"), {"finish": "visualized"}]

    def printed(**kw) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):  # the console binds stdout when it is created
            conversation = conversation_for(script, **kw)
            conversation.send_message("Echo, then finish.")
            conversation.run()
            conversation.close()
        return buf.getvalue()

    default_text = printed(visualizer=DefaultConversationVisualizer)  # the default
    out |= {
        "default_has_user_panel": "Message from User" in default_text,
        "default_has_observation_panel": "Observation" in default_text,
        "default_shows_command": "shown-in-panel" in default_text,
        "default_uses_boxes": "╭" in default_text or "─" in default_text,
        "none_prints": len(printed(visualizer=None)),
        "instance_skip_user": "Message from User"
        in printed(visualizer=DefaultConversationVisualizer(skip_user_messages=True)),
    }
    buf = io.StringIO()
    with redirect_stdout(buf):
        implicit = Conversation(
            agent=Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)]),
            workspace=WORKSPACE,
        )
        implicit.send_message("Echo, then finish.")
        implicit.run()
        implicit.close()
    out["omitted_prints_panels"] = "Observation" in buf.getvalue()

    seen: list[str] = []
    rendered: dict[str, str] = {}

    class Minimal(ConversationVisualizerBase):
        def on_event(self, event) -> None:
            kind = type(event).__name__
            seen.append(kind)
            rendered.setdefault(kind, event.visualize.plain[:120])

    buf = io.StringIO()
    with redirect_stdout(buf):
        conversation = conversation_for(script, visualizer=Minimal())
        conversation.send_message("Echo, then finish.")
        conversation.run()
        events = len(conversation.state.events)
        conversation.close()
    out |= {
        "custom_events": len(seen),
        "custom_saw_every_event": len(seen) == events,
        "custom_kinds": sorted(set(seen)),
        "custom_printed": len(buf.getvalue()),
        "action_rendering": rendered.get("ActionEvent", ""),
        "observation_rendering": rendered.get("ObservationEvent", ""),
    }

elif mode == "fork":
    with (
        FakeProvider(
            [
                {
                    "tool": "terminal",
                    "args": {
                        "command": "echo source",
                        "summary": "s",
                        "security_risk": "LOW",
                    },
                },
                {"finish": "source done"},
            ]
        ) as server,
        FakeProvider(
            [{"finish": "fork done"}, {"finish": "kept metrics fork done"}]
        ) as fork_server,
    ):

        def priced(srv, model="openai/gpt-4o-mini") -> LLM:
            return LLM(
                model=model,
                base_url=srv.url,
                api_key=SecretStr("sk-test"),
                usage_id="agent",
                num_retries=1,
                **PRICE,
            )

        source = Conversation(
            agent=Agent(llm=priced(server), tools=[Tool(name=TerminalTool.name)]),
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            visualizer=None,
        )
        source.send_message("Echo, then finish.")
        source.run()
        source_events = len(source.state.events)
        source_cost = source.conversation_stats.get_combined_metrics().accumulated_cost
        fork = source.fork(
            title="Follow-up fork",
            tags={"team": "verify"},
            agent=Agent(
                llm=priced(fork_server, "openai/gpt-4.1-mini"),
                tools=[Tool(name=TerminalTool.name)],
            ),
        )
        copied = len(fork.state.events)
        status = fork.state.execution_status.value
        fork_cost_before = (
            fork.conversation_stats.get_combined_metrics().accumulated_cost
        )
        fork.send_message("Continue on the fork.")
        fork.run()
        kept = source.fork(
            reset_metrics=False,
            conversation_id=uuid.UUID(int=7),
            agent=Agent(llm=priced(fork_server), tools=[Tool(name=TerminalTool.name)]),
        )
        out |= {
            "new_id": fork.id != source.id,
            "copied_events": copied,
            "source_events": source_events,
            "fork_status": status,
            "title": fork.state.tags.get("title"),
            "tags": fork.state.tags,
            "fork_model": fork.agent.llm.model,
            "source_model": source.agent.llm.model,
            "source_cost": round(source_cost, 4),
            "fork_cost_before_run": round(fork_cost_before, 4),
            "kept_metrics_cost": round(
                kept.conversation_stats.get_combined_metrics().accumulated_cost, 4
            ),
            "chosen_id_kept": kept.id == uuid.UUID(int=7),
            "fork_events_after_run": len(fork.state.events),
            "source_events_after_fork_run": len(source.state.events),
            "source_status": source.state.execution_status.value,
            "fork_status_after_run": fork.state.execution_status.value,
            "fork_request_had_source_history": "Echo, then finish."
            in json.dumps(fork_server.requests[0]["body"]),
            "same_workspace": fork.workspace.working_dir
            == source.workspace.working_dir,
            "fork_on_disk": (BASE / fork.id.hex / "base_state.json").is_file(),
        }
        fork_id, fork_events = fork.id, len(fork.state.events)
        for conv in (fork, kept, source):
            conv.close()
        reloaded = Conversation(
            agent=Agent(
                llm=priced(fork_server, "openai/gpt-4.1-mini"),
                tools=[Tool(name=TerminalTool.name)],
            ),
            workspace=WORKSPACE,
            persistence_dir=str(BASE),
            conversation_id=fork_id,
            visualizer=None,
        )
        out["reloaded_fork_events"] = len(reloaded.state.events)
        out["reloaded_fork_title"] = reloaded.state.tags.get("title")
        out["fork_events"] = fork_events
        reloaded.close()

elif mode == "branch":
    conversation = conversation_for(
        [
            {"text": "first answer"},
            {"text": "second answer"},
            {"text": "answer on a new branch"},
        ],
        persistence_dir=str(BASE),
    )
    conversation.send_message("First question.")
    conversation.run()
    conversation.send_message("Second question.")
    conversation.run()
    events = list(conversation.state.events)
    linear = all(events[i].parent_id == events[i - 1].id for i in range(1, len(events)))
    first_answer = next(
        e for e in events if isinstance(e, MessageEvent) and e.source == "agent"
    )
    branch_fork = conversation.fork(from_event_id=first_answer.id)
    fork_events = list(branch_fork.state.events)
    out |= {
        "events": len(events),
        "parent_chain_linear": linear,
        "fork_from_event_events": len(fork_events),
        "fork_from_event_last_is_branch_point": fork_events[-1].id == first_answer.id,
        "fork_has_second_question": any(
            isinstance(e, MessageEvent)
            and "Second question." in str(e.llm_message.content)
            for e in fork_events
        ),
    }
    branch_fork.close()
    # Move HEAD back to the first answer and continue: a sibling branch.
    conversation.navigate_to(first_answer.id)
    conversation.send_message("Different second question.")
    conversation.run()
    after = list(conversation.state.events)
    new_question = next(
        e
        for e in after
        if isinstance(e, MessageEvent)
        and "Different second question." in str(e.llm_message.content)
    )
    active = conversation.state.active_branch()
    out |= {
        "events_after_branch": len(after),
        "new_question_parent_is_branch_point": new_question.parent_id
        == first_answer.id,
        "old_branch_still_in_log": any(
            "Second question." in str(getattr(e, "llm_message", "")) for e in after
        ),
        "old_branch_in_active": any(
            isinstance(e, MessageEvent)
            and "Second question." in str(e.llm_message.content)
            and "Different" not in str(e.llm_message.content)
            for e in active
        ),
        "active_ends_with": "".join(c.text for c in active[-1].llm_message.content)
        if isinstance(active[-1], MessageEvent)
        else type(active[-1]).__name__,
    }
    _, out["unknown_event_error"] = attempt(
        lambda: conversation.navigate_to("no-such-event")
    )
    conversation.navigate_to(None)
    out["active_after_none"] = len(conversation.state.active_branch())
    out["log_after_none"] = len(conversation.state.events)
    conversation.close()

elif mode == "goal":
    from openhands.sdk.conversation.goal import run_goal

    def judge(replies):
        return make_llm([{"text": r} for r in replies], usage_id="judge")

    conversation = conversation_for(
        [{"finish": "wrote the code"}, {"finish": "wrote the code and the tests"}]
    )
    outcome = run_goal(
        conversation,
        "Write the code and its tests.",
        judge(
            [
                '{"score": 0.3, "complete": false, "missing": "the unit tests"}',
                'Verdict: {"score": 0.95, "complete": true, "missing": ""}',
            ]
        ),
        max_iterations=4,
    )
    followups = [
        "".join(c.text for c in e.llm_message.content)
        for e in conversation.state.events
        if isinstance(e, MessageEvent) and e.source == "user"
    ]
    out["complete"] = {
        "status": outcome.status,
        "iterations": outcome.iterations,
        "score": outcome.verdict.score,
        "user_messages": len(followups),
        "followup_names_missing": "the unit tests"
        in (followups[1] if len(followups) > 1 else ""),
        "conversation_status": conversation.state.execution_status.value,
    }
    conversation.close()
    conversation = conversation_for([{"finish": "tried"}, {"finish": "tried again"}])
    outcome = run_goal(
        conversation,
        "Do the impossible.",
        judge(
            [
                '{"score": 0.1, "complete": false, "missing": "everything"}',
                "not json at all",
            ]
        ),
        max_iterations=2,
    )
    out["capped"] = {
        "status": outcome.status,
        "iterations": outcome.iterations,
        "last_score": outcome.verdict.score,
        "last_missing": outcome.verdict.missing,
    }
    conversation.close()

elif mode == "final":
    from openhands.sdk.conversation.response_utils import get_agent_final_response

    def final_of(script, messages=("Do it.",)) -> str:
        conversation = conversation_for(script)
        for m in messages:
            conversation.send_message(m)
            conversation.run()
        result = get_agent_final_response(conversation.state.events)
        conversation.close()
        return result

    out |= {
        "finish": final_of([term("echo work"), {"finish": "the finish message"}]),
        "text_reply": final_of([{"text": "a plain text answer"}]),
        "nothing": final_of([], messages=()),
        "finish_then_text": final_of(
            [{"finish": "finished first"}, {"text": "then talked"}],
            messages=("One.", "Two."),
        ),
        "text_then_finish": final_of(
            [{"text": "talked first"}, {"finish": "then finished"}],
            messages=("One.", "Two."),
        ),
    }

elif mode == "title-tags":
    from openhands.sdk.testing import TestLLM

    def titled(reply_llm=None, agent_reply="Agent model title", **kw):
        conversation = conversation_for([{"text": agent_reply}])
        conversation.send_message(
            "Please fix the login bug that breaks password reset emails for new users"
        )
        try:
            return conversation.generate_title(llm=reply_llm, **kw)
        except Exception as exc:
            return f"{type(exc).__name__}: {exc}"[:200]
        finally:
            conversation.close()

    out |= {
        "agent_llm": titled(),
        "reasoning_stripped": titled(
            agent_reply="<think>The user wants a title.</think>Fix login reset emails"
        ),
        "explicit_llm": titled(
            reply_llm=make_llm(
                [{"text": "Title from the explicit LLM"}], usage_id="titler"
            )
        ),
        "fallback_on_error": titled(
            reply_llm=TestLLM.from_messages([RuntimeError("model down")], usage_id="t")
        ),
        "fallback_max_length": titled(
            reply_llm=TestLLM.from_messages([RuntimeError("down")], usage_id="t"),
            max_length=20,
        ),
        "only_reasoning": titled(agent_reply="<think>no title here"),
    }
    empty = conversation_for([])
    _, out["no_user_message"] = attempt(empty.generate_title)
    empty.close()
    tagged = conversation_for(
        [], persistence_dir=str(BASE), tags={"env": "verify", "team2": "sdk"}
    )
    out["tags"] = tagged.state.tags
    out["tags_on_disk"] = json.loads(
        (BASE / tagged.id.hex / "base_state.json").read_text()
    ).get("tags")
    tagged.close()
    for bad in ({"Env": "x"}, {"team-name": "x"}, {"ok": "x" * 300}):
        _, err = attempt(lambda: conversation_for([], tags=bad))
        out.setdefault("bad_tags", []).append(err)

elif mode == "interactive":
    from openhands.sdk.event import LLMConvertibleEvent

    ASK = 'python3 -c \'n = input("name? "); print("hello", n, 6 * 7)\''

    def session(steps: list[dict]) -> dict:
        messages: list[dict] = []

        def collect(event) -> None:
            if isinstance(event, LLMConvertibleEvent):
                msg = event.to_llm_message()
                messages.append(
                    {
                        "role": msg.role,
                        "text": "".join(getattr(c, "text", "") for c in msg.content),
                    }
                )

        conversation = Conversation(
            agent=Agent(
                llm=make_llm([*steps, {"finish": "interactive done"}]),
                tools=[
                    Tool(
                        name=TerminalTool.name, params={"no_change_timeout_seconds": 3}
                    )
                ],
            ),
            workspace=WORKSPACE,
            callbacks=[collect],
            visualizer=None,
        )
        conversation.send_message("Run the program and answer its prompt.")
        conversation.run()
        obs = [
            "".join(getattr(c, "text", "") for c in e.observation.to_llm_content)
            for e in conversation.state.events
            if isinstance(e, ObservationEvent)
        ]
        conversation.close()
        return {
            "status": conversation.state.execution_status.value,
            "waiting": bool(obs) and "no new output after 3 seconds" in obs[0],
            "reply": obs[1][:200] if len(obs) > 1 else None,
            "collected_roles": sorted({m["role"] for m in messages}),
            "collected_tool_messages": sum(m["role"] == "tool" for m in messages),
        }

    # The Python REPL: start it, then type into it (as the tool's description
    # says: "send other commands to interact with the current process").
    out["repl"] = session(
        [
            term("python3"),
            term("print(6 * 7)"),
            term("exit()"),
            term("echo back-in-shell"),
        ]
    )
    # A program waiting on input(), answered with is_input=True (the field the
    # terminal tool documents for "an input to the running process") ...
    out["prompt_is_input"] = session([term(ASK), term("John", is_input=True)])
    # ... and answered as a plain command.
    out["prompt_plain"] = session([term(ASK), term("John")])

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
