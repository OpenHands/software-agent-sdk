"""Probe real server startup and task execution in source/artifact environments."""

import json
import tempfile
from pathlib import Path

from openhands.agent_server import tool_router  # noqa: F401
from openhands.agent_server.sub_agents_router import SubAgentsRequest, get_sub_agents
from openhands.sdk import Agent, LocalConversation
from openhands.sdk.llm import Message, MessageToolCall
from openhands.sdk.subagent.registry import get_registered_agent_definitions
from openhands.sdk.testing import TestLLM
from openhands.tools.preset import default
from openhands.tools.task.definition import TaskAction
from openhands.tools.task.impl import TaskExecutor
from openhands.tools.task.manager import TaskManager


print("MODULE", default.__file__, flush=True)
print("REGISTRY", [d.name for d in get_registered_agent_definitions()], flush=True)
print(
    "API",
    [
        a.name
        for a in get_sub_agents(
            SubAgentsRequest(load_user=False, load_project=False)
        ).agents
    ],
    flush=True,
)
print(
    "BROWSER_DISABLED",
    [d.name for d in default.discover_builtin_agents(False)],
    flush=True,
)
for name in [
    "general-purpose",
    "code-explorer",
    "bash-runner",
    "genuinely-unknown-5380",
]:
    message = Message(
        role="assistant",
        content=[],
        tool_calls=[
            MessageToolCall(
                id="finish_probe",
                name="finish",
                arguments=json.dumps({"message": "probe complete"}),
                origin="completion",
            )
        ],
    )
    llm = TestLLM.from_messages([message])
    with tempfile.TemporaryDirectory(
        prefix="probe-", dir=Path(__file__).parent
    ) as workspace:
        parent = LocalConversation(
            agent=Agent(llm=llm, tools=[]),
            workspace=workspace,
            visualizer=None,
            persistence_dir=Path(workspace) / "state",
        )
        executor = TaskExecutor(TaskManager())
        try:
            observation = executor(
                TaskAction(
                    prompt="Reply done",
                    subagent_type=name,
                    description="packaging probe",
                ),
                conversation=parent,
            )
            print("TASK", name, observation.model_dump_json(), flush=True)
        finally:
            executor.close()
            parent.close()
