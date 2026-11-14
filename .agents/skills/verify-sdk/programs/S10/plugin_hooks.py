"""Hooks from plugins next to an explicit hook_config.

The conversation gets an explicit PostToolUse hook, one plugin attached with
`plugins=[PluginSource(...)]`, and later a second plugin loaded at runtime
with `load_plugin("runtime-hooks@local")` from a local marketplace
(programs/S10/marketplace). Every PostToolUse hook appends its name to
order.log in the workspace, so the file shows which hooks ran, in order.
"""

from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.sdk.event import HookExecutionEvent
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.sdk.marketplace.registration import MarketplaceRegistration
from openhands.sdk.plugin import PluginSource
from openhands.tools.terminal import TerminalTool


MARKETPLACE = Path(__file__).resolve().parent / "marketplace"
workspace = run_paths()["workspace"]
llm = make_llm(
    [
        {"tool": "terminal", "args": {"command": "echo one"}},
        {"finish": "first"},
        {"tool": "terminal", "args": {"command": "echo two"}},
        {"finish": "second"},
    ]
)
agent = Agent(
    llm=llm,
    tools=[Tool(name=TerminalTool.name)],
    agent_context=AgentContext(
        registered_marketplaces=[
            MarketplaceRegistration(name="local", source=str(MARKETPLACE))
        ]
    ),
)
conversation = Conversation(
    agent=agent,
    workspace=str(workspace),
    plugins=[PluginSource(source=str(MARKETPLACE / "plugins" / "attached-hooks"))],
    hook_config=HookConfig(
        post_tool_use=[
            HookMatcher(hooks=[HookDefinition(command="echo explicit >> order.log")])
        ]
    ),
    visualizer=None,
)
conversation.send_message("Echo one.")
conversation.run()
after_first = (workspace / "order.log").read_text().split()
matchers_before = len(conversation.state.hook_config.post_tool_use)

conversation.load_plugin("runtime-hooks@local")
matchers_after = len(conversation.state.hook_config.post_tool_use)
conversation.send_message("Echo two.")
conversation.run()
order = (workspace / "order.log").read_text().split()
status = conversation.state.execution_status.value
hook_commands = [
    e.hook_command
    for e in conversation.state.events
    if isinstance(e, HookExecutionEvent) and e.hook_event_type == "PostToolUse"
]
resolved = [p.source for p in (conversation.resolved_plugins or [])]
conversation.close()

emit(
    {
        "ok": True,
        "execution_status": status,
        "order_after_first_run": after_first,
        "order": order,
        "post_tool_use_matchers_before_load": matchers_before,
        "post_tool_use_matchers_after_load": matchers_after,
        "post_hook_commands": hook_commands,
        "resolved_plugins": resolved,
    }
)
