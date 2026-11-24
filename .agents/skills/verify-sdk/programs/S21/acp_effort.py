"""Drive ACP reasoning effort the way a program does: `ACPAgent(acp_effort=...)`,
`Conversation.switch_acp_effort(...)` before and after the session starts,
across model switches, and through `discover_acp_models()`. The ACP server is
`fake_acp.py` with `FAKE_ACP_EFFORT=1`: model `m1` offers a `thought_level`
select (`effort`: low/medium/high, default medium) that a model switch resets,
and `m2` offers none. A server named `codex-acp` offers `reasoning_effort`.

Usage: acp_effort.py
"""

import json
import logging
import os
import sys
from pathlib import Path

from sdk_verify import emit, run_paths

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.agent import ACPAgent


HERE = Path(__file__).resolve().parent
FAKE = [sys.executable, str(HERE / "fake_acp.py")]
paths = run_paths()
work = paths["run"] / "acp" / "effort"
work.mkdir(parents=True, exist_ok=True)
JOURNAL = work / "journal.jsonl"
os.environ["FAKE_ACP_JOURNAL"] = str(JOURNAL)
os.environ["FAKE_ACP_SESSIONS"] = str(work / "sessions.txt")
os.environ["FAKE_ACP_EFFORT"] = "1"
workspace = paths["workspace"]
workspace.mkdir(parents=True, exist_ok=True)
result: dict = {"ok": True}
warnings_seen: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            warnings_seen.append(record.getMessage()[:300])


logging.getLogger("openhands").addHandler(_Capture())


def journal(kind: str) -> list[dict]:
    if not JOURNAL.exists():
        return []
    rows = [json.loads(x) for x in JOURNAL.read_text().splitlines() if x.strip()]
    return [r for r in rows if r["kind"] == kind]


def config_calls(since: int) -> list[str]:
    return [
        f"{c['config_id']}={c['value']}" for c in journal("set_config_option")[since:]
    ]


def run_turn(conversation, text: str) -> str | None:
    conversation.send_message(text)
    try:
        conversation.run()
    except Exception as exc:  # recorded, the checks judge it
        return f"{type(exc).__name__}: {str(exc)[:200]}"
    return None


def error_of(fn, *args) -> str:
    try:
        fn(*args)
    except Exception as exc:  # recorded, the checks judge it
        return f"{type(exc).__name__}: {exc}"[:300]
    return "no error"


def view(conversation) -> dict:
    agent = conversation.agent
    return {
        "acp_effort": agent.acp_effort,
        "current_model_id": agent.current_model_id,
        "current_effort": agent.current_effort,
        "available_efforts": [o.value for o in agent.available_efforts],
        "hint_effort": conversation.state.agent_state.get(
            "acp_current_effort", "unset"
        ),
        "session_id": conversation.state.agent_state.get("acp_session_id"),
    }


def persisted_agent(conversation) -> dict:
    base = Path(conversation.state.persistence_dir or "") / "base_state.json"
    data = json.loads(base.read_text())
    return {
        "acp_effort": data["agent"].get("acp_effort"),
        "hint_effort": data.get("agent_state", {}).get("acp_current_effort", "unset"),
    }


# 1. Pinned at construction, then switched live.
calls = len(journal("set_config_option"))
live = Conversation(
    agent=ACPAgent(acp_command=FAKE, acp_model="m1", acp_effort="high"),
    workspace=str(workspace),
    visualizer=None,
    persistence_dir=str(work / "store-live"),
)
result["start_error"] = run_turn(live, "hello")
result["start"] = {**view(live), "config_calls": config_calls(calls)}
session = live.state.agent_state.get("acp_session_id")

calls = len(journal("set_config_option"))
live.switch_acp_effort("low")
result["switched"] = {
    **view(live),
    "config_calls": config_calls(calls),
    "same_session": live.state.agent_state.get("acp_session_id") == session,
    "persisted": persisted_agent(live),
}
calls = len(journal("set_config_option"))
result["unoffered_error"] = error_of(live.switch_acp_effort, "ultra")
result["blank_error"] = error_of(live.switch_acp_effort, "  ")
result["after_refused"] = {**view(live), "config_calls": config_calls(calls)}

