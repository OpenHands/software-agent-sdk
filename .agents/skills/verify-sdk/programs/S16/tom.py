"""The Theory-of-Mind tools as example 30 wires them, with their model
pointed at a local OpenAI-compatible endpoint (no key, no network).

The program persists one earlier scripted conversation under the user
persistence directory (`$OH_PERSISTENCE_DIR/conversations`, which is
`~/.openhands/conversations` when the variable is unset), then a scripted
agent with `tom_consult` and `sleeptime_compute` runs sleeptime compute up
front with `conversation.execute_tool` and consults Tom.
"""

import os
from pathlib import Path

from S14.toolkit import converse, tool_results
from S16 import fake_openai
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.sdk.utils.path import get_user_persistence_dir
from openhands.tools.terminal import TerminalTool
from openhands.tools.tom_consult import (
    SleeptimeComputeAction,
    SleeptimeComputeTool,
    TomConsultTool,
)


root = Path(get_user_persistence_dir())
conversations = root / "conversations"
base_url = fake_openai.serve(
    '{"suggestions": "Ask which tests matter.", "confidence_score": 0.4}'
)
tom_params = {
    "enable_rag": True,
    "llm_model": "openai/tom-model",
    "api_key": "local",
    "api_base": base_url,
}
tools = [
    Tool(name=TomConsultTool.name, params=tom_params),
    Tool(name=SleeptimeComputeTool.name, params=tom_params),
]

# Sleeptime compute before any conversation is stored.
empty = converse(
    tools,
    [],
    run=False,
    conversation_kwargs={"persistence_dir": str(conversations)},
    persist=False,
)
before = empty.execute_tool("sleeptime_compute", SleeptimeComputeAction())
empty.close()

# One earlier session in the user store.
earlier = converse(
    [Tool(name=TerminalTool.name)],
    [
        {"tool": "terminal", "args": {"command": "echo I prefer pytest -q"}},
        {"finish": "ok"},
    ],
    "I always run tests with pytest -q.",
    persist=False,
    conversation_kwargs={"persistence_dir": str(conversations)},
)
earlier.close()

conversation = converse(
    tools,
    [
        {
            "tool": "tom_consult",
            "args": {"reason": "The request is vague.", "use_user_message": True},
        },
        {"finish": "done"},
    ],
    "Make the tests better.",
    persist=False,
    conversation_kwargs={"persistence_dir": str(conversations)},
)
try:
    after = conversation.execute_tool("sleeptime_compute", SleeptimeComputeAction())
    after_text, after_count, after_error = (
        after.text,
        getattr(after, "sessions_processed", None),
        None,
    )
except Exception as exc:  # what a program sees if indexing fails
    after_text, after_count, after_error = (
        "",
        None,
        f"{type(exc).__name__}: {exc}"[:300],
    )
rows = tool_results(conversation)
consult = rows[0]
conversation.close()

emit(
    {
        "ok": True,
        "store_root": str(root),
        "store_root_is_env": str(root) == os.environ.get("OH_PERSISTENCE_DIR"),
        "before_message": getattr(before, "message", None),
        "before_processed": getattr(before, "sessions_processed", None),
        "sessions_on_disk": len([p for p in conversations.iterdir() if p.is_dir()]),
        "after_text": after_text[:300],
        "after_processed": after_count,
        "after_error": after_error,
        "consult_tool": consult["tool"],
        "consult_is_error": consult["is_error"],
        "consult_text": consult["text"][:500],
        "tom_requests": len(fake_openai.requests),
        "files_under_root": sorted(
            str(p.relative_to(root))
            for p in root.rglob("*")
            if p.is_file() and "conversations" not in p.parts
        )[:20],
    }
)
