"""Observe explicit attach against a missing disposable conversation."""

import json
from pathlib import Path
from uuid import uuid4

import httpx

from openhands.sdk import RemoteConversation, RemoteWorkspace


s = Path(__file__).resolve().parent
control = json.loads((s / "private-control.json").read_text())
cid = uuid4()
requests = []
with RemoteWorkspace(
    host="http://172.17.0.1:19109",
    api_key=control["session"],
    working_dir=str(s / "local/absent-workspace"),
) as workspace:
    workspace.client.event_hooks["request"].append(
        lambda request: requests.append(
            {"method": request.method, "path": request.url.path}
        )
    )
    try:
        RemoteConversation.attach(workspace, cid, visualizer=None)
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
    else:
        raise AssertionError("Missing attach unexpectedly succeeded")
assert status == 404
assert requests and all(row["method"] == "GET" for row in requests)
metadata = list((s / "local/conversations").rglob(str(cid))) + list(
    (s / "local/conversations").rglob(cid.hex)
)
assert not metadata, "Attach created persisted conversation state"
result = {
    "conversation_id": str(cid),
    "status": status,
    "requests": requests,
    "post_requests": 0,
    "persisted_conversation_created": False,
}
(s / "local/missing-attach.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
