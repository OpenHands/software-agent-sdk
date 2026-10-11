# Persistence, restart recovery, leases and limits

What a consumer can rely on across the server's own lifecycle, with no route
of its own: conversations, settings, secrets and LLM profiles live on disk
and read back unchanged after a graceful restart or a crash; a conversation
that was mid tool call when the process died comes back `error` with the
interrupted call answered; every loaded conversation is owned through an
`owner_lease.json` lease, so a second server sharing the same conversations
directory can read it but not drive it until the owner dies or stops
renewing; idle conversations are unloaded after a TTL and load again on
access; creating a conversation needs a free run slot; nothing secret is
stored in plaintext; and the configuration file and `OH_*` variables that set
these limits are applied (or refused) at startup.

Source: `openhands-agent-server/openhands/agent_server/conversation_lease.py`, `openhands-agent-server/openhands/agent_server/conversation_service.py`, `openhands-agent-server/openhands/agent_server/event_service.py`, `openhands-agent-server/openhands/agent_server/config.py`, `openhands-agent-server/openhands/agent_server/env_parser.py`, `openhands-agent-server/openhands/agent_server/api.py`, `openhands-agent-server/openhands/agent_server/persistence/`, `openhands-agent-server/openhands/agent_server/storage/`, `openhands-sdk/openhands/sdk/conversation/state.py`, `openhands-sdk/openhands/sdk/conversation/event_store.py`

Needs: `llm`, `tmux`

Routes: none

## Sub-features

- `F31.lease-claimed`: loading a conversation writes `owner_lease.json` in its directory with generation 1, the server's pid and host, and an expiry about 45 s ahead.
- `F31.lease-renewed`: while the server lives, the lease's `expires_at` moves forward within 15 s, with the same owner and generation.
- `F31.lease-released-graceful`: a graceful restart deletes the lease of every loaded conversation, catalog reads do not claim it again, and the next load claims generation 1 under the new pid.
- `F31.lease-dead-owner-takeover`: after a SIGKILL the lease still names the dead pid; the restarted server takes it over on first load with generation 2 and logs the takeover.
- `F31.crash-tool-call`: a conversation SIGKILLed during a tool call is loaded again at startup, comes back `error` with one `AgentErrorEvent` answering the interrupted call and no observation, and the resumed run hands that error to the model.
- `F31.state-survives-restarts`: a conversation's title, tags and events, the settings, a stored secret and the LLM profiles read back unchanged after a graceful restart and after a SIGKILL.
- `F31.no-plaintext-at-rest`: after a real model run, the provider key, a stored secret and a conversation secret appear nowhere in the run's persisted files or the server log, while the server still hands out the provider key and the stored secret and keeps the conversation secret encrypted in its registry.
- `F31.second-instance-catalog`: a second server sharing the conversations directory lists, counts and reads conversations that existed when it started, without claiming their leases.
- `F31.second-instance-lease-held`: while the owner lives, the second server cannot load the conversation: event reads, sending a message, `/run` and `agent_final_response` are 404, PATCH is `success: false`, pause, interrupt and DELETE are 400, its events socket closes 4004, and nothing changes.
- `F31.second-instance-takeover`: once the owner is SIGKILLed, the second server takes the conversation over at once (generation 2, its pid) with the same events, and the restarted owner is now the one refused.
- `F31.takeover-keeps-metadata`: a title the owner stored after the second server started survives the second server's takeover, in its GET and in `meta.json`.
- `F31.lease-expiry-fencing`: an owner that stops renewing (frozen, pid alive) loses the lease once it expires, and after it resumes its writes are refused and never reach disk.
- `F31.fenced-write-no-trace`: a write refused for lost ownership answers a 4xx and leaves no trace: the stale owner's own GET does not report the refused title.
- `F31.lease-disabled`: with `lease_ttl_seconds` 0 no lease file is written and a second server loads and writes the same conversation concurrently.
- `F31.idle-eviction`: an idle conversation is unloaded at the first eviction pass after its TTL (lease deleted, `Evicted idle` logged), stays listed with its title and status, and loads again with the same events on the next access.
- `F31.eviction-skips-busy`: the same pass keeps a conversation with an open events socket and a running one loaded; once the socket closes and the run is interrupted, the next pass unloads both.
- `F31.create-capacity`: with every run slot taken, `POST /api/conversations` answers 429 and creates nothing, on disk or in the catalog; the same request succeeds once the slot is free.
- `F31.config-precedence`: the configuration file is applied and an `OH_*` variable overrides the same field (seen through the lease TTL).
- `F31.config-null-override`: `OH_<FIELD>_IS_NONE=1` clears a value the configuration file sets, as a non-null `OH_*` value replaces it (seen through `app_backend_public_url` in `/server_info`).
- `F31.config-invalid-fails-fast`: a non-numeric `OH_MAX_CONCURRENT_RUNS` or an out-of-range limit in the configuration file stops the server during startup instead of serving with a default.
- `F31.config-silent-env`: an `OH_*` boolean other than `1`/`true` is read as false (it overrides a `true` in the configuration file) and a `Literal` variable outside its choices falls back to the default; the server starts either way, while the same invalid choice in the file stops startup.
- `F31.tmux-dir-cleanup`: a server that defaulted its own per-process `TMUX_TMPDIR` removes that directory when it stops.
- `F31.storage-retention`: with the Docker runtime, `conversation_storage_retention_days` archives a stopped conversation inactive past the limit (events readable, resume and proxied routes 410, runtime directory gone), and `conversation_storage_disk_budget` sheds a stopped workspace's gitignored directories while keeping its files (blocked here: no Docker daemon).

## How to get to it (agent POV)

- No route of its own: every bullet combines routes owned by other families
  (conversations and their events, PATCH, `/run`, pause, interrupt, DELETE,
  the events socket, settings, secrets, LLM profiles) with process events
  that a consumer only meets as an operator: a graceful restart (`restart`),
  a crash (`restart --hard`), a frozen process (`kill -STOP`), and a second
  server on the same conversations directory
  (`launch --new --share-conversations-from RUN`).
- Persisted layout, read with `state ls|cat|grep`: per conversation
  `<conversations_path>/<id hex>/meta.json` (title, tags, encrypted secrets),
  `base_state.json` (execution status, agent, encrypted secret registry),
  `events/` and, while it is loaded, `owner_lease.json`
  (`owner_instance_id`, `generation`, `expires_at`, `owner_host`,
  `owner_pid`) guarded by `.owner_lease.lock`; under `OH_PERSISTENCE_DIR`
  `settings.json`, `secrets.json` and `profiles/`. The server log
  (`logs --grep`) is the only view of eviction and takeover.
- Configuration (the JSON file at `OPENHANDS_AGENT_SERVER_CONFIG_PATH`,
  overridden by `OH_*` variables): `lease_ttl_seconds` /
  `OH_LEASE_TTL_SECONDS` (45; 0 disables leasing),
  `conversation_idle_ttl_seconds` / `OH_CONVERSATION_IDLE_TTL_SECONDS` (1200;
  `null`, or `OH_CONVERSATION_IDLE_TTL_SECONDS_IS_NONE=1` when the file does
  not set it, disables eviction),
  `max_concurrent_runs` / `OH_MAX_CONCURRENT_RUNS` (10), `OH_SECRET_KEY` (the
  cipher for secrets at rest), `TMUX_TMPDIR`, and with the Docker runtime
  only `conversation_storage_retention_days` and
  `conversation_storage_disk_budget` (unset by default). How any `OH_*`
  boolean or choice is parsed is seen through `deferred_init` /
  `OH_DEFERRED_INIT` and `conversation_runtime` / `OH_CONVERSATION_RUNTIME`
  (`F31.config-silent-env`). The CLI writes the file
  (`launch|restart --config-json`) and passes variables (`--env`);
  `control-agent-server config` shows both.
