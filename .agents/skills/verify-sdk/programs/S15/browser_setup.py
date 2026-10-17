"""How a program's browser setup behaves: detection, a missing Chromium,
sharing one browser between conversations, executor options, recovery
from hung actions, the prompt guidance and the presets.

Usage: browser_setup.py nochrome|shared|config|hang|record|preset

- nochrome: HOME has no Playwright Chromium (and none is on PATH or in the
  system paths); an agent with terminal + browser_tool_set still works.
- shared: two conversations open at once with different browser params,
  then a third after both close.
- config: allowed_domains and inject_scripts.
- hang: a 12 s action_timeout_seconds against a page whose script never
  yields (three timeouts reset the session), then a normal page again.
- record: record a short session (start, type, click, change page, wait
  past one flush, stop) and list the recording files on disk. Needs
  rrweb from unpkg.com.
- preset: the default presets with and without the browser.
"""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from S14.toolkit import converse, tool_results
from S15.site import install_playwright_chromium, serve
from sdk_verify import emit

from openhands.sdk import Tool
from openhands.sdk.event import SystemPromptEvent
from openhands.sdk.tool.registry import list_usable_tools
from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.browser_use.impl import BrowserToolExecutor
from openhands.tools.terminal import TerminalTool


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text += f" | {record.exc_info[1]}"
        self.lines.append(text)


capture = Capture()
for name in ("", "openhands", "openhands.tools"):
    logging.getLogger(name).addHandler(capture)

mode = sys.argv[1]
GUIDANCE = "<BROWSER_TOOLS>"


def system_prompt(conversation) -> str:
    for event in conversation.state.events:
        if isinstance(event, SystemPromptEvent):
            dynamic = event.dynamic_context.text if event.dynamic_context else ""
            return event.system_prompt.text + "\n" + dynamic
    return ""


def browser_executor(conversation):
    tool = conversation.agent.tools_map.get("browser_navigate")
    return tool.executor if tool else None


result: dict = {"ok": True, "mode": mode}

if mode == "nochrome":
    result["detected"] = BrowserToolExecutor.check_chromium_available()
    result["is_usable"] = BrowserToolSet.is_usable()
    result["usable_tools"] = list_usable_tools()
    conversation = converse(
        [Tool(name=TerminalTool.name), Tool(name=BrowserToolSet.name)],
        [
            {"tool": "terminal", "args": {"command": "echo still working"}},
            {"finish": "done"},
        ],
    )
    rows = tool_results(conversation)
    result["status"] = conversation.state.execution_status.value
    result["tools_map"] = sorted(conversation.agent.tools_map)
    result["terminal_text"] = rows[0]["text"][:200]
    result["guidance_in_prompt"] = GUIDANCE in system_prompt(conversation)
    conversation.close()
    result["warnings"] = [
        w[:600] for w in capture.lines if "rowser" in w or "hromium" in w
    ]

elif mode == "shared":
    result["detected"] = (
        install_playwright_chromium() is not None
        and BrowserToolExecutor.check_chromium_available()
    )
    base = serve()
    first = converse(
        [Tool(name=BrowserToolSet.name, params={"action_timeout_seconds": 30})],
        [
            {"tool": "browser_navigate", "args": {"url": f"{base}/index.html"}},
            {"finish": "first"},
        ],
    )
    second = converse(
        [
            Tool(
                name=BrowserToolSet.name,
                params={"action_timeout_seconds": 45, "headless": True},
            )
        ],
        [{"tool": "browser_list_tabs", "args": {}}, {"finish": "second"}],
    )
    result["same_executor"] = browser_executor(first) is browser_executor(second)
    result["second_sees_first_page"] = "index.html" in tool_results(second)[0]["text"]
    result["ignored_warning"] = any("will be ignored" in w for w in capture.lines)
    first_executor = browser_executor(first)
    first.close()
    second.close()
    third = converse(
        [Tool(name=BrowserToolSet.name, params={"action_timeout_seconds": 30})],
        [
            {"tool": "browser_navigate", "args": {"url": f"{base}/page2.html"}},
            {"finish": "third"},
        ],
    )
    rows = tool_results(third)
    result["third_new_executor"] = browser_executor(third) is not first_executor
    result["third_text"] = rows[0]["text"][:200]
    result["third_is_error"] = rows[0]["is_error"]
    third.close()

