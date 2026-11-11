"""Track LLM cost across a conversation, an extra LLM the program adds to the
conversation's registry, and a resume from disk.

Usage: conversation_stats.py [after-run|before-first-message]

A priced `LLM` (input/output cost per token) answers from a local endpoint, so
costs are real numbers computed by the SDK. The program:
1. runs a persisted conversation (two priced agent calls),
2. adds a second LLM to `conversation.llm_registry` and calls it once, after
   the run as example 21 does (or, with `before-first-message`, right after
   building the conversation, before anything was sent),
3. closes, reopens the conversation from disk with a fresh agent LLM, and
   runs one more turn.
"""

import sys
import uuid

from pydantic import SecretStr
from S02.fake_provider import FakeProvider
from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.llm import Message, TextContent
from openhands.tools.terminal import TerminalTool


paths = run_paths()
PRICE = {"input_cost_per_token": 0.001, "output_cost_per_token": 0.002}  # 0.14 per call
ECHO = {"command": "echo stats", "summary": "echo", "security_risk": "LOW"}
cid = uuid.uuid4()


def priced(server: FakeProvider, usage_id: str) -> LLM:
    return LLM(
        model="openai/gpt-4o-mini",
        base_url=server.url,
        api_key=SecretStr("sk-test"),
        usage_id=usage_id,
        num_retries=1,
        **PRICE,
    )


def open_conversation(server: FakeProvider, callbacks=None) -> Conversation:
    return Conversation(
        agent=Agent(llm=priced(server, "agent"), tools=[Tool(name=TerminalTool.name)]),
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        conversation_id=cid,
        visualizer=None,
        callbacks=callbacks,
    )


order = sys.argv[1] if len(sys.argv) > 1 else "after-run"


def side_job(conversation: Conversation, server: FakeProvider) -> None:
    secondary = priced(server, "secondary")
    conversation.llm_registry.add(secondary)
    secondary.completion([Message(role="user", content=[TextContent(text="side job")])])


script = [{"tool": "terminal", "args": ECHO}, {"finish": "done"}]
script.insert(0 if order == "before-first-message" else 2, {"text": "side"})
with FakeProvider(script) as server:
    conversation = open_conversation(server)
    if order == "before-first-message":
        side_job(conversation, server)
    conversation.send_message("Echo stats.")
    conversation.run()
    if order != "before-first-message":
        side_job(conversation, server)
    stats = conversation.conversation_stats
    first = {
        "usage_ids": sorted(stats.usage_to_metrics),
        "agent_cost": round(stats.usage_to_metrics["agent"].accumulated_cost, 6),
        "secondary_in_registry": "secondary"
        in conversation.llm_registry.list_usage_ids(),
        "secondary_cost": round(stats.usage_to_metrics["secondary"].accumulated_cost, 6)
        if "secondary" in stats.usage_to_metrics
        else None,
        "combined_cost": round(stats.get_combined_metrics().accumulated_cost, 6),
        "combined_prompt_tokens": (
            stats.get_combined_metrics().accumulated_token_usage.prompt_tokens
        ),
    }
    conversation.close()

with FakeProvider([{"finish": "again"}]) as server:
    resumed = open_conversation(server)
    restored_cost = round(
        resumed.conversation_stats.usage_to_metrics["agent"].accumulated_cost, 6
    )
    resumed.send_message("Once more.")
    resumed.run()
    after = resumed.conversation_stats.usage_to_metrics["agent"]
    resumed_view = {
        "restored_agent_cost": restored_cost,
        "agent_cost_after": round(after.accumulated_cost, 6),
        "agent_calls_after": len(after.costs),
        "usage_ids": sorted(resumed.conversation_stats.usage_to_metrics),
        "status": resumed.state.execution_status.value,
    }
    resumed.close()

emit({"ok": True, "order": order, "first_run": first, "resumed": resumed_view})