- SDK and TypeScript client: no method of their own. `RemoteConversation`
  reconnects its events socket after a restart and sees the statuses and
  events these bullets assert; an orchestrator that runs several servers on
  shared storage relies on the lease to keep one owner per conversation.
- Agent Canvas (context only): conversations listed after a sandbox
  restarts, and a `429` when a sandbox is at its run limit.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok,
  `$DEEPSEEK_API_KEY` is set (one real model run in `F31.no-plaintext-at-rest`),
  `tmux` is installed (the terminal tool in `F31.crash-tool-call`), and `jq`
  is on `PATH`.
- Many bullets restart this run (`restart`, `restart --hard`,
  `restart --reset-config --config-json ...`); later bullets set the config
  they need, so the run ends at its defaults. Bullets that need a second
  server launch it with `--share-conversations-from` and stop it before they
  end (a `trap` stops it if a command fails first); evidence saved with
  `--run "$B"` lands in that run's `evidence/`.
- The server's pid comes from the run's `run.json` (no verb prints it), and
  `kill -STOP`/`kill -CONT` freeze and resume this run's own server in
  `F31.lease-expiry-fencing`.
- The block below sets up DeepSeek flash, two secret values, the event kinds,
  two `llm-stub` providers (`qa-f31-tool` asks for `terminal` `sleep 300`;
  `qa-f31-hang` hangs every call for 600 s) with an inline agent for each, a
  workspace directory, and `T`, a conversation on the tool stub.

  ```sh
  control-agent-server llm preset deepseek
  export QA_F31_STORED=qa-f31-stored-secret-8c41
  export QA_F31_CONV=qa-f31-conversation-secret-5e07
  ACTION_KIND=openhands.sdk.event.llm_convertible.action.ActionEvent
  OBSERVATION_KIND=openhands.sdk.event.llm_convertible.observation.ObservationEvent
  AGENT_ERROR_KIND=openhands.sdk.event.llm_convertible.observation.AgentErrorEvent
  TOOL=$(control-agent-server fixture llm-stub --name qa-f31-tool --step 'tool:terminal:{"command":"sleep 300"}' --print-path)
  TOOL_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$TOOL/v1\", \"num_retries\": 0}, \"tools\": [{\"name\": \"terminal\"}]}"
  HANG=$(control-agent-server fixture llm-stub --name qa-f31-hang --step hang:600 --print-path)
  HANG_AGENT="{\"llm\": {\"model\": \"openai/qa-stub\", \"api_key\": \"qa-stub-key\", \"base_url\": \"$HANG/v1\", \"num_retries\": 0}, \"tools\": []}"
  QA_WS="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f31-workspace"
  mkdir -p "$QA_WS"
  T=$(control-agent-server conversation start --body-json "{\"agent\": $TOOL_AGENT}" --no-autotitle --print-id)
  ```

- **Lease claimed on load (`F31.lease-claimed`).** Creating a conversation
  loads it.
  ```sh
  L=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
  NOW=$(date +%s)
  PID=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  control-agent-server state cat owner_lease.json --conversation "$L" --check generation eq 1 --check owner_pid eq "$PID" \
    --check owner_host eq "$(hostname)" --check owner_instance_id matches '^[0-9a-f]{32}$' \
    --check expires_at gt "$((NOW + 40))" --check expires_at le "$((NOW + 46))"
  ```
  The lease names this server's pid and host, generation 1, and expires
  about 45 s after the create.
- **Lease renewed (`F31.lease-renewed`).** The server's renewal timer
  (every 15 s) is under test, so this bullet polls the file once a second.
  ```sh
  E1=$(control-agent-server state cat owner_lease.json --conversation "$L" | jq -r .content.expires_at)
  OWNER=$(control-agent-server state cat owner_lease.json --conversation "$L" | jq -r .content.owner_instance_id)
  T0=$SECONDS
  until control-agent-server state cat owner_lease.json --conversation "$L" --check expires_at gt "$E1" >/dev/null; do
    test $((SECONDS - T0)) -le 17
    sleep 1
  done
  control-agent-server state cat owner_lease.json --conversation "$L" --check expires_at gt "$E1" --check owner_instance_id eq "$OWNER" \
    --check generation eq 1 --check expires_at gt "$(($(date +%s) + 40))"
  ```
  Within 17 s `expires_at` moves forward to about 45 s from now; the owner
  and the generation stay.
- **Graceful release (`F31.lease-released-graceful`).** Restart, read the
  conversation from the catalog, then load it.
  ```sh
  PID1=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  control-agent-server restart
  PID2=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  test "$PID2" != "$PID1"
  control-agent-server state ls --conversation "$L" owner_lease.json --expect-count 0
  control-agent-server api GET "/api/conversations/$L" --expect 200 --check id eq "$L" --quiet
  control-agent-server state ls --conversation "$L" owner_lease.json --expect-count 0
  control-agent-server api PATCH "/api/conversations/$L" --json '{"title": "QA F31 lease"}' --expect 200 --check success eq true
  control-agent-server state cat owner_lease.json --conversation "$L" --check generation eq 1 --check owner_pid eq "$PID2"
  ```
  Shutdown deleted the lease; GET (a catalog read) does not load the
  conversation; PATCH loads it and claims a fresh generation 1 under the new
  pid.
- **Dead owner takeover (`F31.lease-dead-owner-takeover`).** SIGKILL the
  owner; nothing releases the lease.
  ```sh
  DEAD=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  NT=$(control-agent-server logs --grep 'Taking over conversation' | jq .total_matching)
  control-agent-server restart --hard
  if kill -0 "$DEAD" 2>/dev/null; then false; fi
  PID3=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  control-agent-server state cat owner_lease.json --conversation "$L" --check owner_pid eq "$DEAD" --check generation eq 1 \
    --check expires_at gt "$(date +%s)"
  control-agent-server api GET "/api/conversations/$L/events/count" --expect 200 --save F31.lease-dead-owner-takeover/first-load
  control-agent-server state cat owner_lease.json --conversation "$L" --check owner_pid eq "$PID3" --check generation eq 2
  control-agent-server logs --grep 'Taking over conversation' --expect-min $((NT + 1))
  ```
  The leftover lease names the dead pid and is still nominally valid; the
  first load on the restarted server takes it over at once (same host, pid
  gone) as generation 2 and logs `Taking over conversation lease ... from
  dead owner`.