elif mode in ("config", "hang"):
    install_playwright_chromium()
    base = serve()
    marker = (
        "document.addEventListener('DOMContentLoaded', () =>"
        " document.body.insertAdjacentHTML('afterbegin', '<p>INJECTED MARKER</p>'));"
    )
    steps = [
        ("allowed", "browser_navigate", {"url": f"{base}/index.html"}),
        ("state", "browser_get_state", {}),
        ("content", "browser_get_content", {"extract_links": False}),
        ("blocked_domain", "browser_navigate", {"url": "https://example.com/"}),
        ("blocked_state", "browser_get_state", {}),
    ]
    if mode == "hang":
        steps = steps[:2] + [
            ("hang1", "browser_navigate", {"url": f"{base}/hang.html"}),
            ("hang2", "browser_get_state", {}),
            ("hang3", "browser_get_content", {"extract_links": False}),
            ("recovered", "browser_navigate", {"url": f"{base}/page2.html"}),
            ("recovered_state", "browser_get_state", {}),
        ]
    conversation = converse(
        [
            Tool(
                name=BrowserToolSet.name,
                params={
                    "allowed_domains": ["127.0.0.1"],
                    "inject_scripts": [marker],
                    "action_timeout_seconds": 12,
                },
            )
        ],
        [{"tool": t, "args": a} for _, t, a in steps] + [{"finish": "done"}],
    )
    rows = [r for r in tool_results(conversation) if r["tool"] != "finish"]
    named = {
        n: {k: r[k] for k in ("is_error", "text")} for (n, _, _), r in zip(steps, rows)
    }
    for row in named.values():
        row["text"] = row["text"][:400]
    result["steps"] = named
    result["reset_warning"] = any("consecutive failures" in w for w in capture.lines)
    conversation.close()
    time.sleep(2)
    # Chromium processes from this run's HOME still alive after close.
    pattern = f"{Path.home()}/.cache/ms-playwright/chromium"
    alive = subprocess.run(
        ["pgrep", "-f", pattern], capture_output=True, text=True
    ).stdout.split()
    result["chrome_processes_after_close"] = len(alive)
    for pid in alive:  # do not leave a spinning browser behind
        subprocess.run(["kill", "-9", pid], capture_output=True)

elif mode == "record":
    from openhands.tools.browser_use.definition import BROWSER_RECORDING_OUTPUT_DIR

    install_playwright_chromium()
    base = serve()
    steps = [
        ("navigate", "browser_navigate", {"url": f"{base}/index.html"}),
        ("start", "browser_start_recording", {}),
        ("scroll", "browser_scroll", {"direction": "down"}),
        ("page2", "browser_navigate", {"url": f"{base}/page2.html"}),
        ("back", "browser_go_back", {}),
        ("state", "browser_get_state", {}),
        ("stop", "browser_stop_recording", {}),
    ]
    script = [{"tool": t, "args": a} for _, t, a in steps[:5]]
    script += [
        {"tool": "terminal", "args": {"command": "sleep 6"}}
    ]  # past one 5 s flush
    script += [{"tool": t, "args": a} for _, t, a in steps[5:]] + [{"finish": "done"}]
    conversation = converse(
        [
            Tool(name=BrowserToolSet.name, params={"action_timeout_seconds": 30}),
            Tool(name=TerminalTool.name),
        ],
        script,
    )
    rows = [
        r for r in tool_results(conversation) if r["tool"] not in ("finish", "terminal")
    ]
    result["steps"] = {
        n: {"is_error": r["is_error"], "text": r["text"][:400]}
        for (n, _, _), r in zip(steps, rows)
    }
    conversation.close()
    root = Path.cwd() / BROWSER_RECORDING_OUTPUT_DIR
    folders = sorted(root.glob("recording-*"))
    result["recording_folders"] = [f.name for f in folders]
    result["recording_files"] = sorted(
        p.name for f in folders for p in f.glob("*.json")
    )
    events = 0
    for f in folders:
        for chunk in f.glob("*.json"):
            data = json.loads(chunk.read_text())
            events += (
                len(data) if isinstance(data, list) else len(data.get("events", []))
            )
    result["events_on_disk"] = events

elif mode == "preset":
    from openhands.sdk import LLM
    from openhands.tools.preset.default import (
        get_default_agent,
        get_default_tools,
        register_builtins_agents,
    )

    llm = LLM(model="test-model", usage_id="preset")
    result["with_browser_and_subagents"] = [
        t.name for t in get_default_tools(enable_browser=True, enable_sub_agents=True)
    ]
    result["default_agent_tools"] = [t.name for t in get_default_agent(llm).tools]
    result["cli_agent_tools"] = [
        t.name for t in get_default_agent(llm, cli_mode=True).tools
    ]
    result["builtins_without_browser"] = register_builtins_agents(enable_browser=False)
    result["added_with_browser"] = register_builtins_agents(enable_browser=True)

else:
    raise SystemExit(f"unknown mode {mode}")

emit(result)
