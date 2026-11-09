"""Persistent memory: MEMORY.md files on disk reach the system prompt.

Writes a user-tier index under $OH_PERSISTENCE_DIR/memory/ (plus a daily log
that must not be loaded) and a project-tier index under
<workspace>/.openhands/memory/, then runs conversations with
AgentContext(load_memory=True): once normally, once with an oversized user
index, once with an empty project index and an unreadable user index.
"""

import json
import os
import shutil
from pathlib import Path

from sdk_verify import conversation_dir, emit, run_paths, script_messages

from openhands.sdk import Agent, AgentContext, Conversation
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.testing import TestLLM


paths = run_paths()
user_dir = Path(os.environ["OH_PERSISTENCE_DIR"]) / "memory"
project_dir = paths["workspace"] / ".openhands" / "memory"
user_dir.mkdir(parents=True, exist_ok=True)
project_dir.mkdir(parents=True, exist_ok=True)


def run_once():
    agent = Agent(
        llm=TestLLM.from_messages(script_messages([{"text": "ok"}]), usage_id="agent"),
        tools=[],
        agent_context=AgentContext(load_memory=True),
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        visualizer=None,
    )
    before = conversation.agent.agent_context.memory_context
    conversation.send_message("hi")
    conversation.run()
    event = next(
        e for e in conversation.state.events if isinstance(e, SystemPromptEvent)
    )
    static = event.system_prompt.text
    dynamic = event.dynamic_context.text if event.dynamic_context else ""
    status = conversation.state.execution_status.value
    dumped = json.dumps(conversation.agent.agent_context.model_dump(mode="json"))
    stored = (conversation_dir(conversation.id.hex) / "base_state.json").read_text()
    conversation.close()
    return before, static, dynamic, status, dumped, stored


out = {"ok": True}

# 1. Both tiers and a daily log.
(user_dir / "MEMORY.md").write_text("USER-MEM: the user prefers uv.\n")
(user_dir / "2026-10-12.md").write_text("DAILY-LOG: not an index.\n")
(project_dir / "MEMORY.md").write_text("PROJECT-MEM: tests live in tests/.\n")
before, static, dynamic, status, dumped, stored = run_once()
out["memory_before_first_message"] = before
out["user_in_prompt"] = "USER-MEM" in dynamic
out["project_in_prompt"] = "PROJECT-MEM" in dynamic
out["user_before_project"] = 0 <= dynamic.find("USER-MEM") < dynamic.find("PROJECT-MEM")
out["daily_log_in_prompt"] = "DAILY-LOG" in static + dynamic
out["memory_in_static"] = "USER-MEM" in static
out["write_path_advertised"] = str(user_dir) in static
out["memory_in_agent_context_dump"] = "USER-MEM" in dumped
out["memory_in_persisted_state"] = "USER-MEM" in stored

# 2. An oversized user index is truncated from the top, by whole lines.
lines = [f"line {i:04d}: " + "x" * 40 for i in range(300)]
(user_dir / "MEMORY.md").write_text("\n".join(lines) + "\n")
_, _, dynamic, _, _, _ = run_once()
out["big_first_line_kept"] = lines[0] in dynamic
out["big_last_line_kept"] = lines[-1] in dynamic
out["big_truncation_notice"] = "[earlier memory truncated]" in dynamic
out["big_kept_lines"] = sum(1 for line in lines if line in dynamic)
out["big_partial_lines"] = sum(
    1 for row in dynamic.splitlines() if row.startswith("line ") and row not in lines
)

# 3. Empty project index, unreadable (a directory) user index.
(project_dir / "MEMORY.md").write_text("   \n")
(user_dir / "MEMORY.md").unlink()
(user_dir / "MEMORY.md").mkdir()
_, _, dynamic, status, _, _ = run_once()
out["broken_status"] = status
out["broken_has_memory_block"] = "MEMORY.md" in dynamic and "memory (" in dynamic
shutil.rmtree(user_dir / "MEMORY.md")

emit(out)
