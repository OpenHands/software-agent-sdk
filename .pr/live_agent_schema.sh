#!/bin/bash
# usage: live.sh <label>  -- start the Agent Server from the worktree, GET /api/settings/agent-schema, stop it
set -u
WT=/tmp/claude-0/-home-user/22a0973a-68bd-5e3e-a1e3-37a6e2c83102/scratchpad/wt/software-agent-sdk-5495
R=/tmp/claude-0/-home-user/22a0973a-68bd-5e3e-a1e3-37a6e2c83102/scratchpad/r5495
export UV_FROZEN=1 OPENHANDS_SUPPRESS_BANNER=1 SESSION_API_KEY=repro-5495-local-key
mkdir -p $R/srv-$1 && cd $R/srv-$1
"$WT/.venv/bin/python" -m openhands.agent_server --host 127.0.0.1 --port 18495 > $R/server-$1.log 2>&1 &
PID=$!
curl -sS -H "X-Session-API-Key: repro-5495-local-key" --retry-connrefused --retry 60 --retry-delay 1 -o $R/schema-$1.json -w "HTTP %{http_code}\n" http://127.0.0.1:18495/api/settings/agent-schema
kill $PID; wait $PID 2>/dev/null
"$WT/.venv/bin/python" - "$R/schema-$1.json" <<'PY'
import json, sys
body = json.load(open(sys.argv[1]))
sec = next(s for s in body["sections"] if s["key"] == "condenser")
print("GET /api/settings/agent-schema -> condenser section fields:")
for f in sec["fields"]:
    print(f"  {f['key']:48s} label={f['label']!r}")
kind = next((f for f in sec["fields"] if f["key"] == "condenser.condenser_kind"), None)
print("condenser.condenser_kind present:", kind is not None)
if kind:
    print("  description:", kind["description"])
    print("  choices:", [c["label"] for c in kind["choices"]])
PY
