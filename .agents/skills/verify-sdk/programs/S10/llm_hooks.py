"""Prompt and agent hooks in a conversation, with a scripted model.

A prompt hook on PreToolUse(terminal) and an agent hook on Stop use the
conversation's own LLM. TestLLM copies share one script queue, so the script
below interleaves the agent's turns with the hooks' answers:

  agent: terminal ls          -> prompt hook answers deny (JSON)
  agent: terminal echo ok     -> prompt hook answers prose (falls open)
  agent: finish               -> agent hook finishes with fenced JSON deny
  agent: finish               -> agent hook finishes with prose + JSON allow

Then the program switches the conversation to a second scripted LLM and
sends another message: the prompt hook must use the new model.
"""

from sdk_verify import emit, make_llm, run_paths

from openhands.sdk import Agent, Conversation, Tool
from openhands.sdk.event import HookExecutionEvent, MessageEvent, UserRejectObservation
from openhands.sdk.hooks import HookConfig, HookDefinition, HookMatcher, HookType
from openhands.tools.terminal import TerminalTool


hook_config = HookConfig(
    pre_tool_use=[
        HookMatcher(
            matcher="terminal",
            hooks=[
                HookDefinition(
                    type=HookType.PROMPT,
                    name="policy",
                    prompt="Deny any command that lists files.",
                    timeout=30,
                )
            ],
        )
    ],
    stop=[
        HookMatcher(
            hooks=[
                HookDefinition(
                    type=HookType.AGENT,
                    name="reviewer",
                    system_prompt="Allow stopping only once REPORT.md exists.",
                    tools=[],
                    max_iterations=2,
                    timeout=30,
                )
            ]
        )
    ],
)
llm = make_llm(
    [
        {"tool": "terminal", "args": {"command": "ls"}},
        {"text": '{"decision": "deny", "reason": "listing files is not allowed"}'},
        {"tool": "terminal", "args": {"command": "echo ok > ran.txt"}},
        {"text": "I think this one is fine."},
        {"finish": "done"},
        {
            "finish": "REPORT.md is missing.\n```json\n"
            '{"decision": "deny", "reason": "write REPORT.md"}\n```'
        },
        {"finish": "done again"},
        {"finish": 'All good now. {"decision": "allow", "reason": "ok"}'},
    ]
)
conversation = Conversation(
    agent=Agent(llm=llm, tools=[Tool(name=TerminalTool.name)]),
    workspace=str(run_paths()["workspace"]),
    hook_config=hook_config,
    visualizer=None,
)
conversation.send_message("List the files, then write ran.txt.")
conversation.run()
first_status = conversation.state.execution_status.value
first_left = len(llm._scripted_responses)
first_usage_ids = sorted(conversation.conversation_stats.usage_to_metrics)

# switch_llm: the prompt hook must now ask the new model.
switched = make_llm(
    [
        {"tool": "terminal", "args": {"command": "ls -la"}},
        {"text": '{"decision": "deny", "reason": "the switched model says no"}'},
        {"finish": "stopping"},
        {"finish": '{"decision": "allow", "reason": "fine"}'},
    ],
    usage_id="switched",
)
conversation.switch_llm(switched)
conversation.send_message("List the files again.")
conversation.run()

events = list(conversation.state.events)
hooks = [e for e in events if isinstance(e, HookExecutionEvent)]
pre = [e for e in hooks if e.hook_event_type == "PreToolUse"]
stop = [e for e in hooks if e.hook_event_type == "Stop"]
rejections = [e for e in events if isinstance(e, UserRejectObservation)]
feedback = [
    "".join(c.text for c in e.llm_message.content)
    for e in events
    if isinstance(e, MessageEvent) and e.source == "environment"
]
usage_ids = sorted(conversation.conversation_stats.usage_to_metrics)
status = conversation.state.execution_status.value
ran = (run_paths()["workspace"] / "ran.txt").exists()
conversation.close()

emit(
    {
        "ok": True,
        "first_status": first_status,
        "first_script_left": first_left,
        "execution_status": status,
        "switched_script_left": len(switched._scripted_responses),
        "pre_commands": [e.hook_command for e in pre],
        "pre_blocked": [e.blocked for e in pre],
        "pre_success": [e.success for e in pre],
        "pre_errors": [e.error for e in pre],
        "rejection_reasons": [e.rejection_reason for e in rejections],
        "fall_open_ran": ran,
        "stop_commands": [e.hook_command for e in stop],
        "stop_blocked": [e.blocked for e in stop],
        "stop_reasons": [e.reason for e in stop],
        "stop_feedback": feedback,
        "first_usage_ids": first_usage_ids,
        "usage_ids": usage_ids,
    }
)
