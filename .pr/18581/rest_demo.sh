#!/usr/bin/env bash
# Start a local agent server, create an ACP conversation whose CLI is missing,
# and print the ConversationErrorEvents it produces.
PORT=8013
DIR=$(mktemp -d)
env -u OH_WEBHOOKS_0_BASE_URL -u OH_LLM_API_KEY_REFRESH_URL -u OH_LLM_API_KEY_REFRESH_HEADERS \
  -u OH_LLM_API_KEY_REFRESH_BASE_URLS -u OH_INTERNAL_SERVER_URL -u OH_SESSION_API_KEYS_0 \
  OPENHANDS_SUPPRESS_BANNER=1 OH_ENABLE_VSCODE=false OH_ENABLE_VNC=false \
  OH_CONVERSATIONS_PATH="$DIR/convs" \
  uv run python -m openhands.agent_server --host 127.0.0.1 --port $PORT >"$DIR/server.log" 2>&1 &
PID=$!
for _ in $(seq 60); do curl -sf 127.0.0.1:$PORT/server_info >/dev/null && break; sleep 1; done
BODY='{"agent_settings":{"agent_kind":"acp","acp_server":"custom","acp_command":["my-acp-cli","--acp"]},"workspace":{"working_dir":"'"$DIR"'"},"initial_message":{"role":"user","content":[{"type":"text","text":"hi"}],"run":true}}'
echo "POST /api/conversations  acp_command=[\"my-acp-cli\",\"--acp\"]  initial_message.run=true"
CID=$(curl -s -X POST 127.0.0.1:$PORT/api/conversations -H 'content-type: application/json' -d "$BODY" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
sleep 4
curl -s 127.0.0.1:$PORT/api/conversations/$CID \
  | python3 -c "import sys,json;print('execution_status:', json.load(sys.stdin)['execution_status'])"
curl -s "127.0.0.1:$PORT/api/conversations/$CID/events/search?limit=100" | python3 -c "
import sys, json
errs = [e for e in json.load(sys.stdin)['items'] if e['kind'] == 'ConversationErrorEvent']
print(f'ConversationErrorEvents: {len(errs)}')
for e in errs:
    print(f\"  source={e['source']:<12} code={e['code']:<18} detail={e['detail']}\")
"
kill $PID
wait $PID 2>/dev/null
rm -rf "$DIR"
