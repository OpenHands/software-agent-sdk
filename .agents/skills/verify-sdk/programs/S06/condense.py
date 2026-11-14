"""Condensers driven through real conversations.

Usage: condense.py MODE

  size      LLMSummarizingCondenser(max_size=10, keep_first=2) over a long
            scripted run (with a parallel tool batch); summaries come from a
            separate scripted LLM that has stream=True and an extra body set.
  tokens    max_tokens set just above the system prompt; large command
            outputs push the view over it.
  manual    conversation.condense() with a summarizing, a no-op and no
            condenser.
  overflow  the agent's LLM raises a context-window error, with and without
            a condenser that handles requests.
  hard      summarizing fails: the condenser falls back to a hard context
            reset (retries, then gives up).
  soft      size pressure with nothing worth forgetting (minimum_progress)
            leaves the run alone; an explicit request resets hard.
  reasons   condensation reasons and their combination, on a View taken from
            a real conversation (the API a custom condenser author uses).
  pipeline  PipelineCondenser and NoOpCondenser inside conversations.
  resume    a condensed conversation is reopened and keeps going from the
            summary.
  live      the run's real model works through several commands with a
            real summarizing LLM (max_size=10), then is condensed on request.

Prints facts only; the recipe's checks judge them.
"""

import sys
import uuid

from recorder import calls, recording_llm, text
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.context.condenser import (
    LLMSummarizingCondenser,
    NoOpCondenser,
    PipelineCondenser,
)
from openhands.sdk.context.view import View
from openhands.sdk.event import (
    ActionEvent,
    Condensation,
    CondensationRequest,
    CondensationSummaryEvent,
    ObservationEvent,
)
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()


def echo_steps(n, command="echo step {i}"):
    return [
        {"tool": "terminal", "args": {"command": command.format(i=i)}} for i in range(n)
    ]


def parallel_step():
    return {
        "tools": [
            {"tool": "terminal", "args": {"command": "echo left"}},
            {"tool": "terminal", "args": {"command": "echo right"}},
        ]
    }


def steps(summaries):
    return [{"text": s} if isinstance(s, str) else s for s in summaries]


def summarizer(summaries, usage_id="condenser", **kwargs):
    llm = recording_llm(steps(summaries), usage_id=usage_id)
    defaults = {"max_size": 10, "keep_first": 2}
    return LLMSummarizingCondenser(llm=llm, **{**defaults, **kwargs})


def conversation_for(script, condenser=None, conversation_id=None, persist=True):
    agent = Agent(
        llm=recording_llm(script),
        tools=[Tool(name=TerminalTool.name)],
        condenser=condenser,
    )
    options = {"persistence_dir": str(paths["conversations"])} if persist else {}
    if conversation_id is not None:
        options["conversation_id"] = conversation_id
    return Conversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        visualizer=None,
        max_iteration_per_run=60,
        **options,
    )


def run(conversation, message="go"):
    error = None
    try:
        conversation.send_message(message)
        conversation.run()
    except Exception as exc:
        error = type(exc).__name__
        print(f"run raised {error}: {str(exc)[:800]}", file=sys.stderr)
    return error


def condensations(conversation):
    return [e for e in conversation.state.events if isinstance(e, Condensation)]


def atomic(conversation):
    """No condensation splits an action from its observation or a batch."""
    events = list(conversation.state.events)
    actions = [e for e in events if isinstance(e, ActionEvent)]
    observations = {
        e.action_id: e.id for e in events if isinstance(e, ObservationEvent)
    }
    for cond in condensations(conversation):
        gone = cond.forgotten_event_ids
        for action in actions:
            obs = observations.get(action.id)
            if obs is not None and (action.id in gone) != (obs in gone):
                return False
        batches = {}
        for action in actions:
            batches.setdefault(action.llm_response_id, []).append(action.id in gone)
        if any(len(set(flags)) > 1 for flags in batches.values()):
            return False
    return True


def view_facts(conversation):
    view = conversation.state.view
    kinds = [type(e).__name__ for e in view.events]
    return {
        "view_len": len(view),
        "view_kinds_head": kinds[:3],
        "view_summaries": kinds.count("CondensationSummaryEvent"),
        "view_has_request": any(
            isinstance(e, CondensationRequest) for e in view.events
        ),
    }


def summary_reached_agent():
    """Did the agent LLM see a summary after the first condensation?"""
    return any("SUMMARY-" in text(m) for c in calls("agent") for m in c["messages"])


out = {"ok": True, "mode": mode}

