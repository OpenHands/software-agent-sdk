"""A conversation whose agent has context (a system-prompt suffix and the
working directory) saves a SystemPromptEvent that becomes system content
blocks with the static prompt first, so that block stays cacheable.
"""

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, AgentContext, Conversation, Tool
from openhands.sdk.event import LLMConvertibleEvent, SystemPromptEvent
from openhands.tools.terminal import TerminalTool


agent = Agent(
    llm=make_llm([{"finish": "ok"}]),
    tools=[Tool(name=TerminalTool.name)],
    agent_context=AgentContext(system_message_suffix="Always answer in French."),
)
conversation = Conversation(
    agent=agent,
    workspace=str(run_paths()["workspace"]),
    persistence_dir=str(run_paths()["conversations"]),
    visualizer=None,
)
conversation.send_message("hi")
conversation.run()
events = list(conversation.state.events)
system = next(e for e in events if isinstance(e, SystemPromptEvent))
convertible = [e for e in events if isinstance(e, LLMConvertibleEvent)]
messages = LLMConvertibleEvent.events_to_messages(convertible)
blocks = [getattr(c, "text", "") for c in messages[0].content]
conversation.close()

emit(
    {
        "ok": True,
        "blocks": len(blocks),
        "blocks_ge_2": len(blocks) >= 2,
        "static_first": blocks[0] == system.system_prompt.text,
        "dynamic_has_suffix": any("French" in b for b in blocks[1:]),
        "static_has_suffix": "French" in blocks[0],
    }
)
