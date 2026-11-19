"""A command hook in a conversation that has secrets.

The conversation holds the secret VERIFY_HOOK_SECRET. A PreToolUse hook on
terminal and the agent's own terminal call both test whether the variable is
set in their environment, without printing it. The program reports what each
saw.
"""

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import HookExecutionEvent, ObservationEvent
from openhands.sdk.hooks import HookConfig
from openhands.tools.terminal import TerminalTool


paths = run_paths()
probe = 'test -n "$VERIFY_HOOK_SECRET" && echo {0}_SECRET_SET || echo {0}_SECRET_UNSET'
llm = make_llm(
    [
        {"tool": "terminal", "args": {"command": probe.format("TERMINAL")}},
        {"finish": "done"},
    ]
)
conversation = Conversation(
    agent=Agent(llm=llm, tools=[Tool(name=TerminalTool.name)]),
    workspace=str(paths["workspace"]),
    persistence_dir=str(paths["conversations"]),
    secrets={"VERIFY_HOOK_SECRET": "verify-hook-secret-value"},
    hook_config=HookConfig.model_validate(
        {
            "pre_tool_use": [
                {"matcher": "terminal", "hooks": [{"command": probe.format("HOOK")}]}
            ]
        }
    ),
    visualizer=None,
)
conversation.send_message("check the secret")
conversation.run()
events = list(conversation.state.events)
hooks = [e for e in events if isinstance(e, HookExecutionEvent)]
observations = [
    e for e in events if isinstance(e, ObservationEvent) and e.tool_name == "terminal"
]
emit(
    {
        "ok": True,
        "status": conversation.state.execution_status.value,
        "hook_runs": len(hooks),
        "hook_stdout": hooks[0].stdout.strip() if hooks else None,
        "terminal_text": observations[0].observation.text[:200]
        if observations
        else None,
    }
)
conversation.close()