if mode == "size":
    # A streaming summary LLM with an extra body, as a provider config might have.
    llm = recording_llm(
        steps(["SUMMARY-1: echoed steps 0-3.", "SUMMARY-2: more steps.", "SUMMARY-3."]),
        usage_id="condenser",
        stream=True,
        litellm_extra_body={"metadata": {"tag": "x"}},
    )
    condenser = LLMSummarizingCondenser(llm=llm, max_size=10, keep_first=2)
    script = (
        echo_steps(3)
        + [parallel_step()]
        + echo_steps(6, "echo later {i}")
        + [{"finish": "done"}]
    )
    conversation = conversation_for(script, condenser)
    out["error"] = run(conversation)
    conds = condensations(conversation)
    events = list(conversation.state.events)
    first = conds[0] if conds else None
    out["status"] = conversation.state.execution_status.value
    out["condensations"] = len(conds)
    out["first_forgotten"] = len(first.forgotten_event_ids) if first else 0
    out["first_summary"] = first.summary if first else None
    out["first_offset"] = first.summary_offset if first else None
    out["kept_first_two"] = bool(first) and not (
        {events[0].id, events[1].id} & first.forgotten_event_ids
    )
    out["atomic"] = atomic(conversation)
    out.update(view_facts(conversation))
    out["forgotten_in_view"] = any(
        e.id in c.forgotten_event_ids
        for c in conds
        for e in conversation.state.view.events
    )
    out["summary_reached_agent"] = summary_reached_agent()
    summary_calls = calls("condenser")
    out["summary_calls"] = len(summary_calls)
    out["summary_call_streamed"] = any(c["stream"] for c in summary_calls)
    out["summary_call_kwargs"] = sorted({k for c in summary_calls for k in c["kwargs"]})
    out["summary_roles"] = (
        [m.role for m in summary_calls[0]["messages"]] if summary_calls else []
    )
    out["second_builds_on_first"] = len(summary_calls) > 1 and "SUMMARY-1" in text(
        summary_calls[1]["messages"][-1]
    )
    out["summary_usage_ids"] = sorted({c["usage_id"] for c in summary_calls})
    conversation.close()

elif mode == "tokens":
    probe = Agent(llm=recording_llm([]), tools=[Tool(name=TerminalTool.name)])
    from openhands.sdk.llm import Message, TextContent

    base = probe.llm.get_token_count(
        [
            Message(
                role="system", content=[TextContent(text=probe.static_system_message)]
            )
        ]
    )
    max_tokens = base * 3
    condenser = summarizer(
        [f"SUMMARY-T{i}." for i in range(8)], max_tokens=max_tokens, max_size=200
    )
    script = echo_steps(10, "seq 1 700 | tr '\\n' ' ' # {i}") + [{"finish": "done"}]
    conversation = conversation_for(script, condenser)
    out["error"] = run(conversation)
    conds = condensations(conversation)
    from openhands.sdk.context.condenser.utils import get_total_token_count

    view = conversation.state.view
    out["status"] = conversation.state.execution_status.value
    out["max_tokens"] = max_tokens
    out["condensations"] = len(conds)
    out["events_total"] = len(conversation.state.events)
    out["view_tokens"] = get_total_token_count(view.events, conversation.agent.llm)
    out["view_under_max_tokens"] = out["view_tokens"] <= max_tokens
    out["atomic"] = atomic(conversation)
    out["summary_reached_agent"] = summary_reached_agent()
    conversation.close()

elif mode == "manual":
    script = echo_steps(4) + [{"finish": "done"}, {"text": "after condense"}]
    conversation = conversation_for(script, summarizer(["SUMMARY-M."], max_size=100))
    run(conversation)
    before = len(conversation.state.view)
    conversation.condense()
    kinds = [type(e).__name__ for e in conversation.state.events]
    out["request_events"] = kinds.count("CondensationRequest")
    out["condensations"] = len(condensations(conversation))
    out["view_before"] = before
    out["view_after"] = len(conversation.state.view)
    out["request_in_view"] = view_facts(conversation)["view_has_request"]
    out["summary_calls"] = len(calls("condenser"))
    out["agent_calls_during_condense"] = len(calls("agent")) - 5
    out["error_after"] = run(conversation, "and now?")
    out["summary_reached_agent"] = summary_reached_agent()
    conversation.close()
    for name, condenser in [("none", None), ("noop", NoOpCondenser())]:
        other = conversation_for([{"text": "hi"}], condenser, persist=False)
        run(other)
        try:
            other.condense()
            out[f"{name}_error"] = None
        except ValueError as exc:
            out[f"{name}_error"] = str(exc)[:300]
        other.close()

elif mode == "overflow":
    for name, condenser in [
        ("with", summarizer(["SUMMARY-O."], max_size=100)),
        ("without", None),
    ]:
        script = echo_steps(3) + [
            {"error": "LLMContextWindowExceedError", "message": "context too long"},
            {"finish": "done"},
        ]
        conversation = conversation_for(script, condenser)
        out[f"{name}_error"] = run(conversation)
        kinds = [type(e).__name__ for e in conversation.state.events]
        out[f"{name}_status"] = conversation.state.execution_status.value
        out[f"{name}_requests"] = kinds.count("CondensationRequest")
        out[f"{name}_condensations"] = kinds.count("Condensation")
        out[f"{name}_error_events"] = [
            e.code
            for e in conversation.state.events
            if type(e).__name__ == "ConversationErrorEvent"
        ]
        conversation.close()
    out["summary_reached_agent"] = summary_reached_agent()

