#!/bin/bash
# Live end-to-end evidence for issue #4192.
# Flow: server A creates a conversation over REST -> SIGKILL A -> rewrite the
# lease as held by a vanished container hostname (unverifiable owner) ->
# restart server B on the same storage -> can B list the conversation AND
# serve its events?
# Run twice: OLD behavior (OH_LEASE_TTL_SECONDS=45) vs NEW default (unset).
set -u
cd "$(dirname "$0")/.."
PORT=8055
BASE="http://127.0.0.1:$PORT/api"

run_scenario() {
  local label="$1"; shift
  local tmp
  tmp=$(mktemp -d)
  echo "=== $label ==="

  env "$@" OH_CONVERSATIONS_PATH="$tmp/conversations" OPENHANDS_SUPPRESS_BANNER=1 \
    .venv/bin/python -m openhands.agent_server --port $PORT >"$tmp/serverA.log" 2>&1 &
  local pid_a=$!
  for _ in $(seq 1 60); do curl -sf "$BASE/health" >/dev/null 2>&1 && break; sleep 0.5; done

  local cid
  cid=$(curl -sf -X POST "$BASE/conversations" -H 'Content-Type: application/json' -d '{
    "agent": {"llm": {"model": "gpt-4o", "usage_id": "test-llm"}, "tools": []},
    "workspace": {"kind": "LocalWorkspace", "working_dir": "'"$tmp"'/ws"},
    "confirmation_policy": {"kind": "NeverConfirm"}
  }' | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  echo "created conversation: $cid"

  kill -9 $pid_a 2>/dev/null; wait $pid_a 2>/dev/null

  local lease="$tmp/conversations/$(echo "$cid" | tr -d -)/owner_lease.json"
  if [ -f "$lease" ]; then
    .venv/bin/python - "$lease" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["owner_host"] = "vanished-container-1234"
json.dump(d, open(p, "w"))
EOF
    echo "lease rewritten to foreign host (still live)"
  else
    echo "no lease file present (leasing disabled)"
  fi

  env "$@" OH_CONVERSATIONS_PATH="$tmp/conversations" OPENHANDS_SUPPRESS_BANNER=1 \
    .venv/bin/python -m openhands.agent_server --port $PORT >"$tmp/serverB.log" 2>&1 &
  local pid_b=$!
  for _ in $(seq 1 60); do curl -sf "$BASE/health" >/dev/null 2>&1 && break; sleep 0.5; done

  local n_listed events_status
  n_listed=$(curl -sf "$BASE/conversations/search" | .venv/bin/python -c 'import json,sys; print(len(json.load(sys.stdin)["items"]))')
  events_status=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/conversations/$cid/events/search")
  echo "restarted server: conversations listed=$n_listed events endpoint HTTP=$events_status"

  kill -9 $pid_b 2>/dev/null; wait $pid_b 2>/dev/null
  rm -rf "$tmp"
}

run_scenario "OLD default (leasing on, OH_LEASE_TTL_SECONDS=45)" OH_LEASE_TTL_SECONDS=45
run_scenario "NEW default (leasing disabled)"
