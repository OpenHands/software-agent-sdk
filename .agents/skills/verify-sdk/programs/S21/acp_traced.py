"""An ACP conversation traced to a local OTLP collector: the program sets the
endpoint before importing the SDK (as any traced program must), runs one ACP
turn against `fake_acp.py` whose prompt and replies carry a secret, and
reports the spans the collector received.

Usage: acp_traced.py
"""

import json
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "S20"))
from otlp import Collector, ancestors  # noqa: E402


collector = Collector().__enter__()
os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = collector.endpoint
os.environ["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] = "http/protobuf"

from sdk_verify import emit, run_paths  # noqa: E402

from openhands.sdk import Conversation  # noqa: E402
from openhands.sdk.agent import ACPAgent  # noqa: E402


HERE = Path(__file__).resolve().parent
paths = run_paths()
work = paths["run"] / "acp" / "traced"
work.mkdir(parents=True, exist_ok=True)
os.environ["FAKE_ACP_JOURNAL"] = str(work / "journal.jsonl")
paths["workspace"].mkdir(parents=True, exist_ok=True)

secret = "tok-trace-9931"
agent = ACPAgent(
    acp_command=[sys.executable, str(HERE / "fake_acp.py")],
    acp_server="custom",
    acp_model="m2",
)
conversation = Conversation(
    agent=agent,
    workspace=str(paths["workspace"]),
    visualizer=None,
    secrets={"FAKE_TOKEN": secret},
)
conversation.send_message(f"[secret] Use the token {secret} to list files.")
conversation.run()
status = conversation.state.execution_status.value
conversation.agent.close()
conversation.close()

from lmnr import Laminar  # noqa: E402


Laminar.flush()
collector.wait_for(lambda spans: any(s["name"] == "acp.completion" for s in spans))
spans = collector.spans
META = "lmnr.association.properties.metadata."
turns = [s for s in spans if s["name"] == "acp.completion"]
turn = turns[0] if turns else None
tools = [
    s
    for s in spans
    if s["attributes"].get("lmnr.span.type") == "TOOL"
    and str(s["attributes"].get(META + "agent_kind")) == "acp"
]
emit(
    {
        "ok": True,
        "status": status,
        "turn_spans": len(turns),
        "turn_type": turn["attributes"].get("lmnr.span.type") if turn else None,
        "turn_agent_kind": turn["attributes"].get(META + "agent_kind")
        if turn
        else None,
        "turn_acp_server": turn["attributes"].get(META + "acp_server")
        if turn
        else None,
        "turn_acp_model": turn["attributes"].get(META + "acp_model") if turn else None,
        "turn_under_conversation": bool(turn)
        and "conversation" in ancestors(spans, turn),
        "turn_output_has_reply": bool(turn)
        and "listed 2 files" in str(turn["attributes"].get("lmnr.span.output", "")),
        "turn_input_masked": bool(turn)
        and "<secret-hidden>" in str(turn["attributes"].get("lmnr.span.input", "")),
        "tool_span_names": sorted(s["name"] for s in tools),
        "tool_call_ids": sorted(
            str(s["attributes"].get(META + "tool_call_id")) for s in tools
        ),
        "tools_under_turn": bool(tools)
        and bool(turn)
        and all(s["parent"] == turn["span_id"] for s in tools),
        "tool_output_recorded": any(
            "file_a" in str(s["attributes"].get("lmnr.span.output", "")) for s in tools
        ),
        "secret_in_acp_spans": secret in json.dumps(turns + tools, default=str),
        # The generic conversation spans (S20) record the user's message as typed.
        "spans_with_secret": sorted(
            {
                f"{s['name']}:{k}"
                for s in spans
                for k, v in s["attributes"].items()
                if secret in str(v)
            }
        ),
    }
)
collector.__exit__()
