"""Query one actual failed conversation with exact before/after public clients."""

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path


r = Path(__file__).parent
p = argparse.ArgumentParser()
p.add_argument("conversation_id")
a = p.parse_args()
control = json.loads((r / "private-control.json").read_text())
rows = []
for label in ["before", "after"]:
    spec = importlib.util.spec_from_file_location(
        "evidence_client_" + label, r / ("agent_server_" + label + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = module.AgentServerClient("http://172.17.0.1:19109", control["session"])
    try:
        data = client.get_errors(a.conversation_id)
    finally:
        client.close()

    async def query():
        client = module.AsyncAgentServerClient(
            "http://172.17.0.1:19109", control["session"]
        )
        try:
            return await client.get_errors(a.conversation_id)
        finally:
            await client.aclose()

    async_data = asyncio.run(query())

    def safe(page):
        return [
            {
                "id": event.get("id"),
                "kind": event.get("kind"),
                "code": event.get("code"),
                "disclosed_fixture_error_present": "DELIBERATE_SDK_ERROR_EVIDENCE"
                in event.get("detail", ""),
            }
            for event in page.get("items", [])
        ]

    rows.append(
        {
            "client": label,
            "commit": "d76aa33d2" if label == "before" else "34ef2b29a",
            "conversation_id": a.conversation_id,
            "sync_errors": safe(data),
            "async_errors": safe(async_data),
        }
    )
assert not rows[0]["sync_errors"] and not rows[0]["async_errors"]
assert rows[1]["sync_errors"] and rows[1]["async_errors"]
assert rows[1]["sync_errors"][0]["disclosed_fixture_error_present"]
(r / "error-client-comparison.json").write_text(json.dumps(rows, indent=2))
print(json.dumps(rows, indent=2))