- **Crash during a tool call (`F31.crash-tool-call`).** `T`'s model asks for
  `terminal` `sleep 300`; the server is SIGKILLed while it runs, then the
  stub is rewritten to call `finish` and the run resumes.
  ```sh
  control-agent-server conversation send "$T" --text 'hi' --no-run
  control-agent-server api POST "/api/conversations/$T/run" --expect 200 --check success eq true
  control-agent-server api GET "/api/conversations/$T/events/count" --query kind=$ACTION_KIND --check . eq 1 --until-ok 30
  TCALL=$(control-agent-server api GET "/api/conversations/$T/events/search" --query kind=$ACTION_KIND --field items.0.tool_call_id)
  control-agent-server state cat base_state.json --conversation "$T" --check execution_status eq running
  control-agent-server restart --hard
  PIDT=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  control-agent-server state cat owner_lease.json --conversation "$T" --check owner_pid eq "$PIDT"
  control-agent-server api GET "/api/conversations/$T" --check execution_status eq error --quiet --save F31.crash-tool-call/get
  control-agent-server api GET "/api/conversations/$T/events/search" --query kind=$AGENT_ERROR_KIND --check items len-eq 1 \
    --check items.0.tool_call_id eq "$TCALL" --check items.0.error contains 'A restart occurred while this tool was in progress' \
    --save F31.crash-tool-call/agent-error
  control-agent-server api GET "/api/conversations/$T/events/count" --query kind=$OBSERVATION_KIND --check . eq 0
  control-agent-server fixture llm-stub --name qa-f31-tool --step 'tool:finish:{"message":"QA_F31_RESUMED"}'
  control-agent-server api POST "/api/conversations/$T/run" --expect 200 --check success eq true
  control-agent-server conversation wait "$T" --until finished --timeout 60
  control-agent-server api GET "/api/conversations/$T/agent_final_response" --check response eq QA_F31_RESUMED
  control-agent-server sink read --name qa-f31-tool --contains 'A restart occurred while this tool was in progress' --expect-min 1 \
    --save F31.crash-tool-call/resumed-request
  ```
  `base_state.json` said `running` when the server died. Before any request,
  startup loaded `T` (its lease already names the new pid), marked it
  `error` and appended one `AgentErrorEvent` for the open `tool_call_id`
  ("A restart occurred while this tool was in progress ..."), with no
  observation. The resumed model request carries that error as the tool
  result, and `T` finishes.
- **State survives restarts (`F31.state-survives-restarts`).** One
  conversation, a settings field, a stored secret and the two DeepSeek
  profiles, read back after a graceful restart and again after a SIGKILL.
  ```sh
  ST=$(control-agent-server conversation start --tools none --no-autotitle --title 'QA F31 state' --tag env=qa-f31 --prompt 'qa-f31-state-marker' --no-run --print-id)
  NS=$(control-agent-server api GET "/api/conversations/$ST/events/count" --field .)
  control-agent-server api PATCH /api/settings --json '{"misc_settings_diff": {"qa_f31": "kept"}}' --expect 200 --check misc_settings.qa_f31 eq kept
  control-agent-server api PUT /api/settings/secrets --json "{\"name\": \"QA_F31_STORED\", \"value\": \"$QA_F31_STORED\"}" --expect 200
  for MODE in graceful hard; do
    if [ "$MODE" = hard ]; then control-agent-server restart --hard; else control-agent-server restart; fi
    control-agent-server api GET "/api/conversations/$ST" --check title eq 'QA F31 state' --check tags.env eq qa-f31 \
      --check execution_status eq idle --quiet --save "F31.state-survives-restarts/conversation-$MODE"
    control-agent-server api GET "/api/conversations/$ST/events/count" --check . eq "$NS"
    control-agent-server conversation events "$ST" --kinds MessageEvent --contains qa-f31-state-marker --expect-count 1
    control-agent-server api GET /api/settings --check misc_settings.qa_f31 eq kept --check agent_settings.llm.model eq deepseek/deepseek-flash --quiet
    control-agent-server api GET /api/settings/secrets/QA_F31_STORED --check . eq "$QA_F31_STORED"
    control-agent-server api GET /api/profiles --check active_profile eq deepseek-flash --check profiles len-eq 2 --save "F31.state-survives-restarts/profiles-$MODE"
  done
  ```
  Both times the title, tag, `idle` status, event count and queued message,
  the misc setting and the active model, the secret's value and both
  profiles read back unchanged.
- **Nothing secret in plaintext (`F31.no-plaintext-at-rest`).** A real
  DeepSeek run with a conversation secret, then a search of every file the
  server wrote (`home/`, which holds `OH_PERSISTENCE_DIR`, and `server/`, which
  holds the conversations) and of the server log.
  ```sh
  control-agent-server api PUT /api/settings/secrets --json "{\"name\": \"QA_F31_STORED\", \"value\": \"$QA_F31_STORED\"}" --expect 200
  M=$(control-agent-server conversation start --tools none --no-autotitle --secret "QA_F31_CONV=$QA_F31_CONV" \
    --prompt 'Reply with the single word ok, then finish.' --wait --until finished --timeout 180 --print-id)
  control-agent-server api GET "/api/conversations/$M" --check execution_status eq finished --quiet
  control-agent-server state cat base_state.json --conversation "$M" --check secret_registry.secret_sources.QA_F31_CONV.value exists \
    --not-contains "$QA_F31_CONV"
  control-agent-server state grep QA_F31_CONV --glob 'server/workspace/conversations/**/*'
  control-agent-server api GET /api/settings --header 'X-Expose-Secrets: plaintext' --check agent_settings.llm.api_key eq "$DEEPSEEK_API_KEY" --quiet
  control-agent-server api GET /api/settings/secrets/QA_F31_STORED --check . eq "$QA_F31_STORED"
  for NAME in DEEPSEEK_API_KEY QA_F31_STORED QA_F31_CONV; do
    control-agent-server state grep --env-value "$NAME" --glob 'home/**/*' --expect-none
    control-agent-server state grep --env-value "$NAME" --glob 'server/**/*' --expect-none
  done
  control-agent-server logs --grep "$DEEPSEEK_API_KEY" --expect-none
  control-agent-server logs --grep "$QA_F31_STORED" --expect-none
  control-agent-server logs --grep "$QA_F31_CONV" --expect-none
  ```
  The run finished on DeepSeek flash. The secret registry stores the
  conversation secret's value (encrypted), the secret's name is found in the
  conversation files (the search works), and the server still hands out the
  provider key and the stored secret. None of the three values appears in
  any persisted file (settings, secrets, profiles, `meta.json`,
  `base_state.json`, events) or in the server log. No route reads a
  conversation secret back, so its decryption is not observed here.
- **Second server reads the catalog (`F31.second-instance-catalog`).** `X`
  is loaded on this server; `B` starts on the same conversations directory.
  ```sh
  X=$(control-agent-server conversation start --tools none --no-autotitle --title 'QA F31 shared' --prompt 'qa-f31-shared-marker' --no-run --print-id)
  COLD=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$X/events/count" --expect 200
  PIDA=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  NC=$(control-agent-server api GET /api/conversations/count --field .)
  B=$(control-agent-server launch --new --name f31-catalog --share-conversations-from "$AGENT_SERVER_VERIFY_RUN" --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  control-agent-server api GET /api/conversations/count --run "$B" --check . eq "$NC" --save F31.second-instance-catalog/count
  control-agent-server api GET /api/conversations/search --run "$B" --query limit=100 --check items contains "$X" --check items contains "$COLD" --quiet
  control-agent-server api GET "/api/conversations/$X" --run "$B" --expect 200 --check title eq 'QA F31 shared' --check execution_status eq idle \
    --save F31.second-instance-catalog/get
  control-agent-server api GET "/api/conversations/$COLD" --run "$B" --expect 200 --check id eq "$COLD" --quiet
  control-agent-server state cat owner_lease.json --conversation "$X" --check owner_pid eq "$PIDA" --check generation eq 1
  control-agent-server state ls --conversation "$COLD" owner_lease.json --expect-count 0
  control-agent-server stop --run "$B"
  trap - EXIT
  ```
  `B` counts and lists the same conversations and reads `X`'s title and
  status from disk. Its reads claim nothing: `X`'s lease still names this
  server, and `COLD` (unloaded since the restart) still has no lease.
