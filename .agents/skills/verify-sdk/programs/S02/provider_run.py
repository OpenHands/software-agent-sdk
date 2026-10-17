"""Run a two-step agent task through a real `LLM` against a local
OpenAI-compatible (or Anthropic) endpoint, and report what the SDK sent and
recorded. The endpoint answers from a script, so the SDK's own provider path
(LiteLLM request building, parsing, streaming, metrics, logs) is what runs.

Usage: provider_run.py chat|responses|prompted|anthropic|stream
"""

import json
import sys

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.event import ActionEvent, ObservationEvent
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
paths = run_paths()
log_dir = paths["run"] / "completions"
ECHO = {"command": "echo provider-run", "summary": "echo", "security_risk": "LOW"}

if mode == "prompted":
    first = {
        "text": "I will echo.\n<function=terminal>\n"
        "<parameter=command>echo provider-run"
        "</parameter>\n<parameter=summary>echo</parameter>\n"
        "<parameter=security_risk>LOW</parameter>\n</function>"
    }
    second = {
        "text": "<function=finish>\n<parameter=message>done</parameter>\n"
        "<parameter=summary>finish</parameter>\n</function>"
    }
else:
    first = {"text": "Echoing.", "tool": "terminal", "args": ECHO}
    second = {"finish": "done"}
if mode == "responses":
    first |= {"reasoning": "plan the echo", "encrypted": "ENC-1"}
if mode in ("stream", "chat"):
    first |= {"reasoning": "think about echo"}
if mode == "anthropic":
    first |= {"thinking": "considering the echo"}

common = {"api_key": SecretStr("sk-test"), "usage_id": "agent", "num_retries": 1}
tokens: list = []
with FakeProvider([first, second]) as server:
    if mode == "chat":
        llm = LLM(
            model="openai/gpt-4o-mini",
            base_url=server.url,
            temperature=0.3,
            top_p=0.9,
            max_output_tokens=123,
            seed=7,
            extra_headers={"X-Verify": "1"},
            litellm_extra_body={"verify_extra": "yes"},
            log_completions=True,
            log_completions_folder=str(log_dir),
            input_cost_per_token=0.001,
            output_cost_per_token=0.002,
            **common,
        )
    elif mode == "responses":
        llm = LLM(
            model="openai/gpt-5-mini",
            base_url=server.url,
            api_mode="responses",
            reasoning_effort="low",
            reasoning_summary="auto",
            **common,
        )
    elif mode == "prompted":
        llm = LLM(
            model="openai/gpt-4o-mini",
            base_url=server.url,
            native_tool_calling=False,
            **common,
        )
    elif mode == "anthropic":
        llm = LLM(
            model="anthropic/claude-sonnet-4-5-20250929",
            base_url=server.root,
            extended_thinking_budget=200000,
            max_output_tokens=4096,
            **common,
        )
    else:
        llm = LLM(
            model="openai/gpt-4o-mini", base_url=server.url, stream=True, **common
        )

    agent = Agent(llm=llm, tools=[Tool(name=TerminalTool.name)])
    conversation = Conversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        visualizer=None,
        token_callbacks=[tokens.append] if mode == "stream" else None,
    )
    conversation.send_message("Echo provider-run, then finish.")
    conversation.run()
    events = list(conversation.state.events)
    stats = conversation.conversation_stats.usage_to_metrics
    requests = server.requests
    conversation.close()

actions = [e for e in events if isinstance(e, ActionEvent)]
observations = [e for e in events if isinstance(e, ObservationEvent)]
body1 = requests[0]["body"] if requests else {}
body2 = requests[1]["body"] if len(requests) > 1 else {}
metrics = stats.get("agent")
text1 = json.dumps(body1)


def tool_names(body: dict) -> list[str]:
    return sorted(
        t.get("function", {}).get("name") or t.get("name", "")
        for t in body.get("tools", [])
    )


def delta_kinds() -> list[str]:
    kinds = set()
    for chunk in tokens:
        for choice in getattr(chunk, "choices", []) or []:
            delta = choice.delta
            if getattr(delta, "content", None):
                kinds.add("content")
            if getattr(delta, "reasoning_content", None):
                kinds.add("reasoning")
            if getattr(delta, "tool_calls", None):
                kinds.add("tool_call")
    return sorted(kinds)


result = {
    "ok": True,
    "mode": mode,
    "execution_status": conversation.state.execution_status.value,
    "requests": len(requests),
    "paths": sorted({r["path"] for r in requests}),
    "actions": [a.tool_name for a in actions],
    "observation_text": "".join(
        c.text
        for o in observations
        for c in o.observation.to_llm_content
        if hasattr(c, "text")
    )[:200],
    "uses_responses_api": llm.uses_responses_api(),
    "request_tools": tool_names(body1),
    "first_body_keys": sorted(body1),
}
if metrics is not None:
    result |= {
        "accumulated_cost": round(metrics.accumulated_cost, 6),
        "costs": len(metrics.costs),
        "token_usages": len(metrics.token_usages),
        "response_latencies": len(metrics.response_latencies),
        "prompt_tokens": metrics.accumulated_token_usage.prompt_tokens
        if metrics.accumulated_token_usage
        else None,
    }
if mode == "chat":
    result |= {
        "sent": {
            k: body1.get(k)
            for k in (
                "temperature",
                "top_p",
                "max_completion_tokens",
                "seed",
                "verify_extra",
            )
        },
        "header_forwarded": requests[0]["headers"].get("x-verify") == "1",
        "log_files": len(list(log_dir.glob("*.json"))) if log_dir.is_dir() else 0,
        "reasoning_content_on_action": actions[0].reasoning_content
        if actions
        else None,
    }
if mode == "responses":
    replayed = [i for i in body2.get("input", []) if i.get("type") == "reasoning"]
    item = actions[0].responses_reasoning_item if actions else None
    result |= {
        "reasoning_sent": body1.get("reasoning"),
        "include": body1.get("include"),
        "replayed_reasoning": len(replayed),
        "replayed_encrypted": [i.get("encrypted_content") for i in replayed],
        "action_reasoning_summary": item.summary if item else None,
        "action_encrypted": item.encrypted_content if item else None,
    }
if mode == "prompted":
    result |= {
        "tools_param_sent": "tools" in body1,
        "prompt_describes_terminal": "terminal" in text1,
        "prompt_has_call_format": "<function=" in text1,
        "stop_words": body1.get("stop"),
    }
if mode == "anthropic":
    assistant = [m for m in body2.get("messages", []) if m.get("role") == "assistant"]
    blocks = [
        b for m in assistant for b in (m.get("content") or []) if isinstance(b, dict)
    ]
    result |= {
        "thinking_param": body1.get("thinking"),
        "max_tokens": body1.get("max_tokens"),
        "thinking_sent_back": [
            b.get("signature") for b in blocks if b.get("type") == "thinking"
        ],
        "action_thinking_blocks": len(actions[0].thinking_blocks) if actions else 0,
        "cache_control_markers": text1.count("cache_control"),
        "anthropic_beta": requests[0]["headers"].get("anthropic-beta"),
    }
if mode == "stream":
    result |= {
        "stream_requested": bool(body1.get("stream")),
        "chunks": len(tokens),
        "delta_kinds": delta_kinds(),
        "reasoning_content_on_action": actions[0].reasoning_content
        if actions
        else None,
        "action_command": actions[0].action.model_dump().get("command")
        if actions
        else None,
    }
emit(result)
