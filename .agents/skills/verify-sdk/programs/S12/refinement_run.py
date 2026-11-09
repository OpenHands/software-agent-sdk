"""Critics inside running conversations.

Usage: refinement_run.py [scripted|live]

Scenarios (scripted agent unless noted):
  refine     a scoring critic (0.2, 0.3, 0.1) with threshold 0.6, max 2
  threshold  scores 0.2 then exactly 0.6, max 3
  message    finish_and_message mode, the agent ends with a plain message
  all        all_actions mode
  broken     the critic raises
  hooked     a PreToolUse hook blocks the first finish (scored 0.2, then 0.9)
  api        APIBasedCritic built from settings, against a local stand-in for
             the critic service's /classify endpoint (first reply HTTP 500)
In `live` mode only `api` runs, with the run's real model as the agent.
"""

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pydantic import SecretStr
from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.agent.critic_mixin import ITERATIVE_REFINEMENT_ITERATION_KEY
from openhands.sdk.critic import CriticBase, CriticResult, IterativeRefinementConfig
from openhands.sdk.critic.impl.api.client import CriticClient
from openhands.sdk.event import ActionEvent, MessageEvent, UserRejectObservation
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.settings import OpenHandsAgentSettings, VerificationSettings
from openhands.tools.terminal import TerminalTool


MODE = sys.argv[1] if len(sys.argv) > 1 else "scripted"
REPO = Path(__file__).resolve().parents[5]
paths = run_paths()
ws = paths["workspace"]
SCORES: list[float] = []


class ScoringCritic(CriticBase):
    """A program's own critic: hands out the next score from SCORES."""

    fail: bool = False

    def evaluate(self, events, git_patch=None) -> CriticResult:  # noqa: ARG002
        if self.fail:
            raise RuntimeError("critic backend down")
        return CriticResult(score=SCORES.pop(0), message="scored")


def run(critic, script, *, hook_config=None, message="Write out.txt, then finish."):
    conversation = Conversation(
        agent=Agent(
            llm=make_llm(script), tools=[Tool(name=TerminalTool.name)], critic=critic
        ),
        workspace=str(ws),
        hook_config=hook_config,
        visualizer=None,
    )
    conversation.send_message(message)
    conversation.run()
    events = list(conversation.state.events)
    out = {
        "status": conversation.state.execution_status.value,
        "refinements": conversation.state.agent_state.get(
            ITERATIVE_REFINEMENT_ITERATION_KEY, 0
        ),
        "finish_scores": [
            e.critic_result.score if e.critic_result else None
            for e in events
            if isinstance(e, ActionEvent) and e.tool_name == "finish"
        ],
        "terminal_scores": [
            e.critic_result.score if e.critic_result else None
            for e in events
            if isinstance(e, ActionEvent) and e.tool_name == "terminal"
        ],
        "message_scores": [
            e.critic_result.score if e.critic_result else None
            for e in events
            if isinstance(e, MessageEvent) and e.source == "agent"
        ],
        "followups": [
            "".join(c.text for c in e.llm_message.content)
            for e in events
            if isinstance(e, MessageEvent) and e.source == "user"
        ][1:],
        "rejections": [
            e.rejection_source for e in events if isinstance(e, UserRejectObservation)
        ],
        "script_left": len(conversation.agent.llm._scripted_responses)
        if hasattr(conversation.agent.llm, "_scripted_responses")
        else None,
    }
    conversation.close()
    return out


touch = {"tool": "terminal", "args": {"command": "echo hi > out.txt"}}
fin = {"finish": "done"}
result: dict = {"ok": True}

if MODE == "scripted":
    refine = IterativeRefinementConfig(success_threshold=0.6, max_iterations=2)
    SCORES[:] = [0.2, 0.3, 0.1]
    result["refine"] = run(
        ScoringCritic(iterative_refinement=refine), [touch, fin, fin, fin]
    )
    SCORES[:] = [0.2, 0.6]
    result["threshold"] = run(
        ScoringCritic(
            iterative_refinement=IterativeRefinementConfig(
                success_threshold=0.6, max_iterations=3
            )
        ),
        [touch, fin, fin],
    )
    SCORES[:] = [0.1]
    result["message"] = run(
        ScoringCritic(iterative_refinement=refine), [touch, {"text": "All written."}]
    )
    SCORES[:] = [0.9, 0.8]
    result["all"] = run(ScoringCritic(mode="all_actions"), [touch, fin])
    result["broken"] = run(
        ScoringCritic(fail=True, iterative_refinement=refine), [touch, fin]
    )
    SCORES[:] = [0.2, 0.9]
    result["hooked"] = run(
        ScoringCritic(iterative_refinement=refine),
        [touch, fin, fin],
        hook_config=HookConfig(
            pre_tool_use=[
                HookMatcher(
                    matcher="finish",
                    hooks=[
                        HookDefinition(
                            command="test -f finish_seen || "
                            "{ touch finish_seen; exit 2; }"
                        )
                    ],
                )
            ]
        ),
    )