- **Second server refused while the owner lives (`F31.second-instance-lease-held`).**
  A fresh `B` tries every way of loading `X`.
  ```sh
  B=$(control-agent-server launch --new --name f31-held --share-conversations-from "$AGENT_SERVER_VERIFY_RUN" --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  OWNER=$(control-agent-server state cat owner_lease.json --conversation "$X" | jq -r .content.owner_instance_id)
  NX=$(control-agent-server api GET "/api/conversations/$X/events/count" --field .)
  control-agent-server api GET "/api/conversations/$X/events/search" --run "$B" --expect 404 --check detail eq "Conversation not found: $X" \
    --save F31.second-instance-lease-held/events-search
  control-agent-server api GET "/api/conversations/$X/events/count" --run "$B" --expect 404
  control-agent-server api POST "/api/conversations/$X/events" --run "$B" --json '{"role": "user", "content": [{"type": "text", "text": "qa-f31-from-b"}], "run": false}' --expect 404
  control-agent-server api POST "/api/conversations/$X/run" --run "$B" --expect 404 --save F31.second-instance-lease-held/run
  control-agent-server api GET "/api/conversations/$X/agent_final_response" --run "$B" --expect 404
  control-agent-server api PATCH "/api/conversations/$X" --run "$B" --json '{"title": "QA F31 from B"}' --expect 200 --check success eq false \
    --save F31.second-instance-lease-held/patch
  control-agent-server api POST "/api/conversations/$X/pause" --run "$B" --expect 400
  control-agent-server api POST "/api/conversations/$X/interrupt" --run "$B" --expect 400
  control-agent-server api DELETE "/api/conversations/$X" --run "$B" --expect 400 --save F31.second-instance-lease-held/delete
  control-agent-server ws listen "/sockets/events/$X" --run "$B" --expect-close 4004 --duration 10 --save F31.second-instance-lease-held/socket
  control-agent-server api GET "/api/conversations/$X" --run "$B" --expect 200 --check title eq 'QA F31 shared' --quiet
  control-agent-server state cat owner_lease.json --conversation "$X" --check owner_instance_id eq "$OWNER" --check generation eq 1
  control-agent-server api GET "/api/conversations/$X" --check title eq 'QA F31 shared' --quiet
  control-agent-server api GET "/api/conversations/$X/events/count" --check . eq "$NX"
  control-agent-server state grep qa-f31-shared-marker --glob "server/workspace/conversations/${X//-/}/**/*"
  control-agent-server state grep qa-f31-from-b --glob "server/workspace/conversations/${X//-/}/**/*" --expect-none
  control-agent-server stop --run "$B"
  trap - EXIT
  ```
  Every loading call on `B` reports the conversation as missing (404, 400 or
  `success: false`, socket close 4004 `Conversation not found`), the same
  answers an unknown id gets, although `B`'s GET still reads it from the
  catalog. The lease, the title, the event count and the event log on the
  owner are unchanged, and `X` was not deleted.
- **Takeover after the owner dies (`F31.second-instance-takeover`).** `B`
  waits while this server is SIGKILLed and restarted.
  ```sh
  B=$(control-agent-server launch --new --name f31-takeover --share-conversations-from "$AGENT_SERVER_VERIFY_RUN" --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  PIDB=$(jq -r .pid "$B/run.json")
  NX=$(control-agent-server api GET "/api/conversations/$X/events/count" --field .)
  NB=$(control-agent-server logs --run "$B" --grep 'Taking over conversation' | jq .total_matching)
  control-agent-server api GET "/api/conversations/$X/events/count" --run "$B" --expect 404
  control-agent-server restart --hard
  control-agent-server api GET "/api/conversations/$X/events/count" --run "$B" --expect 200 --check . eq "$NX" --save F31.second-instance-takeover/events-count
  control-agent-server state cat owner_lease.json --conversation "$X" --check owner_pid eq "$PIDB" --check generation eq 2
  control-agent-server logs --run "$B" --grep 'Taking over conversation' --expect-min $((NB + 1))
  control-agent-server conversation events "$X" --run "$B" --kinds MessageEvent --contains qa-f31-shared-marker --expect-count 1
  control-agent-server api GET "/api/conversations/$X/events/count" --expect 404 --save F31.second-instance-takeover/old-owner-refused
  control-agent-server stop --run "$B"
  trap - EXIT
  control-agent-server state ls --conversation "$X" owner_lease.json --expect-count 0
  ```
  Refused a moment before, `B` loads `X` right after the SIGKILL (same host,
  dead pid, lease not yet expired) as generation 2 with the same events; the
  restarted owner now gets 404. `B`'s graceful stop releases the lease.
