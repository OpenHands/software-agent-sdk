"""End-to-end check: the skill-location footer lets the LLM reach bundled files.

Loads the `frobnitz-converter` skill (under `.pr/skills_footer_check/`),
which defines fictional units whose conversion factors live in
`scripts/convert.py` and `references/conversion_table.md`. The LLM cannot
answer from prior knowledge — it must either run the script or read the
reference file. Both are addressed by paths relative to the skill
directory, which the footer appended by `invoke_skill` makes discoverable.

The run PASSES if any tool call references a file under the skill's
`scripts/` or `references/` directory — i.e. the agent tried to view or
run one of the bundled resources that only the footer could have pointed
it at.

Usage:
    LLM_API_KEY=... python .pr/check_skill_footer.py
    # optional: LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import SecretStr

from openhands.sdk import LLM, Agent, AgentContext, Conversation, Event
from openhands.sdk.event.llm_convertible.action import ActionEvent
from openhands.sdk.skills import Skill
from openhands.sdk.tool import Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


SKILL_MD = (
    Path(__file__).parent / "skills_footer_check" / "frobnitz-converter" / "SKILL.md"
)
PROMPT = (
    "I need to know how many meters 7 frobs equal. "
    "You have a skill available that handles frobnitz unit conversions — "
    "use it and tell me the exact numeric answer."
)
TARGETS = ("scripts/", "references/")


def _build_agent() -> tuple[Agent, Skill]:
    api_key = os.environ["LLM_API_KEY"]
    model = os.getenv("LLM_MODEL", "openhands/claude-sonnet-4-5-20250929")
    base_url = os.getenv("LLM_BASE_URL")
    llm = LLM(
        usage_id="agent",
        model=model,
        base_url=base_url,
        api_key=SecretStr(api_key),
    )
    skill = Skill.load(SKILL_MD)
    tools = [Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
    agent = Agent(llm=llm, tools=tools, agent_context=AgentContext(skills=[skill]))
    return agent, skill


def _touched_bundled_resource(events: list[Event]) -> tuple[bool, str]:
    """True if any tool call argument mentions `scripts/` or `references/`."""
    for e in events:
        if not isinstance(e, ActionEvent):
            continue
        args = getattr(e.tool_call, "arguments", {}) or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        blob = " ".join(str(v) for v in args.values())
        if any(t in blob for t in TARGETS):
            return True, f"{e.tool_name}({args})"
    return False, ""


def main() -> int:
    agent, _ = _build_agent()

    events: list[Event] = []
    conv = Conversation(
        agent=agent,
        callbacks=[events.append],
        workspace=os.getcwd(),
    )
    conv.send_message(PROMPT)
    conv.run()

    invoked = any(
        isinstance(e, ActionEvent) and e.tool_name == "invoke_skill" for e in events
    )
    touched, evidence = _touched_bundled_resource(events)

    print(f"invoke_skill called:           {invoked}")
    print(f"touched scripts/ or references/: {touched}")
    if evidence:
        print(f"  evidence: {evidence}")

    verdict = (
        "agent reached a bundled resource — footer usable"
        if touched
        else "agent never opened scripts/ or references/"
    )
    print(f"\n{'PASS' if touched else 'FAIL'}: {verdict}")
    return 0 if touched else 1


if __name__ == "__main__":
    raise SystemExit(main())
