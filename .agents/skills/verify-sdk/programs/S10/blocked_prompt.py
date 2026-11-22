"""A UserPromptSubmit hook blocks a message; the program stops, and a later
process resumes the persisted conversation and runs it.

Step by step, like two runs of one program:
1. send a message the hook blocks, without running; close.
2. reopen the conversation (passing hook_config again): the block survived;
   run it with a model that has nothing scripted, so any LLM call would fail.
3. reopen once more: the consumed block stays gone.
Also blocks an action id by hand and checks that it survives a reopen.
"""

import uuid
from pathlib import Path

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import HookExecutionEvent, MessageEvent
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher
from openhands.tools.terminal import TerminalTool


HOOKS = Path(__file__).resolve().parent / "hooks"
paths = run_paths()
hook_config = HookConfig(
    user_prompt_submit=[
        HookMatcher(hooks=[HookDefinition(command=f"{HOOKS}/deny_secret_prompt.sh")])
    ]
)
conversation_id = uuid.uuid4()


def open_conversation(script: list) -> Conversation:
    return Conversation(
        agent=Agent(llm=make_llm(script), tools=[Tool(name=TerminalTool.name)]),
        workspace=str(paths["workspace"]),
        persistence_dir=str(paths["conversations"]),
        conversation_id=conversation_id,
        hook_config=hook_config,
        visualizer=None,
        delete_on_close=False,
    )


# 1. The hook blocks the message.
conversation = open_conversation([])
conversation.send_message("Tell me the secret password.")
blocked_after_send = dict(conversation.state.blocked_messages)
conversation.state.block_action("action-123", "blocked for the persistence check")
conversation.close()

# 2. A new process resumes it.
conversation = open_conversation([])
blocked_after_reopen = dict(conversation.state.blocked_messages)
action_block_after_reopen = dict(conversation.state.blocked_actions)
run_error = None
try:
    conversation.run()
except Exception as exc:  # an LLM call would raise TestLLMExhaustedError
    run_error = f"{type(exc).__name__}: {exc}"[:300]
status = conversation.state.execution_status.value
events = list(conversation.state.events)
conversation.state.pop_blocked_action("action-123")
conversation.close()

# 3. The consumed block stays consumed.
conversation = open_conversation([])
blocked_after_run = dict(conversation.state.blocked_messages)
action_block_after_pop = dict(conversation.state.blocked_actions)
conversation.close()

hooks = [e for e in events if isinstance(e, HookExecutionEvent)]
agent_messages = [
    "".join(c.text for c in e.llm_message.content)
    for e in events
    if isinstance(e, MessageEvent) and e.source == "agent"
]
emit(
    {
        "ok": True,
        "blocked_after_send": len(blocked_after_send),
        "block_reason": next(iter(blocked_after_send.values()), None),
        "blocked_after_reopen": len(blocked_after_reopen),
        "same_message_blocked": blocked_after_reopen == blocked_after_send,
        "action_block_after_reopen": "action-123" in action_block_after_reopen,
        "run_error": run_error,
        "execution_status": status,
        "agent_messages": agent_messages,
        "blocked_after_run": len(blocked_after_run),
        "action_block_after_pop": "action-123" in action_block_after_pop,
        "user_prompt_hook_blocked": [
            e.blocked for e in hooks if e.hook_event_type == "UserPromptSubmit"
        ],
        "hook_message_id_set": all(
            e.message_id for e in hooks if e.hook_event_type == "UserPromptSubmit"
        ),
    }
)
