"""Live check of ACPAgent.acp_effort + switch_acp_effort against pinned adapters.

Usage: effort_field_harness.py <provider> <model> <effort> <live-effort> <switch-model> <out.json> [proxy-log]
"""

import glob
import json
import os
import sys
import time

from openhands.sdk.agent import ACPAgent
from openhands.sdk.conversation import Conversation
from openhands.sdk.settings.acp_providers import ACP_PROVIDERS

provider, model, effort, live_effort, switch_model, out = sys.argv[1:7]
proxy_log = sys.argv[7] if len(sys.argv) > 7 else None
events: list[dict] = []


def emit(kind, **data):
    events.append({"kind": kind, **data})
    print(f"[{kind}] " + json.dumps(data, default=str), flush=True)


def state(agent):
    return {
        "model": agent.current_model_id,
        "effort": agent.current_effort,
        "levels": [o.value for o in agent.available_efforts],
    }


started = time.time()
agent = ACPAgent(
    acp_command=list(ACP_PROVIDERS[provider].default_command),
    acp_model=model,
    acp_effort=effort,
)
ok = True
try:
    conv = Conversation(agent=agent, workspace=os.getcwd(), visualizer=None)
    conv.send_message("Reply with exactly: OK")
    conv.run()
    emit("turn1", **state(conv.agent))

    conv.switch_acp_effort(live_effort)
    emit("after_switch_effort", acp_effort=conv.agent.acp_effort, **state(conv.agent))
    conv.send_message("Reply with exactly: OK2")
    conv.run()
    emit("turn2", **state(conv.agent))

    try:
        conv.switch_acp_effort("not-a-level")
        emit("bogus_effort", accepted=True)
    except ValueError as e:
        emit("bogus_effort", rejected=str(e)[:160])

    conv.switch_acp_model(switch_model)
    emit("after_switch_model", **state(conv.agent))
    conv.send_message("Reply with exactly: OK3")
    conv.run()
    emit("turn3", **state(conv.agent))
    session_id = conv.agent._session_id
except Exception as e:
    ok = False
    session_id = None
    emit("error", type=type(e).__name__, message=str(e)[:500])
finally:
    agent.close()

if provider == "codex" and session_id:
    home = os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))
    for f in glob.glob(f"{home}/sessions/**/rollout-*{session_id}.jsonl", recursive=True):
        turns = [
            (j["payload"].get("model"), j["payload"].get("effort"))
            for j in map(json.loads, open(f))
            if j.get("type") == "turn_context"
        ]
        emit("codex_rollout", file=os.path.basename(f), turns=turns)
if proxy_log and os.path.exists(proxy_log):
    emit(
        "anthropic_requests",
        requests=[
            (j["model"], (j.get("output_config") or {}).get("effort"), j["n_messages"])
            for j in map(json.loads, open(proxy_log))
        ],
    )
emit("result", ok=ok)
json.dump(events, open(out, "w"), indent=1, default=str)
