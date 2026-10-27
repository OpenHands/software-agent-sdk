# PR #5794: remaining profile-store consumers

Baseline: `10b723b856bf089ae52c68cc1c4a5957892586e8`.
The working-tree fix makes settings update and scoped secret-list routes synchronous
FastAPI handlers and offloads the OpenAI models profile read with `asyncio.to_thread`.

Verification ran on macOS with Python 3.13.11, using an isolated real agent-server
process, real HTTP requests and a FileLock held by a separate process. No LLM calls
or mocked server responses were used. Both runs passed `control-agent-server doctor`.
This verifies the local server path; it does not exercise a deployed container.

The same three scenarios ran twice before and twice after the fix. A safety timer
releases each lock after five seconds. The fixed run explicitly checks that the
store request is still pending when `/alive` responds.

| Waiting request | Baseline `/alive`, ms | Fixed `/alive`, ms | Request status after release |
| --- | --- | --- | --- |
| GET /v1/models | 4350, 4351 | 25, 27 | 200 |
| PATCH /api/settings, active_profile=qa-missing | 4370, 4334 | 27, 25 | 404 |
| GET /api/settings/secrets?agent_profile_id=qa-missing | 4343, 4380 | 27, 26 | 404 |

Reproduce from the checkout root:

```sh
CLI=.agents/skills/verify-agent-server/scripts/control-agent-server
RUN=$($CLI launch --new --print-run)
$CLI doctor --run "$RUN"
uv run python .pr/verify_store_responsiveness.py "$RUN"
$CLI stop --run "$RUN"
```

The script saves redacted HTTP exchanges under the selected run's evidence/PR5794/.
The baseline exits 1 for all three latency assertions; the fix exits 0.

Three new real HTTP regression cases in
`tests/cross/test_remote_conversation_live_server.py` failed before the fix and
passed afterward. All 108 selected settings, OpenAI gateway, store responsiveness,
and live HTTP tests passed; pre-commit passed for every edited source/test file.