elif mode == "hard":
    # Summaries fail twice: the normal path, then the first hard-reset attempt.
    condenser = summarizer(
        [
            {"error": "LLMBadRequestError", "message": "summary failed"},
            {"error": "LLMBadRequestError", "message": "still too long"},
            "SUMMARY-H: reset.",
        ],
        max_size=100,
    )
    # One output larger than the system prompt, so the retry's 0.8 cap bites.
    script = (
        echo_steps(3)
        + echo_steps(1, "seq 1 4000 # {i}")
        + [
            {"finish": "done"},
            {"text": "after reset"},
        ]
    )
    conversation = conversation_for(script, condenser)
    run(conversation)
    conversation.condense()
    conds = condensations(conversation)
    events = list(conversation.state.events)
    view = conversation.state.view
    summary_calls = calls("condenser")
    lengths = [len(text(c["messages"][-1])) for c in summary_calls]
    out["summary_calls"] = len(summary_calls)
    out["summary_prompt_lengths"] = lengths
    out["retry_shrank"] = len(lengths) == 3 and lengths[2] < lengths[1]
    out["condensations"] = len(conds)
    out["summary"] = conds[-1].summary if conds else None
    out["offset"] = conds[-1].summary_offset if conds else None
    out["view_kinds"] = [type(e).__name__ for e in view.events]
    first_llm_event = next(e for e in events if type(e).__name__ == "SystemPromptEvent")
    out["system_prompt_kept"] = (
        bool(conds) and first_llm_event.id not in conds[-1].forgotten_event_ids
    )
    conversation.close()
    # Every attempt fails: condense() raises instead of losing the history.
    failing = summarizer(
        [{"error": "LLMBadRequestError", "message": "no"}] * 3,
        usage_id="failing",
        max_size=100,
        hard_context_reset_max_retries=2,
    )
    conversation = conversation_for(echo_steps(2) + [{"finish": "done"}], failing)
    run(conversation)
    try:
        conversation.condense()
        out["exhausted_error"] = None
    except Exception as exc:
        out["exhausted_error"] = type(exc).__name__
    out["exhausted_calls"] = len(calls("failing"))
    out["exhausted_condensations"] = len(condensations(conversation))
    conversation.close()

elif mode == "soft":
    condenser = summarizer(["SUMMARY-S."], minimum_progress=0.95)
    script = echo_steps(8) + [{"finish": "done"}, {"text": "after"}]
    conversation = conversation_for(script, condenser)
    out["error"] = run(conversation)
    out["status"] = conversation.state.execution_status.value
    out["events"] = len(conversation.state.events)
    out["condensations_soft"] = len(condensations(conversation))
    out["summary_calls_soft"] = len(calls("condenser"))
    conversation.condense()
    conds = condensations(conversation)
    out["condensations_after_request"] = len(conds)
    out["view_kinds_after_request"] = [
        type(e).__name__ for e in conversation.state.view.events
    ]
    out["hard_offset"] = conds[-1].summary_offset if conds else None
    conversation.close()

elif mode == "reasons":
    script = echo_steps(12) + [{"finish": "done"}]
    conversation = conversation_for(script, None)
    run(conversation)
    agent_llm = conversation.agent.llm
    view = conversation.state.view
    with_request = View.from_events([*conversation.state.events, CondensationRequest()])
    from openhands.sdk.context.condenser.utils import get_total_token_count

    tokens = get_total_token_count(view.events, agent_llm)
    out["view_len"] = len(view)
    out["request_view_len"] = len(with_request)
    out["request_view_unhandled"] = with_request.unhandled_condensation_request
    out["request_view_has_request"] = any(
        isinstance(e, CondensationRequest) for e in with_request.events
    )
    cases = {
        "request": (with_request, {"max_size": 200}),
        "events": (view, {"max_size": 20}),
        "request_events": (with_request, {"max_size": 20}),
        "request_events_tokens": (
            with_request,
            {"max_size": 20, "max_tokens": int(tokens * 0.9)},
        ),
    }
    for name, (v, kwargs) in cases.items():
        condenser = summarizer([f"SUMMARY-{name}."], usage_id=name, **kwargs)
        reasons = condenser.get_condensation_reasons(v, agent_llm)
        result = condenser.condense(v, agent_llm)
        out[f"{name}_reasons"] = sorted(r.value for r in reasons)
        out[f"{name}_forgotten"] = (
            len(result.forgotten_event_ids) if isinstance(result, Condensation) else 0
        )
    out["most_aggressive_wins"] = (
        out["request_events_forgotten"]
        >= max(out["request_forgotten"], out["events_forgotten"])
        and out["request_events_tokens_forgotten"] >= out["request_events_forgotten"]
    )
    picky = summarizer(["unused"], usage_id="picky", max_size=20, minimum_progress=0.9)
    result = picky.condense(view, agent_llm)
    out["minimum_progress_result"] = type(result).__name__
    out["minimum_progress_summary_calls"] = len(calls("picky"))
    try:
        LLMSummarizingCondenser(llm=recording_llm([]), max_size=10, keep_first=4)
        out["keep_first_too_big_error"] = None
    except Exception as exc:
        out["keep_first_too_big_error"] = type(exc).__name__
    noop = NoOpCondenser()
    out["noop_same_view"] = (
        noop.condense(view) is view and noop.condense(view, agent_llm) is view
    )
    conversation.close()

