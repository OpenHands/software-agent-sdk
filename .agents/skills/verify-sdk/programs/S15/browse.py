"""Browse a local two-page site with the browser tool set, driven by a
scripted agent through a real conversation and a real headless Chromium.

The agent navigates, reads the page state (with a screenshot) and content,
pages through the content, scrolls, types a name and clicks the button,
follows a link and goes back, opens a second tab, reads and restores
storage, and tries recording. The program then switches and closes tabs
in a third turn. Element indexes and tab ids are only known once the page
is loaded, so each turn swaps in the next scripted model with
`conversation.switch_llm`, built from what the previous turn showed. The
agent follows the flow the browser guidance prescribes: get_state after
each interaction, then get_content. Reports each named step and what is
left running after `conversation.close()`.
"""

import json
import re
import subprocess
import time
from pathlib import Path

from S14.toolkit import converse, tool_results
from S15.site import install_playwright_chromium, serve
from sdk_verify import emit, make_llm

from openhands.sdk import Tool
from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.browser_use.definition import BROWSER_RECORDING_OUTPUT_DIR


install_playwright_chromium()
base = serve()
home, page2 = f"{base}/index.html", f"{base}/page2.html"
storage = {
    "cookies": [
        {"name": "restored", "value": "yes", "domain": "127.0.0.1", "path": "/"}
    ],
    "origins": [
        {
            "origin": base,
            "localStorage": [{"name": "restored_key", "value": "restored_value"}],
        }
    ],
}
turn1 = [
    ("navigate", "browser_navigate", {"url": home}),
    ("state", "browser_get_state", {"include_screenshot": True}),
    ("content", "browser_get_content", {"extract_links": True}),
    (
        "content_paged",
        "browser_get_content",
        {"extract_links": False, "start_from_char": 2000},
    ),
    ("scroll", "browser_scroll", {"direction": "down"}),
]


def script(steps):
    return [{"tool": t, "args": a} for _, t, a in steps] + [{"finish": "done"}]


conversation = converse(
    [Tool(name=BrowserToolSet.name, params={"action_timeout_seconds": 30})],
    script(turn1),
    "Look around the local site.",
)
rows = [r for r in tool_results(conversation, clip=6000) if r["tool"] != "finish"]
named = dict(zip([n for n, _, _ in turn1], rows))
state_text = named["state"]["text"]
elements = json.loads(state_text[: state_text.rindex("}") + 1]).get(
    "interactive_elements", []
)
index_map = {e.get("tag"): e.get("index") for e in elements}


def next_turn(steps, text, usage_id):
    """Swap in the next scripted model (as a program swaps models between
    turns) whose calls use what the agent saw, and run one more turn."""
    seen = len(tool_results(conversation))
    conversation.switch_llm(make_llm(script(steps), usage_id=usage_id))
    conversation.send_message(text)
    conversation.run()
    new = [
        r for r in tool_results(conversation, clip=6000)[seen:] if r["tool"] != "finish"
    ]
    named.update(zip([n for n, _, _ in steps], new))


turn2 = [
    ("type", "browser_type", {"index": index_map["input"], "text": "Ada"}),
    ("click", "browser_click", {"index": index_map["button"]}),
    ("state_after_click", "browser_get_state", {}),
    ("after_click", "browser_get_content", {"extract_links": False}),
    ("follow_link", "browser_click", {"index": index_map["a"]}),
    ("state_page2", "browser_get_state", {}),
    ("on_page2", "browser_get_content", {"extract_links": False}),
    ("back", "browser_go_back", {}),
    ("state_after_back", "browser_get_state", {}),
    ("after_back", "browser_get_content", {"extract_links": False}),
    ("new_tab", "browser_navigate", {"url": page2, "new_tab": True}),
    ("tabs", "browser_list_tabs", {}),
    ("get_storage", "browser_get_storage", {}),
    ("set_storage", "browser_set_storage", {"storage_state": storage}),
    ("get_storage_again", "browser_get_storage", {}),
    ("missing_element", "browser_click", {"index": 99999}),
    ("stop_unstarted", "browser_stop_recording", {}),
    ("start_recording", "browser_start_recording", {}),
]
next_turn(
    turn2, "Greet Ada, follow the link, open a tab, check storage.", "agent-turn2"
)

tab_ids = re.findall(r'"tab_id": "([^"]+)"', named["tabs"]["text"])
turn3 = [
    ("switch_tab", "browser_switch_tab", {"tab_id": tab_ids[0]}),
    ("close_tab", "browser_close_tab", {"tab_id": tab_ids[-1]}),
    ("tabs_after_close", "browser_list_tabs", {}),
]
next_turn(turn3, "Switch to the first tab and close the last one.", "agent-turn3")
status = conversation.state.execution_status.value

screenshot_dir = Path(conversation.state.env_observation_persistence_dir or ".")
screenshots = sorted(p.name for p in screenshot_dir.glob("browser_screenshot_*"))
tools_map = sorted(conversation.agent.tools_map)
browser_tools = [
    t for n, t in conversation.agent.tools_map.items() if n.startswith("browser_")
]
executors = {id(t.executor) for t in browser_tools}
schemas_ok = all(t.to_mcp_tool().get("inputSchema") for t in browser_tools)
guidance_on_all = all(t.prompt_guidance for t in browser_tools)


def chrome_processes() -> int:
    """Chromium processes started from this run's HOME (other runs may be browsing
    too)."""
    pattern = f"{Path.home()}/.cache/ms-playwright/chromium"
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
    return len(out.stdout.split())


chrome_before = chrome_processes()
conversation.close()
time.sleep(1)

facts = {
    "storage_has_page_values": '"abc123"' in named["get_storage"]["text"]
    and '"dark"' in named["get_storage"]["text"],
    "storage_restored": '"restored_value"' in named["get_storage_again"]["text"]
    and '"restored"' in named["get_storage_again"]["text"],
}
for row in named.values():
    row["text"] = row["text"][:1500]
emit(
    {
        "ok": True,
        "steps": named,
        "browser_tools": [t for t in tools_map if t.startswith("browser_")],
        "shared_executors": len(executors),
        "mcp_schemas": schemas_ok,
        "guidance_on_all": guidance_on_all,
        "index_map": index_map,
        "tab_ids": tab_ids,
        "status": status,
        **facts,
        "tabs_seen": len(tab_ids),
        "tabs_left": len(re.findall(r'"tab_id"', named["tabs_after_close"]["text"])),
        "screenshot_files": screenshots,
        "screenshot_named_in_state": any(s in state_text for s in screenshots),
        "recordings_dir_exists": (Path.cwd() / BROWSER_RECORDING_OUTPUT_DIR).exists(),
        "chrome_processes_before_close": chrome_before,
        "chrome_processes_after_close": chrome_processes(),
    }
)
