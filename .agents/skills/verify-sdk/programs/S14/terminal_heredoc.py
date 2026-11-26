"""The calls after a heredoc command whose next prompt is slow.

Usage: terminal_heredoc.py subprocess|tmux

`Tool(name="terminal", params={"terminal_type": MODE})`. A scripted agent runs:
  slow_prompt  PROMPT_COMMAND="$PROMPT_COMMAND; sleep 0.3" (each prompt now
               arrives 0.3 s after the command it follows, so a heredoc's
               completion marker is read before that prompt)
  heredoc      an alias, a `cat <<EOF` heredoc and the alias
  next         echo next-command
  third        echo third
The program reports each call's exit code and text.
"""

import sys

from S14.toolkit import converse, tool_results
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.tools.terminal import TerminalTool


mode = sys.argv[1]
steps = [
    ("slow_prompt", {"command": 'PROMPT_COMMAND="$PROMPT_COMMAND; sleep 0.3"'}),
    (
        "heredoc",
        {
            "command": "alias oh_test='echo alias-expanded'; "
            "cat <<EOF\nheredoc-body-line\nEOF\noh_test"
        },
    ),
    ("next", {"command": "echo next-command"}),
    ("third", {"command": "echo third"}),
]
script = [{"tool": "terminal", "args": args} for _, args in steps] + [
    {"finish": "done"}
]
conversation = converse(
    [Tool(name=TerminalTool.name, params={"terminal_type": mode})], script
)
rows = [r for r in tool_results(conversation) if r["tool"] == "terminal"]
named = {
    name: {"exit_code": row.get("exit_code"), "text": row["text"][:600]}
    for (name, _), row in zip(steps, rows)
}
status = conversation.state.execution_status.value
conversation.close()

emit({"ok": True, "mode": mode, "execution_status": status, "steps": named})