live.switch_acp_model("m2")
result["on_m2"] = view(live)
result["on_m2_error"] = error_of(live.switch_acp_effort, "low")
calls = len(journal("set_config_option"))
live.switch_acp_model("m1")
result["back_on_m1"] = {**view(live), "config_calls": config_calls(calls)}
prompts = len(journal("prompt"))
result["next_turn_error"] = run_turn(live, "again")
result["next_turn"] = {
    "status": live.state.execution_status.value,
    "prompts": len(journal("prompt")) - prompts,
    "same_session": live.state.agent_state.get("acp_session_id") == session,
}
live.agent.close()
live.close()

# 2. A level the model does not offer at session start is skipped, not fatal.
calls = len(journal("set_config_option"))
warn = len(warnings_seen)
skipped = Conversation(
    agent=ACPAgent(acp_command=FAKE, acp_model="m1", acp_effort="ultra"),
    workspace=str(workspace),
    visualizer=None,
)
result["skipped_error"] = run_turn(skipped, "hello")
result["skipped"] = {
    **view(skipped),
    "status": skipped.state.execution_status.value,
    "config_calls": config_calls(calls),
    "warned": any("ultra" in w for w in warnings_seen[warn:]),
}
skipped.agent.close()
skipped.close()

# 3. Before the first run the effort is only persisted; the session applies it.
starts = len(journal("start"))
calls = len(journal("set_config_option"))
early = Conversation(
    agent=ACPAgent(acp_command=FAKE),
    workspace=str(workspace),
    visualizer=None,
    persistence_dir=str(work / "store-early"),
)
early.switch_acp_effort("high")
result["deferred"] = {
    **view(early),
    "processes_started": len(journal("start")) - starts,
    "persisted": persisted_agent(early),
    "blank_error": error_of(early.switch_acp_effort, ""),
}
result["deferred_error"] = run_turn(early, "hello")
result["deferred_started"] = {**view(early), "config_calls": config_calls(calls)}
early.agent.close()
early.close()

# 4. A legacy <model>/<effort> id on a server the SDK knows (codex-acp):
#    the model id is bare, and acp_effort wins over the suffix.
os.environ["FAKE_ACP_NAME"] = "codex-acp"
legacy = {}
for name, effort in (("suffix", None), ("explicit", "low")):
    calls = len(journal("set_config_option"))
    conversation = Conversation(
        agent=ACPAgent(acp_command=FAKE, acp_model="gpt-5.5/high", acp_effort=effort),
        workspace=str(workspace),
        visualizer=None,
    )
    error = run_turn(conversation, "hello codex")
    legacy[name] = {
        **view(conversation),
        "error": error,
        "config_calls": config_calls(calls),
    }
    conversation.agent.close()
    conversation.close()
os.environ.pop("FAKE_ACP_NAME")
result["legacy"] = legacy

# 5. Not an ACP conversation.
native = Conversation(
    agent=Agent(llm=LLM(model="openai/qa-native", api_key="sk-qa"), tools=[]),
    workspace=str(workspace),
    visualizer=None,
)
result["native_error"] = error_of(native.switch_acp_effort, "high")
native.close()

# 6. Discovery reports the effort per model; settings carry acp_effort.
from openhands.sdk.agent.acp_model_discovery import discover_acp_models  # noqa: E402
from openhands.sdk.settings import ACPAgentSettings  # noqa: E402


d = discover_acp_models(
    ACPAgentSettings(acp_server="custom", acp_command=FAKE), work_root=str(work)
).model_dump(mode="json")
result["discovery"] = {
    "error": d.get("error"),
    "current_model_id": d.get("current_model_id"),
    "current_effort": d.get("current_effort"),
    "available_efforts": [o["value"] for o in d.get("available_efforts") or []],
    "efforts_by_model": {
        k: [o["value"] for o in v] for k, v in (d.get("efforts_by_model") or {}).items()
    },
}
settings = ACPAgentSettings(acp_server="custom", acp_command=FAKE, acp_effort="low")
result["settings_agent_effort"] = settings.create_agent().acp_effort
result["settings_roundtrip_effort"] = ACPAgentSettings.model_validate(
    settings.model_dump(mode="json")
).acp_effort

emit(result)