# A stand-in for the critic service: records requests, answers from a queue.
labels = CriticClient(api_key="x").all_labels
requests: list[dict] = []


def probs(success: float, **issues: float) -> list[float]:
    values = {label: 0.05 for label in labels}
    values.update(success=success, sentiment_neutral=0.9, **issues)
    return [values[label] for label in labels]


replies = [500, probs(0.3, loop_behavior=0.8, insufficient_testing=0.1), probs(0.95)]


class Classify(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests.append(
            {"path": self.path, "auth": self.headers.get("Authorization"), **body}
        )
        reply = replies.pop(0) if len(replies) > 1 else replies[0]
        if reply == 500:
            self.send_response(500)
            self.end_headers()
            return
        payload = json.dumps(
            {"data": [{"probs": reply, "num_classes": len(reply)}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), Classify)
threading.Thread(target=server.serve_forever, daemon=True).start()

# The tokenizer config the critic would download from huggingface.co, from
# the repo's fixture copy, put where the SDK caches it.
tokenizer = "Qwen/Qwen3-4B-Instruct-2507"
cache = paths["home"] / ".cache/chat_templates"
cache.mkdir(parents=True, exist_ok=True)
(
    cache / f"{hashlib.md5(tokenizer.encode()).hexdigest()}_tokenizer_config.json"
).write_text(
    (
        REPO / "tests/fixtures/tokenizers/qwen3-4b-instruct-2507-tokenizer_config.json"
    ).read_text()
)

settings_agent = OpenHandsAgentSettings(
    llm=LLM(model="openai/gpt-4o-mini", api_key=SecretStr("llm-key")),
    verification=VerificationSettings(
        critic_enabled=True,
        enable_iterative_refinement=True,
        critic_threshold=0.6,
        max_refinement_iterations=3,
        critic_server_url=f"http://127.0.0.1:{server.server_port}/vllm",
        critic_model_name="my-critic",
        critic_api_key="critic-key",
    ),
).create_agent()
critic = settings_agent.critic
try:
    critic.evaluate([])  # type: ignore[union-attr]
    needs_system_prompt = None
except ValueError as exc:
    needs_system_prompt = str(exc)

llm = make_llm([touch, fin, fin]) if MODE == "scripted" else make_llm()
conversation = Conversation(
    agent=Agent(llm=llm, tools=[Tool(name=TerminalTool.name)], critic=critic),
    workspace=str(ws),
    visualizer=None,
)
conversation.send_message(
    "Create greeting.txt containing the word hello using the terminal, then finish."
    if MODE == "live"
    else "Write out.txt, then finish."
)
conversation.run()
events = list(conversation.state.events)
finishes = [e for e in events if isinstance(e, ActionEvent) and e.tool_name == "finish"]
first = finishes[0].critic_result if finishes and finishes[0].critic_result else None
followups = [
    "".join(c.text for c in e.llm_message.content)
    for e in events
    if isinstance(e, MessageEvent) and e.source == "user"
][1:]
display = first.visualize.plain if first else ""
result["api"] = {
    "status": conversation.state.execution_status.value,
    "refinements": conversation.state.agent_state.get(
        ITERATIVE_REFINEMENT_ITERATION_KEY, 0
    ),
    "needs_system_prompt": needs_system_prompt,
    "requests": len(requests),
    "paths": sorted({r["path"] for r in requests}),
    "auth": sorted({r["auth"] for r in requests}),
    "models": sorted({r["model"] for r in requests}),
    "input_starts_with_system": all(
        r["input"].startswith("<|im_start|>system") for r in requests
    ),
    "input_has_tools": all("<tools>" in r["input"] for r in requests),
    "first_score": first.score if first else None,
    "first_event_ids": len(first.metadata.get("event_ids", []))
    if first and first.metadata
    else 0,
    "first_issues": [
        f["name"]
        for f in (first.metadata or {})
        .get("categorized_features", {})
        .get("agent_behavioral_issues", [])
    ]
    if first
    else [],
    "first_display": display,
    "last_score": finishes[-1].critic_result.score
    if finishes and finishes[-1].critic_result
    else None,
    "followups": followups,
    "greeting": (ws / "greeting.txt").read_text()
    if (ws / "greeting.txt").exists()
    else None,
}
conversation.close()
server.shutdown()
emit(result)
