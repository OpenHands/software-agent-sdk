# Unverified feature-map recipe proposal

This is a **draft** because `control-agent-server` process introspection is blocked in the
implementation environment. Do not paste it into the maintained map as verified evidence.
After the CLI launches and `doctor` passes, extend F04's Routes, Sub-features and entry points
with the operation below, execute this recipe, then add the maintained entry and evidence.
Add completion/cleanup/concurrency recipes using the existing fixture helpers as appropriate.

Proposed owner: F04 conversation lifecycle/catalog (uses existing parent/child links).
New route: `POST /api/conversations/{conversation_id}/children/wait`.
TypeScript entry point: `ConversationClient.waitForChildren`.

```sh
W="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-child-wait"
mkdir -p "$W"
BASE="{\"agent\":{\"llm\":{\"model\":\"openai/qa-offline\",\"api_key\":\"qa-key\",\"base_url\":\"http://127.0.0.1:9/v1\",\"num_retries\":0},\"tools\":[]},\"workspace\":{\"working_dir\":\"$W\"}}"
P=$(control-agent-server api POST /api/conversations --json "$BASE" --expect 201 --field id)
CHILD=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d["parent_conversation_id"]=sys.argv[2]; print(json.dumps(d))' "$BASE" "$P")
C=$(control-agent-server api POST /api/conversations --json "$CHILD" --expect 201 --field id)
control-agent-server api POST "/api/conversations/$P/children/wait" \
  --json "{\"conversation_ids\":[\"$C\"],\"timeout_seconds\":0}" \
  --expect 200 --check timed_out eq true --check interrupted eq false
control-agent-server api POST "/api/conversations/$P/children/wait" \
  --json "{\"conversation_ids\":[\"$P\"],\"timeout_seconds\":0}" --expect 404
control-agent-server api POST "/api/conversations/$P/children/wait" \
  --json "{\"conversation_ids\":[\"$C\"],\"timeout_seconds\":0,\"on_timeout\":\"delete\"}" \
  --expect 200 --check timed_out eq true
control-agent-server api GET "/api/conversations/$C" --expect 404
control-agent-server api DELETE "/api/conversations/$P" --expect 200
```

The exact CLI recipe above has not run. The route itself is proven through the real-socket
pytest integration, and 24 deterministic waiting cases run in the final 475-test server suite.
