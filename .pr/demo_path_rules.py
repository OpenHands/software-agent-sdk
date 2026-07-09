"""End-to-end demo for path-scoped rules (issue #3984).

Runs a full ``Conversation.run()`` with the real ``file_editor`` tool and a
scripted ``TestLLM`` (no API key needed). It shows that when the agent touches a
file matching a rule's ``paths:`` glob, the rule content is deterministically
injected into the tool observation the LLM sees on its next step — while the
rule stays out of the ``<available_skills>`` catalog and is not invocable.

Run:  uv run python .pr/demo_path_rules.py
"""

import json
import os
import tempfile

from openhands.sdk import Agent, Conversation, Message, TextContent
from openhands.sdk.context.agent_context import AgentContext
from openhands.sdk.event.llm_convertible import ObservationEvent
from openhands.sdk.llm import MessageToolCall
from openhands.sdk.skills import PathTrigger
from openhands.sdk.testing import TestLLM
from openhands.sdk.tool import Tool, register_tool
from openhands.tools.file_editor import FileEditorTool


def main() -> None:
    ws = tempfile.mkdtemp()

    # 1. Author a path rule — just a skill with `paths:` frontmatter — in a
    #    normal skills dir. No dedicated rules directory needed.
    skills_dir = os.path.join(ws, ".openhands", "skills")
    os.makedirs(skills_dir)
    with open(os.path.join(skills_dir, "api-rule.md"), "w") as f:
        f.write(
            '---\npaths:\n  - "src/api/**/*.ts"\n---\n'
            "API RULE: validate all request inputs with zod.\n"
        )
    os.makedirs(os.path.join(ws, "src", "api"))  # so `create` succeeds
    target = os.path.join(ws, "src", "api", "users.ts")

    # 2. Script the LLM: create a matching file (fires on `create` too), then finish.
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="Creating the users API file.")],
                tool_calls=[
                    MessageToolCall(
                        id="call_1",
                        name="file_editor",
                        arguments=json.dumps(
                            {
                                "command": "create",
                                "path": target,
                                "file_text": "export const users = [];\n",
                            }
                        ),
                        origin="completion",
                    )
                ],
            ),
            Message(role="assistant", content=[TextContent(text="Done.")]),
        ],
        model="test-model",
    )

    register_tool("file_editor", FileEditorTool)
    agent = Agent(
        llm=llm,
        tools=[Tool(name="file_editor")],
        agent_context=AgentContext(load_project_skills=True),
    )
    conv = Conversation(agent=agent, workspace=ws)
    conv.send_message(
        Message(role="user", content=[TextContent(text="Create the users API file.")])
    )
    conv.run()

    # 3. Evidence.
    ctx = conv.agent.agent_context
    assert ctx is not None
    rule = next(s for s in ctx.skills if s.name == "api-rule")
    assert isinstance(rule.trigger, PathTrigger)
    _, available = ctx._partition_skills()
    obs = [
        e
        for e in conv.state.events
        if isinstance(e, ObservationEvent) and e.tool_name == "file_editor"
    ]
    injected = [c.text for o in obs for c in o.extended_content]

    print("=" * 68)
    print(
        f"rule loaded          : trigger={type(rule.trigger).__name__} "
        f"paths={rule.trigger.paths}"
    )
    print(
        f"advertised in catalog: {'api-rule' in [s.name for s in available]}  "
        f"(should be False)"
    )
    print(
        f"invocable by model   : {not rule.disable_model_invocation}  (should be False)"
    )
    print(f"activated_path_rules : {conv.state.activated_path_rules}")
    print(f"injected into obs    : {injected}")
    llm_sees = any("API RULE" in c.text for o in obs for c in o.extended_content)
    print(f"\nLLM sees the rule after touching src/api/users.ts: {llm_sees}")
    print("=" * 68)
    assert llm_sees and conv.state.activated_path_rules == ["api-rule"]


if __name__ == "__main__":
    main()