elif mode == "pipeline":
    pipeline = PipelineCondenser(
        condensers=[
            NoOpCondenser(),
            summarizer([f"SUMMARY-P{i}." for i in range(5)]),
            summarizer(["SECOND-SUMMARY."], usage_id="second"),
        ]
    )
    out["pipeline_handles_requests"] = pipeline.handles_condensation_requests()
    out["noop_only_pipeline_handles_requests"] = PipelineCondenser(
        condensers=[NoOpCondenser()]
    ).handles_condensation_requests()
    script = echo_steps(8) + [{"finish": "done"}, {"text": "after"}]
    conversation = conversation_for(script, pipeline)
    out["pipeline_error"] = run(conversation)
    out["pipeline_status"] = conversation.state.execution_status.value
    out["pipeline_condensations"] = len(condensations(conversation))
    out["pipeline_atomic"] = atomic(conversation)
    out["pipeline_second_member_calls"] = len(calls("second"))
    conversation.condense()
    out["pipeline_condensations_after_request"] = len(condensations(conversation))
    conversation.close()
    noop = conversation_for(echo_steps(8) + [{"finish": "done"}], NoOpCondenser())
    out["noop_error"] = run(noop)
    out["noop_status"] = noop.state.execution_status.value
    out["noop_condensations"] = len(condensations(noop))
    out["noop_view_len"] = len(noop.state.view)
    noop.close()

elif mode == "resume":
    cid = uuid.uuid4()
    script = echo_steps(8) + [{"finish": "done"}]
    conversation = conversation_for(
        script, summarizer(["SUMMARY-R1.", "SUMMARY-R2."]), cid
    )
    run(conversation)
    view_ids = [e.id for e in conversation.state.view.events]
    out["condensations"] = len(condensations(conversation))
    conversation.close()
    reopened = conversation_for([{"text": "resumed"}], summarizer(["SUMMARY-R3."]), cid)
    reopened_ids = [e.id for e in reopened.state.view.events]
    rebuilt = View.from_events(list(reopened.state.events))
    out["reopened_view_matches"] = reopened_ids == view_ids
    out["rebuilt_view_matches"] = [e.id for e in rebuilt.events] == reopened_ids
    out["reopened_summaries"] = sum(
        isinstance(e, CondensationSummaryEvent) for e in reopened.state.view.events
    )
    calls_before = len(calls("agent"))
    out["error_after_resume"] = run(reopened, "what did you do?")
    after = calls("agent")[calls_before:]
    out["resumed_call_has_summary"] = bool(after) and any(
        "SUMMARY-R" in text(m) for m in after[0]["messages"]
    )
    out["resumed_call_messages"] = len(after[0]["messages"]) if after else 0
    out["resumed_status"] = reopened.state.execution_status.value
    reopened.close()

elif mode == "live":
    condenser = LLMSummarizingCondenser(
        llm=make_llm(usage_id="condenser"), max_size=10, keep_first=2
    )
    agent = Agent(
        llm=make_llm(), tools=[Tool(name=TerminalTool.name)], condenser=condenser
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        visualizer=None,
        max_iteration_per_run=40,
    )
    out["error"] = run(
        conversation,
        "Run these terminal commands one at a time, each as its own tool call: "
        "echo one, echo two, echo three, echo four, echo five, echo six, echo seven. "
        "Then reply with the word done.",
    )
    conds = condensations(conversation)
    out["status"] = conversation.state.execution_status.value
    out["condensations"] = len(conds)
    out["summary_nonempty"] = bool(conds) and bool((conds[0].summary or "").strip())
    out["atomic"] = atomic(conversation)
    conversation.condense()
    out["condensations_after_request"] = len(condensations(conversation))
    conversation.close()

else:
    raise SystemExit(f"unknown mode {mode}")

emit(out)