- **Takeover keeps newer metadata (`F31.takeover-keeps-metadata`), known bug.**
  The owner renames `Y` after `B` started; then the owner dies and `B` takes
  `Y` over.
  ```sh
  Y=$(control-agent-server conversation start --tools none --no-autotitle --title 'QA F31 before B' --no-run --print-id)
  B=$(control-agent-server launch --new --name f31-meta --share-conversations-from "$AGENT_SERVER_VERIFY_RUN" --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  PIDB=$(jq -r .pid "$B/run.json")
  control-agent-server api PATCH "/api/conversations/$Y" --json '{"title": "QA F31 after B"}' --expect 200 --check success eq true
  control-agent-server state cat meta.json --conversation "$Y" --check title eq 'QA F31 after B'
  control-agent-server restart --hard
  control-agent-server api GET "/api/conversations/$Y/events/count" --run "$B" --expect 200
  control-agent-server state cat owner_lease.json --conversation "$Y" --check owner_pid eq "$PIDB" --check generation eq 2
  control-agent-server state cat meta.json --conversation "$Y" --check title eq 'QA F31 after B'  # bug
  control-agent-server api GET "/api/conversations/$Y" --run "$B" --check title eq 'QA F31 after B' --save F31.takeover-keeps-metadata/get-after-takeover  # bug
  ```
  Expected: `B` reports and keeps the title the owner stored last. The
  owner's rename reached `meta.json` and `B`'s first load took `Y` over
  (generation 2 under `B`'s pid) before the two `# bug` assertions. Today
  `B` loads `Y` from the catalog snapshot it read at its own startup and
  saves it right away (`_start_event_service` calls `save_meta()`; nothing
  calls `EventService.load_meta()`), so `meta.json` is rewritten with
  `QA F31 before B` (the first assertion fails there) and `B`'s GET reports
  it too: the owner's later title (and `updated_at`) is lost. Takeover is
  not required: a plain generation 1 load on `B` after the owner released
  `Y` with a graceful restart (or an idle eviction) reverts it the same way.
  The `trap` stops `B`.
- **Lease expiry fences a frozen owner (`F31.lease-expiry-fencing`).** With
  a 20 s lease, this server is frozen with SIGSTOP so it stops renewing
  while its pid stays alive.
  ```sh
  control-agent-server restart --reset-config --config-json '{"lease_ttl_seconds": 20}'
  F=$(control-agent-server conversation start --tools none --no-autotitle --title 'QA F31 fenced' --prompt 'qa-f31-fence-marker' --no-run --print-id)
  PIDA=$(jq -r .pid "$AGENT_SERVER_VERIFY_RUN/run.json")
  control-agent-server state cat owner_lease.json --conversation "$F" --check owner_pid eq "$PIDA" --check generation eq 1 \
    --check expires_at le "$(($(date +%s) + 21))"
  B=$(control-agent-server launch --new --name f31-fence --share-conversations-from "$AGENT_SERVER_VERIFY_RUN" --print-run)
  thaw_and_stop() {
    kill -CONT "$PIDA" 2>/dev/null || true
    control-agent-server stop --run "$B" >/dev/null 2>&1 || true
  }
  trap thaw_and_stop EXIT
  PIDB=$(jq -r .pid "$B/run.json")
  NF=$(control-agent-server api GET "/api/conversations/$F/events/count" --field .)
  control-agent-server api GET "/api/conversations/$F/events/count" --run "$B" --expect 404
  kill -STOP "$PIDA"
  T0=$SECONDS
  until control-agent-server state cat owner_lease.json --conversation "$F" --check expires_at lt "$(date +%s)" >/dev/null; do
    test $((SECONDS - T0)) -le 30
    sleep 1
  done
  control-agent-server api GET "/api/conversations/$F/events/count" --run "$B" --expect 200 --check . eq "$NF"
  control-agent-server state cat owner_lease.json --conversation "$F" --check owner_pid eq "$PIDB" --check generation eq 2
  kill -CONT "$PIDA"
  control-agent-server api GET /alive --auth none --expect 200
  control-agent-server api POST "/api/conversations/$F/events" --json '{"role": "user", "content": [{"type": "text", "text": "qa-f31-stale-write"}], "run": false}' \
    --expect 4xx,5xx --save F31.lease-expiry-fencing/stale-write
  control-agent-server api GET "/api/conversations/$F/events/count" --check . eq "$NF"
  control-agent-server state grep qa-f31-fence-marker --glob "server/workspace/conversations/${F//-/}/**/*"
  control-agent-server state grep qa-f31-stale-write --glob "server/workspace/conversations/${F//-/}/**/*" --expect-none
  control-agent-server api GET "/api/conversations/$F/events/count" --run "$B" --check . eq "$NF"
  control-agent-server api PATCH "/api/conversations/$F" --run "$B" --json '{"title": "QA F31 new owner"}' --expect 200 --check success eq true
  control-agent-server state cat meta.json --conversation "$F" --check title eq 'QA F31 new owner'
  control-agent-server stop --run "$B"
  trap - EXIT
  ```
  `B` is refused while the lease is valid; once it expires (the frozen pid is
  alive, so there is no early takeover) `B` takes `F` over as generation 2.
  Resumed, the old owner still has `F` loaded, but its message is refused:
  nothing of it reaches disk or the old owner's own event count, while
  `B`'s write lands. Any error status passes here; which one it should be
  is `F31.fenced-write-no-trace`. `B`'s stop releases the lease.
- **Refused write leaves no trace (`F31.fenced-write-no-trace`), known bug.**
  The old owner from the previous bullet tries to rename `F`.
  ```sh
  PS=$(control-agent-server api PATCH "/api/conversations/$F" --json '{"title": "QA F31 stale owner"}' --expect 4xx,5xx \
    --save F31.fenced-write-no-trace/patch | jq .status)
  control-agent-server state cat meta.json --conversation "$F" --check title eq 'QA F31 new owner'
  control-agent-server api GET "/api/conversations/$F" --check title ne 'QA F31 stale owner' --save F31.fenced-write-no-trace/get  # bug
  echo "refused PATCH answered $PS"
  test "$PS" -lt 500  # bug
  ```
  Expected: a client error that says the conversation is owned elsewhere,
  and no visible change. The refusal itself (any error status) and
  `meta.json` keeping `QA F31 new owner` are the positive controls: the
  guarded save did refuse the write. Today the stale owner's own GET reports
  `QA F31 stale owner` (the first `# bug` assertion fails there): the title
  was set in memory before the guarded save failed. The PATCH status, kept
  in `PS` and checked last, is 500 `Internal Server Error` with `exception`
  `conversation ownership was lost before the write completed` (the server
  maps `ConversationOwnershipLostError` to nothing; the saved exchange shows
  it). The stale owner keeps the conversation loaded and refuses every
  write the same way until it is evicted or restarted, even after the new
  owner stops and the lease file is gone, and meanwhile serves its stale
  in-memory events (its count stays put while the new owner appends).
- **Leasing disabled (`F31.lease-disabled`).** Both servers run with
  `lease_ttl_seconds` 0.
  ```sh
  control-agent-server restart --reset-config --config-json '{"lease_ttl_seconds": 0}'
  D=$(control-agent-server conversation start --tools none --no-autotitle --title 'QA F31 unleased' --prompt 'qa-f31-unleased-marker' --no-run --print-id)
  control-agent-server state ls --conversation "$D" meta.json --expect-count 1
  control-agent-server state ls --conversation "$D" owner_lease.json --expect-count 0
  ND=$(control-agent-server api GET "/api/conversations/$D/events/count" --field .)
  B=$(control-agent-server launch --new --name f31-unleased --share-conversations-from "$AGENT_SERVER_VERIFY_RUN" --config-json '{"lease_ttl_seconds": 0}' --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  control-agent-server api POST "/api/conversations/$D/events" --run "$B" --json '{"role": "user", "content": [{"type": "text", "text": "qa-f31-from-b"}], "run": false}' \
    --expect 200 --save F31.lease-disabled/second-server-write
  control-agent-server api GET "/api/conversations/$D/events/count" --run "$B" --check . gt "$ND"
  control-agent-server state grep qa-f31-from-b --glob "server/workspace/conversations/${D//-/}/**/*"
  control-agent-server api PATCH "/api/conversations/$D" --json '{"title": "QA F31 unleased A"}' --expect 200 --check success eq true
  control-agent-server api GET "/api/conversations/$D/events/count" --check . eq "$ND"
  control-agent-server state ls --conversation "$D" owner_lease.json --expect-count 0
  control-agent-server stop --run "$B"
  trap - EXIT
  ```
  No lease file is ever written; `B` loads `D` and appends a message (on
  disk too) while this server keeps it loaded and renames it. Their in-memory views now
  differ (this server still counts `ND` events): leasing off is only safe
  with one server per conversations directory.
- **Idle eviction (`F31.idle-eviction`).** A 5 s idle TTL; the eviction loop
  runs every `max(60, ttl/2)` seconds, first about 60 s after startup, so
  this bullet polls for up to 90 s. It also arranges the two busy
  conversations of the next bullet before the pass: `WB` with an open events
  socket and `R` running on the hanging stub.
  ```sh
  control-agent-server restart --reset-config --config-json '{"conversation_idle_ttl_seconds": 5}'
  NE=$(control-agent-server logs --grep 'Evicted idle' | jq .total_matching)
  I=$(control-agent-server conversation start --tools none --no-autotitle --title 'QA F31 idle' --prompt 'qa-f31-idle-marker' --no-run --print-id)
  NI=$(control-agent-server api GET "/api/conversations/$I/events/count" --field .)
  WB=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
  R=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
  control-agent-server ws start "/sockets/events/$WB" --name f31-subscribed --duration 300
  control-agent-server conversation send "$R" --text 'hi' --no-run
  control-agent-server api POST "/api/conversations/$R/run" --expect 200 --check success eq true
  control-agent-server conversation wait "$R" --until running --timeout 60
  control-agent-server state ls --conversation "$I" owner_lease.json --expect-count 1
  T0=$SECONDS
  until control-agent-server state ls --conversation "$I" owner_lease.json --expect-count 0 >/dev/null; do
    test $((SECONDS - T0)) -le 90
    sleep 2
  done
  control-agent-server logs --grep 'Evicted idle' --expect-min $((NE + 1))
  control-agent-server api GET "/api/conversations/$I" --check title eq 'QA F31 idle' --check execution_status eq idle --quiet --save F31.idle-eviction/get-evicted
  control-agent-server api GET /api/conversations/search --query status=idle --query limit=100 --check items contains "$I" --quiet
  control-agent-server state ls --conversation "$I" owner_lease.json --expect-count 0
  control-agent-server api GET "/api/conversations/$I/events/count" --check . eq "$NI" --save F31.idle-eviction/events-after-reload
  control-agent-server conversation events "$I" --kinds MessageEvent --contains qa-f31-idle-marker --expect-count 1
  control-agent-server state cat owner_lease.json --conversation "$I" --check generation eq 1
  ```
  The first pass unloads `I` (its lease is deleted and `Evicted idle
  conversation` is logged); GET and search still list it with its title and
  `idle` status without loading it, and the next event read loads it again
  (a fresh generation 1 lease) with the same events.
- **Busy conversations are kept (`F31.eviction-skips-busy`).** Right after
  the pass that unloaded `I`; then both are released and the next pass
  (up to about 60 s later) is awaited.
  ```sh
  control-agent-server state ls --conversation "$WB" owner_lease.json --expect-count 1
  control-agent-server state ls --conversation "$R" owner_lease.json --expect-count 1
  control-agent-server ws read f31-subscribed --expect-open
  control-agent-server api GET "/api/conversations/$R" --check execution_status eq running --quiet
  control-agent-server ws stop f31-subscribed --expect-kind ConversationStateUpdateEvent --save F31.eviction-skips-busy/socket
  control-agent-server api POST "/api/conversations/$R/interrupt" --expect 200 --check success eq true
  T0=$SECONDS
  until control-agent-server state ls --conversation "$WB" owner_lease.json --expect-count 0 >/dev/null; do
    test $((SECONDS - T0)) -le 75
    sleep 2
  done
  control-agent-server state ls --conversation "$R" owner_lease.json --expect-count 0
  control-agent-server api GET "/api/conversations/$R" --check execution_status eq paused --quiet --save F31.eviction-skips-busy/released
  ```
  Both kept their lease through the pass that evicted `I` (an attached
  events socket and an active run block eviction). Once the socket is closed
  and the run interrupted, the next pass unloads both; `R` stays listed as
  `paused`.
- **Create at capacity (`F31.create-capacity`).** One run slot, held by a
  conversation on the hanging stub.
  ```sh
  control-agent-server restart --reset-config --config-json '{"max_concurrent_runs": 1}'
  C=$(control-agent-server conversation start --body-json "{\"agent\": $HANG_AGENT}" --no-autotitle --print-id)
  NC=$(control-agent-server api GET /api/conversations/count --field .)
  NM=$(control-agent-server state ls 'server/workspace/conversations/*/meta.json' | jq .count)
  control-agent-server conversation send "$C" --text 'hi' --no-run
  control-agent-server api POST "/api/conversations/$C/run" --expect 200 --check success eq true
  control-agent-server conversation wait "$C" --until running --timeout 60
  NEW=$(python3 -c 'import uuid; print(uuid.uuid4())')
  control-agent-server api POST /api/conversations --json "{\"conversation_id\": \"$NEW\", \"workspace\": {\"working_dir\": \"$QA_WS\"}, \"agent\": $HANG_AGENT}" \
    --expect 429 --check detail eq 'Conversation run limit reached. Retry the request later.' --save F31.create-capacity/create-429
  control-agent-server api GET "/api/conversations/$NEW" --expect 404
  control-agent-server api GET /api/conversations/count --check . eq "$NC"
  control-agent-server state ls 'server/workspace/conversations/*/meta.json' --expect-count "$NM"
  control-agent-server state ls --conversation "$NEW" '**/*' --expect-count 0
  control-agent-server api POST "/api/conversations/$C/interrupt" --expect 200 --check success eq true
  control-agent-server api POST /api/conversations --json "{\"conversation_id\": \"$NEW\", \"workspace\": {\"working_dir\": \"$QA_WS\"}, \"agent\": $HANG_AGENT}" \
    --expect 201 --check id eq "$NEW" --save F31.create-capacity/create-after-release
  control-agent-server api GET /api/conversations/count --check . eq "$((NC + 1))"
  control-agent-server api DELETE "/api/conversations/$NEW" --expect 200 --check success eq true
  ```
  While `C` runs, the create is 429 with a retry hint and leaves no
  conversation: the id is 404, the count and the `meta.json` files are
  unchanged and no directory was made. After the interrupt frees the slot,
  the same request creates `NEW` (deleted again).
- **Config file and environment (`F31.config-precedence`).** The lease TTL
  set in the configuration file, then overridden by `OH_LEASE_TTL_SECONDS`.
  ```sh
  control-agent-server restart --reset-config --config-json '{"lease_ttl_seconds": 30}'
  control-agent-server config | jq -e '.config_file.lease_ttl_seconds == 30 and .server_env.OH_LEASE_TTL_SECONDS == null'
  P1=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
  NOW=$(date +%s)
  control-agent-server state cat owner_lease.json --conversation "$P1" --check expires_at gt "$((NOW + 25))" --check expires_at le "$((NOW + 31))"
  control-agent-server restart --env OH_LEASE_TTL_SECONDS=90
  control-agent-server config | jq -e '.config_file.lease_ttl_seconds == 30 and .server_env.OH_LEASE_TTL_SECONDS == "90"'
  P2=$(control-agent-server conversation start --tools none --no-autotitle --print-id)
  NOW=$(date +%s)
  control-agent-server state cat owner_lease.json --conversation "$P2" --check expires_at gt "$((NOW + 85))" --check expires_at le "$((NOW + 91))"
  ```
  With only the file, a new lease expires about 30 s ahead; with the file
  still saying 30 and the variable saying 90, about 90 s ahead.
- **Null from the environment (`F31.config-null-override`), known bug.**
  The file sets `app_backend_public_url` (reported by `/server_info` as
  `app_backend_ingress_url`, an instant view of a nullable field); a
  non-null variable replaces it, then `_IS_NONE=1` should clear it.
  ```sh
  control-agent-server restart --reset-config --config-json '{"app_backend_public_url": "http://qa-f31-file.invalid:9"}'
  control-agent-server api GET /server_info --auth none --check app_backend_ingress_url eq http://qa-f31-file.invalid:9 \
    --check capabilities contains canvas_app_backend_bridge_v1
  control-agent-server restart --env OH_APP_BACKEND_PUBLIC_URL=http://qa-f31-env.invalid:9
  control-agent-server api GET /server_info --auth none --check app_backend_ingress_url eq http://qa-f31-env.invalid:9
  control-agent-server restart --reset-config --config-json '{"app_backend_public_url": "http://qa-f31-file.invalid:9"}' \
    --env OH_APP_BACKEND_PUBLIC_URL_IS_NONE=1
  control-agent-server config | jq -e '.config_file.app_backend_public_url == "http://qa-f31-file.invalid:9" and .server_env.OH_APP_BACKEND_PUBLIC_URL_IS_NONE == "1"'
  control-agent-server api GET /server_info --auth none --check app_backend_ingress_url missing \
    --check capabilities not-contains canvas_app_backend_bridge_v1 --save F31.config-null-override/server-info  # bug
  ```
  The file value is served, and a non-null variable replaces it (the
  positive controls); the config shows the file value and the `_IS_NONE`
  variable both reached the server before the `# bug` read. Expected:
  with `OH_APP_BACKEND_PUBLIC_URL_IS_NONE=1` the field is null and the
  bridge capability is gone. Today the file value is still served:
  `load_config` merges the environment over the file with
  `env_parser.merge`, which keeps the earlier value whenever the later one
  is `None`, so an explicit null from the environment cannot clear a file
  value. The same applies to `conversation_idle_ttl_seconds`: with a TTL in
  the file, `OH_CONVERSATION_IDLE_TTL_SECONDS_IS_NONE=1` does not disable
  eviction. The next bullet resets the config.
- **Invalid limits stop startup (`F31.config-invalid-fails-fast`).** A
  non-numeric variable, then a limit below its minimum in the file; then the
  run is restored to its defaults.
  ```sh
  N1=$(control-agent-server logs --grep "invalid literal for int\(\) with base 10: 'qa-f31-nan'" | jq .total_matching)
  if control-agent-server restart --reset-config --env OH_MAX_CONCURRENT_RUNS=qa-f31-nan --timeout 60; then false; fi
  control-agent-server runs | jq -e --arg r "$AGENT_SERVER_VERIFY_RUN" '.runs[] | select(.run == $r) | .alive == false'
  control-agent-server logs --grep "invalid literal for int\(\) with base 10: 'qa-f31-nan'" --expect-min $((N1 + 1))
  N2=$(control-agent-server logs --grep 'greater_than_equal, input_value=0' | jq .total_matching)
  N3=$(control-agent-server logs --grep '^max_concurrent_runs$' | jq .total_matching)
  if control-agent-server restart --reset-config --config-json '{"max_concurrent_runs": 0}' --timeout 60; then false; fi
  control-agent-server runs | jq -e --arg r "$AGENT_SERVER_VERIFY_RUN" '.runs[] | select(.run == $r) | .alive == false'
  control-agent-server logs --grep 'greater_than_equal, input_value=0' --expect-min $((N2 + 1))
  control-agent-server logs --grep '^max_concurrent_runs$' --expect-min $((N3 + 1))
  control-agent-server restart --reset-config
  control-agent-server doctor
  ```
  Both restarts fail with `Server exited with code 1 during startup` and the
  run is not alive; the log shows `ValueError: invalid literal for int() with
  base 10: 'qa-f31-nan'` and the pydantic error `Input should be greater than
  or equal to 1 [type=greater_than_equal, input_value=0, ...]` for
  `max_concurrent_runs`. A restart without overrides is healthy again.
- **Unrecognised booleans and choices start anyway (`F31.config-silent-env`).**
  A second server whose configuration file sets `deferred_init` true (so it
  starts dormant); then the boolean `OH_DEFERRED_INIT=yes`, then the
  `Literal` `OH_CONVERSATION_RUNTIME` spelled `Docker`, each after a positive
  control on the same server.
  ```sh
  B=$(control-agent-server launch --new --name f31-env --config-json '{"deferred_init": true}' --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  control-agent-server api GET /api/conversations/count --run "$B" --expect 503 --check exception contains "deferred-init state 'dormant'"
  control-agent-server restart --run "$B" --env OH_DEFERRED_INIT=yes
  control-agent-server config --run "$B" | jq -e '.config_file.deferred_init == true and .server_env.OH_DEFERRED_INIT == "yes"'
  control-agent-server api GET /api/conversations/count --run "$B" --expect 200 --save F31.config-silent-env/bool-yes
  if command -v docker >/dev/null; then
    control-agent-server restart --run "$B" --reset-config --env OH_CONVERSATION_RUNTIME=docker
    control-agent-server api GET /server_info --run "$B" --auth none --check conversation_runtime eq docker --quiet
  fi
  control-agent-server restart --run "$B" --reset-config --env OH_CONVERSATION_RUNTIME=Docker
  control-agent-server config --run "$B" | jq -e '.server_env.OH_CONVERSATION_RUNTIME == "Docker"'
  control-agent-server api GET /server_info --run "$B" --auth none --check conversation_runtime eq local --quiet \
    --save F31.config-silent-env/literal-fallback
  NL=$(control-agent-server logs --run "$B" --grep "input_value='Docker'" | jq .total_matching)
  if control-agent-server restart --run "$B" --reset-config --config-json '{"conversation_runtime": "Docker"}' --timeout 60; then false; fi
  control-agent-server logs --run "$B" --grep "input_value='Docker'" --expect-min $((NL + 1))
  control-agent-server stop --run "$B"
  trap - EXIT
  ```
  The file's `true` makes the server dormant (503 `deferred-init state
  'dormant'`). With `OH_DEFERRED_INIT=yes` added it starts and serves
  `/api/*`: the boolean parser reads only `1` and `true` (any case) as true,
  so `yes` is read as false and overrides the file's `true` instead of being
  refused. `OH_CONVERSATION_RUNTIME=docker` is honored (`/server_info` says
  `docker`; startup only warns that it cannot reach a daemon, and the
  control runs only where the `docker` CLI is installed, which this mode
  calls at startup), but `Docker` is outside the `Literal` choices and
  silently falls back to `local`. The same `Docker` in the configuration
  file stops startup with pydantic's `literal_error`, as a non-numeric
  integer variable does (`F31.config-invalid-fails-fast`): only the
  environment parser is lenient for booleans and choices.
- **Per-process tmux directory removed on stop (`F31.tmux-dir-cleanup`), known bug.**
  A second run whose `TMUX_TMPDIR` is empty, so the server picks its own.
  ```sh
  B=$(control-agent-server launch --new --name f31-tmux --env TMUX_TMPDIR= --print-run)
  trap 'control-agent-server stop --run "$B" >/dev/null 2>&1 || true' EXIT
  PIDB=$(jq -r .pid "$B/run.json")
  TD="$(python3 -c 'import tempfile; print(tempfile.gettempdir())')/openhands-agent-server-$PIDB"
  control-agent-server logs --run "$B" --grep 'TMUX_TMPDIR not set' --expect-min 1
  test -d "$TD"
  control-agent-server stop --run "$B" | jq -e '.server == "terminated"'
  trap 'rm -rf -- "$TD" || true' EXIT
  test ! -e "$TD"  # bug
  trap - EXIT
  ```
  Expected: the directory is gone after a graceful stop (`stop` reports
  `terminated`, not `killed`, so the lifespan shutdown ran). The log line
  and the directory existing while `B` runs are the positive controls.
  Today the server logs `TMUX_TMPDIR not set; defaulting to per-server tmux
  directory /tmp/openhands-agent-server-<pid>`, creates it, and at shutdown
  only removes the variable from its environment (`api_lifespan`), so every
  such start leaves a directory in the temp dir and `test ! -e` fails. The
  `trap` removes the leftover after the assertion.
- **Storage retention and disk budget (`F31.storage-retention`), blocked.**
  Needs a Docker daemon and the `conversation_image` pulled
  (`control-agent-server capabilities` reports `docker`): the storage
  reclaimer exists only in the Docker runtime (`DockerConversationRegistry`
  builds it; the local runtime has none), and its pass runs every 300 s
  (`MAINTENANCE_INTERVAL` in `storage/reclaimer.py`), first 300 s after
  startup. It would prove that with `conversation_storage_retention_days`
  set, a stopped conversation inactive past the limit is archived: its
  events stay readable, while `/run`, a message and every route proxied into
  its container answer 410 `Conversation runtime was archived; its history
  is read-only`, `GET .../runtime` reports `missing` with `can_resume`
  false, an `<id>.archived.json` marker appears under
  `home/.openhands/runtime-control/` and its `runtime-data/<id>/` directory
  (sandbox home and the workspace the server created) is gone. A second
  server with only `conversation_storage_disk_budget` below the
  filesystem's usage (for example 0.01) would show a stopped runtime's
  workspace losing its gitignored directories (`node_modules`, `.venv`)
  while tracked and untracked files stay, and `Conversation storage: freed`
  in the log.
  ```sh
  DK=$(control-agent-server launch --new --name f31-storage --print-run \
    --config-json '{"conversation_runtime": "docker", "conversation_storage_retention_days": 0.0001}')
  CID=$(control-agent-server api POST /api/conversations --run "$DK" \
    --json '{"agent": {"llm": {"model": "openai/qa-stub", "api_key": "qa"}, "tools": []}}' --expect 200,201 --field id)
  RT="/api/conversations/$CID/runtime"
  NE=$(control-agent-server api GET "/api/conversations/$CID/events/count" --run "$DK" --field .)
  control-agent-server api DELETE "$RT" --run "$DK" --expect 204
  control-agent-server api GET "$RT" --run "$DK" --until-ok 330 --check runtime_status eq missing --check can_resume eq false
  control-agent-server state ls --run "$DK" "home/.openhands/runtime-control/${CID//-/}.archived.json" --expect-count 1
  control-agent-server state ls --run "$DK" "home/.openhands/runtime-data/${CID//-/}/**/*" --expect-count 0
  control-agent-server api GET "/api/conversations/$CID/events/count" --run "$DK" --expect 200 --check . eq "$NE"
  control-agent-server api POST "/api/conversations/$CID/run" --run "$DK" --expect 410 \
    --check detail eq 'Conversation runtime was archived; its history is read-only' --save F31.storage-retention/run-410
  control-agent-server api POST "$RT/reprovision" --run "$DK" --expect 410
  control-agent-server logs --run "$DK" --grep 'Conversation storage: freed' --expect-min 1
  control-agent-server stop --run "$DK"
  ```

## Gotchas

- A lease file exists only while a conversation is loaded. GET, the batch
  get, search and count read the catalog and never load (see
  `F04.read-no-hydrate`); PATCH, DELETE, event reads and writes, `/run`,
  pause, interrupt, `agent_final_response` and the events socket do. Eviction
  and takeover are invisible in the API: read `owner_lease.json` and the log.
- The server log is wrapped by Rich: a long message is split over several
  lines. Grep short phrases (`Taking over conversation`, `Evicted idle`,
  `TMUX_TMPDIR not set`), and count matches before and after because the log
  keeps every earlier start of the run.
- A second server reads the catalog once, at its own startup. Conversations
  created on the owner afterwards are 404 there until it restarts, and its
  copy of the metadata goes stale; the first time it loads such a
  conversation (takeover or not) it writes that stale copy to `meta.json`
  (`F31.takeover-keeps-metadata`).
- A conversation whose lease is held elsewhere looks exactly like an unknown
  id on the second server (404, 400, `success: false`, close 4004), while its
  GET is 200 from the catalog. Only the lease file tells the two apart.
- Takeover before expiry needs the recorded owner on the same host with a
  dead pid. A frozen or hung owner (pid alive), an owner on another host, or
  a lease file from an older server without `owner_host`/`owner_pid` is taken
  over only after `expires_at`. A TTL below the 15 s renewal interval expires
  between renewals.
- An owner that lost its lease keeps the conversation loaded and answers
  every write with 500 until idle eviction or a restart closes it, and its
  reads serve the stale in-memory state; each attempt counts as activity,
  so retries postpone the eviction. Its renewal loop only logs `Conversation
  lease lost while renewing` every 15 s.
- Graceful shutdown pauses running conversations (`F05.restart-running-graceful`);
  a SIGKILL leaves `running` on disk, which the next start turns into
  `error` (`F05.restart-hard-running`, `F31.crash-tool-call`). A running
  conversation is the only kind loaded eagerly at startup.
- Idle eviction cannot be faster than one pass a minute
  (`max(60, ttl/2)`), and the first pass runs about 60 s after startup.
  `OH_CONVERSATION_IDLE_TTL_SECONDS=null` stops startup (`float('null')`);
  disable eviction with `null` in the file, or with
  `OH_CONVERSATION_IDLE_TTL_SECONDS_IS_NONE=1` only when the file does not
  set the TTL (an environment null never overrides a file value,
  `F31.config-null-override`).
- Creating a conversation needs a run slot, like `/run` (`F05.run-capacity`):
  with `max_concurrent_runs` slots busy, create every conversation a recipe
  needs before the first run starts.
- `OH_*` integers and floats that do not parse, and values outside a field's
  bounds, stop startup with exit code 1; booleans (only `1` and `true` are
  true, so `yes` or `on` read as false and override a file's `true`) and
  `Literal` fields (outside their choices: the default) start silently
  instead (`env_parser.py`, `F31.config-silent-env`). The configuration
  file is validated strictly. `launch` and `restart` report the failure as
  exit 3 `Server exited ... during startup`.
- The launcher always sets a short per-run `TMUX_TMPDIR` and removes it on
  `stop`; `--env TMUX_TMPDIR=` (empty) makes the server choose its own.
  Startup kills tmux sessions on the `openhands` sockets of whatever
  `TMUX_TMPDIR` it gets, so never share one between servers.
- A second run launched with `--share-conversations-from` has its own
  `OH_SECRET_KEY`, settings, secrets and profiles: it does not share the
  owner's cipher, so the bullets above take over only conversations without
  secrets (a takeover of one with encrypted secrets is not mapped).
