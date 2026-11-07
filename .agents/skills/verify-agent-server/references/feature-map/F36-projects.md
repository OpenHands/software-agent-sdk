# Projects and coordinator tools

A project groups the conversations that work toward one goal: one coordinator
conversation that the person mainly talks to, and the threads it starts or is
given. Project records (`id`, `name`, `description`,
`coordinator_conversation_id`, timestamps) live in one JSON document,
`projects.json` in the persistence directory, rewritten atomically under a
file lock. Membership is not stored there: a conversation belongs to a project
when its `project` tag holds the project's id (hex), so a child started with
`start_child_conversation` joins its parent's project with the rest of the
copied tags. `GET /api/projects/{id}/threads` lists the members grouped by
what they need from the person (`attention`: `needs_you`, `working`,
`finished`, `idle`, derived from `execution_status`). Two server-executed
tools, `list_project_threads` and `message_project_thread`, give a
coordinator agent typed access to the same list and a way to steer a thread.
Messages go through `POST .../threads/{conversation_id}/messages`, which never
runs a thread that is waiting for confirmation, because that run would approve
the pending action: the check is made under the thread's run lock. Deleting a project never
changes or deletes conversations. A file the server cannot parse, or one
written by a newer schema, is never overwritten: every route answers 409.

Source: `openhands-agent-server/openhands/agent_server/projects_router.py`, `openhands-agent-server/openhands/agent_server/persistence/models.py`, `openhands-agent-server/openhands/agent_server/persistence/store.py`, `openhands-tools/openhands/tools/project/`

Needs: `llm`

Routes: `GET /api/projects`, `POST /api/projects`,
`GET /api/projects/{project_id}`, `PATCH /api/projects/{project_id}`,
`DELETE /api/projects/{project_id}`, `GET /api/projects/{project_id}/threads`,
`PUT /api/projects/{project_id}/threads/{conversation_id}`,
`DELETE /api/projects/{project_id}/threads/{conversation_id}`,
`POST /api/projects/{project_id}/threads/{conversation_id}/messages`

## Sub-features

- `F36.list-empty`: on a fresh persistence directory `GET /api/projects` returns `{"projects": []}` and creates no `projects.json`.
- `F36.create`: `POST /api/projects` answers 201 with a hex `id`, the name and description, no coordinator; `GET` by id, the list and the `0600` file (`schema_version` 1) hold the same record.
- `F36.validation`: an empty name or a name over 256 characters is 422 and writes nothing.
- `F36.unknown-project`: every route answers 404 for an unknown project id, and a thread `PUT` to it leaves the conversation's tags unchanged.
- `F36.patch`: `PATCH` renames and changes the description, keeps `created_at` and moves `updated_at`; omitted fields stay.
- `F36.coordinator`: creating a project with `coordinator_conversation_id` tags that conversation `project=<id>` and keeps its other tags; the threads endpoint reports it as `coordinator`, not as a thread.
- `F36.coordinator-missing`: an unknown coordinator id is 404 and creates no project.
- `F36.coordinator-unique`: a conversation that coordinates one project cannot coordinate or join another (409), and its tag is unchanged.
- `F36.add-thread`: `PUT .../threads/{conversation_id}` answers 204, is idempotent, sets `project` and keeps the conversation's other tags.
- `F36.move-thread`: adding a member of one project to another moves it: the first project's threads no longer list it.
- `F36.attention`: threads are grouped `needs_you` (waiting for confirmation), then `working`, `finished`, `idle`, newest-updated first within a group; non-members are left out.
- `F36.remove-thread`: `DELETE .../threads/{conversation_id}` clears only the `project` tag; a second delete, or a delete through another project, is 404.
- `F36.remove-coordinator`: removing the coordinator as a thread is 409; clearing it with `PATCH {"coordinator_conversation_id": null}` keeps it a member, now listed as a thread.
- `F36.message-rest`: `POST .../messages` to a member saves the message, starts a run and answers `run: started` with the previous status; a non-member is 404.
- `F36.message-refused`: `POST .../messages` to a thread waiting for confirmation is 409, saves nothing, and the thread keeps waiting.
- `F36.persist-restart`: projects and membership survive a server restart.
- `F36.delete`: `DELETE /api/projects/{id}` removes the record, then the threads endpoint is 404, and member conversations keep their tags.
- `F36.corrupted-file`: an unparsable or future-schema `projects.json` makes reads and writes 409 and is never rewritten.
- `F36.tool-start-and-list`: a coordinator with `start_child_conversation` and `list_project_threads` starts a child that joins the project, and its list call reports that child with its attention group.
- `F36.tool-message`: `message_project_thread` delivers a follow-up to a finished thread and requests a run: the thread gets a second user message and runs again.
- `F36.tool-message-refuses-confirmation`: `message_project_thread` refuses a thread waiting for confirmation with an error observation; the thread stays waiting and gets no new message.

## How to get to it (agent POV)

- REST: `GET /api/projects` returns `{"projects": [ProjectItem]}`, oldest first.
  `POST /api/projects` with `{"name", "description"?, "coordinator_conversation_id"?}`
  answers 201 and the `ProjectItem`. `GET`, `PATCH` (`name`, `description`,
  `coordinator_conversation_id`; an explicit null clears the coordinator) and
  `DELETE` act on `/api/projects/{project_id}`.
- REST: `GET /api/projects/{project_id}/threads` returns
  `{"project_id", "coordinator": ProjectThread | null, "threads": [ProjectThread]}`
  where a thread is `{conversation_id, title, execution_status, attention,
  parent_conversation_id, created_at, updated_at}`.
- REST: `PUT /api/projects/{project_id}/threads/{conversation_id}` (204) and
  `DELETE` on the same path (`{"deleted": true}`) change membership by setting
  or clearing the conversation's `project` tag; other tags are kept.
  `PATCH /api/conversations/{id}` with `tags` reaches the same membership but
  replaces the whole tag set (F04).
- REST: `POST /api/projects/{project_id}/threads/{conversation_id}/messages`
  with `{"text"}` answers `{conversation_id, previous_status, run}`, where `run` is
  `started`, `queued`, `waiting_for_confirmation` or `not_run`;
  409 when the thread is waiting for confirmation. It is the confirmation-safe
  counterpart of `POST /api/conversations/{id}/events` with `run: true`.
- Agent tools (registered by the agent-server, not part of the default
  preset): `list_project_threads` and `message_project_thread` from
  `openhands.tools.project`, alongside `start_child_conversation`. They read
  the project from the calling conversation's `project` tag and call this
  server through `OH_INTERNAL_SERVER_URL` with its session key. Name them in
  `agent_settings.tools` (`conversation start --tools ...`).
- SDK and TypeScript client: no typed methods yet; use the REST routes.
- Configuration: `OH_PERSISTENCE_DIR` chooses the directory of
  `projects.json`, as for `workspaces.json` (F17).
- Insider Cat (context only): the coordinator role maps to a project's
  coordinator conversation; its `insidercat`/`insiderrole` tags are kept when
  a conversation joins a project.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok, and
  nothing has touched `/api/projects` on it yet (`F36.list-empty` needs a fresh
  persistence directory). The block below activates the DeepSeek profile
  (`$DEEPSEEK_API_KEY`): the `F36.tool-*` bullets and `F36.attention` run a
  model, and the coordinator is started with the active profile. The other
  REST bullets use placeholder agents.
  `F36.attention` and `F36.tool-message-refuses-confirmation` need `tmux`
  for the terminal tool.
- Bullets run in order and build on each other's projects and
  conversations; `F36.persist-restart` restarts the run.

  ```sh
  control-agent-server llm preset deepseek
  PJ=home/.openhands/projects.json
  PJ_FILE="$AGENT_SERVER_VERIFY_RUN/$PJ"
  ```

- **No projects yet (`F36.list-empty`).**
  ```sh
  control-agent-server api GET /api/projects --expect 200 --check projects len-eq 0 --save F36.list-empty/list
  control-agent-server state ls "$PJ" --expect-count 0
  ```
  The list is empty and reading created no file.
- **Create a project (`F36.create`).**
  ```sh
  P1=$(control-agent-server api POST /api/projects --json '{"name": "qa-f36-one", "description": "first"}' \
    --expect 201 --check name eq qa-f36-one --check description eq first \
    --check coordinator_conversation_id eq null --check id matches '^[0-9a-f]{32}$' --field id)
  control-agent-server api GET "/api/projects/$P1" --expect 200 --check id eq "$P1" --check name eq qa-f36-one --save F36.create/get
  control-agent-server api GET /api/projects --check projects len-eq 1 --check projects.0.id eq "$P1"
  control-agent-server state cat "$PJ" --mode 600 --check schema_version eq 1 --check projects.0.id eq "$P1"
  ```
  The record has a hex id; the read, the list and the file agree.
- **Validation (`F36.validation`).**
  Requires: `F36.create`
  ```sh
  control-agent-server api POST /api/projects --json '{"name": ""}' --expect 422 --save F36.validation/empty
  control-agent-server api POST /api/projects --json "{\"name\": \"$(printf 'x%.0s' $(seq 257))\"}" --expect 422
  control-agent-server api GET /api/projects --check projects len-eq 1
  ```
  Both are 422 and the list still holds one project.
- **Unknown project (`F36.unknown-project`).**
  ```sh
  NOPE=00000000000000000000000000000000
  W1=$(control-agent-server conversation start --no-run --placeholder-agent --title qa-f36-w1 --tag insiderrole=worker --print-id)
  control-agent-server api GET "/api/projects/$NOPE" --expect 404 --check detail eq 'Project not found' --save F36.unknown-project/get
  control-agent-server api PATCH "/api/projects/$NOPE" --json '{"name": "x"}' --expect 404
  control-agent-server api DELETE "/api/projects/$NOPE" --expect 404
  control-agent-server api GET "/api/projects/$NOPE/threads" --expect 404
  control-agent-server api PUT "/api/projects/$NOPE/threads/$W1" --expect 404
  control-agent-server api DELETE "/api/projects/$NOPE/threads/$W1" --expect 404
  control-agent-server api GET "/api/conversations/$W1" --check tags.insiderrole eq worker --check tags not-contains project
  ```
  Every route is 404 and the conversation keeps only its own tag.
- **Rename (`F36.patch`).**
  Requires: `F36.create`
  ```sh
  BEFORE=$(control-agent-server api GET "/api/projects/$P1" --field updated_at)
  CREATED=$(control-agent-server api GET "/api/projects/$P1" --field created_at)
  control-agent-server api PATCH "/api/projects/$P1" --json '{"name": "qa-f36-renamed"}' --expect 200 \
    --check name eq qa-f36-renamed --check description eq first --check created_at eq "$CREATED" \
    --check updated_at ne "$BEFORE" --save F36.patch/patch
  control-agent-server state cat "$PJ" --check projects.0.name eq qa-f36-renamed
  ```
  The name changes, the description and `created_at` stay, `updated_at` moves.
- **Coordinator (`F36.coordinator`).**
  ```sh
  CAT=$(control-agent-server conversation start --no-run --title qa-f36-cat --tag insidercat=self \
    --tools start_child_conversation,list_project_threads,message_project_thread --print-id)
  P2=$(control-agent-server api POST /api/projects --json "{\"name\": \"qa-f36-two\", \"coordinator_conversation_id\": \"$CAT\"}" \
    --expect 201 --field id)
  control-agent-server api GET "/api/projects/$P2" --check coordinator_conversation_id eq "$(echo "$CAT" | tr -d -)" --save F36.coordinator/get
  control-agent-server api GET "/api/conversations/$CAT" --check tags.project eq "$P2" --check tags.insidercat eq self
  control-agent-server api GET "/api/projects/$P2/threads" --check coordinator.title eq qa-f36-cat --check threads len-eq 0 \
    --save F36.coordinator/threads
  ```
  The coordinator carries `project=<P2>` next to `insidercat`, and the threads
  endpoint reports it as the coordinator with no threads yet.
- **Missing coordinator (`F36.coordinator-missing`).**
  Requires: `F36.coordinator`
  ```sh
  control-agent-server api POST /api/projects \
    --json '{"name": "qa-f36-ghost", "coordinator_conversation_id": "00000000-0000-0000-0000-000000000001"}' \
    --expect 404 --check detail eq 'Conversation not found' --save F36.coordinator-missing/post
  control-agent-server api GET /api/projects --check projects len-eq 2 --check . not-contains qa-f36-ghost
  ```
  Nothing is created.
- **One project per coordinator (`F36.coordinator-unique`).**
  Requires: `F36.create`, `F36.coordinator`
  ```sh
  control-agent-server api POST /api/projects --json "{\"name\": \"qa-f36-three\", \"coordinator_conversation_id\": \"$CAT\"}" \
    --expect 409 --check detail eq 'Conversation is the coordinator of another project' --save F36.coordinator-unique/post
  control-agent-server api PUT "/api/projects/$P1/threads/$CAT" --expect 409
  control-agent-server api GET "/api/conversations/$CAT" --check tags.project eq "$P2"
  ```
  Both are 409 and the coordinator stays in its project.
- **Add a thread (`F36.add-thread`).**
  Requires: `F36.unknown-project`, `F36.create`
  ```sh
  control-agent-server api PUT "/api/projects/$P1/threads/$W1" --expect 204 --save F36.add-thread/put
  control-agent-server api PUT "/api/projects/$P1/threads/$W1" --expect 204
  control-agent-server api GET "/api/conversations/$W1" --check tags.project eq "$P1" --check tags.insiderrole eq worker
  control-agent-server api GET "/api/projects/$P1/threads" --check threads len-eq 1 --check threads.0.title eq qa-f36-w1 \
    --check threads.0.attention eq idle --check coordinator eq null --save F36.add-thread/threads
  ```
  Adding twice is fine, the other tag stays, and the thread is listed as idle.
- **Move a thread (`F36.move-thread`).**
  Requires: `F36.add-thread`, `F36.coordinator`
  ```sh
  control-agent-server api PUT "/api/projects/$P2/threads/$W1" --expect 204 --save F36.move-thread/put
  control-agent-server api GET "/api/projects/$P1/threads" --check threads len-eq 0
  control-agent-server api GET "/api/projects/$P2/threads" --check threads len-eq 1 --check threads.0.title eq qa-f36-w1
  ```
  A conversation belongs to one project at a time.
- **Attention groups (`F36.attention`).**
  Requires: `F36.move-thread`
  Needs: `llm`, `tmux`
  ```sh
  ASKS=$(control-agent-server conversation start --title qa-f36-asks --tools terminal --confirmation-policy always \
    --prompt 'Run the shell command: echo qa-f36 > qa-f36.txt' --wait --until waiting_for_confirmation --timeout 180 --print-id)
  OUT=$(control-agent-server conversation start --no-run --placeholder-agent --title qa-f36-outsider --print-id)
  control-agent-server api PUT "/api/projects/$P2/threads/$ASKS" --expect 204
  control-agent-server api GET "/api/projects/$P2/threads" --check threads len-eq 2 \
    --check threads.0.title eq qa-f36-asks --check threads.0.attention eq needs_you \
    --check threads.0.execution_status eq waiting_for_confirmation \
    --check threads.1.title eq qa-f36-w1 --check threads.1.attention eq idle \
    --check . not-contains qa-f36-outsider --save F36.attention/threads
  ```
  The thread waiting for approval comes first as `needs_you`; the idle one
  follows; the untagged conversation is not listed.
- **Remove a thread (`F36.remove-thread`).**
  Requires: `F36.move-thread`
  ```sh
  control-agent-server api DELETE "/api/projects/$P1/threads/$W1" --expect 404 \
    --check detail eq 'Conversation not in project' --save F36.remove-thread/other-project
  control-agent-server api DELETE "/api/projects/$P2/threads/$W1" --expect 200 --check deleted eq true --save F36.remove-thread/delete
  control-agent-server api DELETE "/api/projects/$P2/threads/$W1" --expect 404
  control-agent-server api GET "/api/conversations/$W1" --check tags not-contains project --check tags.insiderrole eq worker
  ```
  Only the owning project can remove it, only once, and only the `project`
  tag goes.
- **Coordinator membership (`F36.remove-coordinator`).**
  Requires: `F36.coordinator`
  ```sh
  control-agent-server api DELETE "/api/projects/$P2/threads/$CAT" --expect 409 \
    --check detail eq "Conversation is the project's coordinator" --save F36.remove-coordinator/delete
  control-agent-server api PATCH "/api/projects/$P2" --json '{"coordinator_conversation_id": null}' --expect 200 \
    --check coordinator_conversation_id eq null
  control-agent-server api GET "/api/projects/$P2/threads" --check coordinator eq null --check . contains qa-f36-cat \
    --save F36.remove-coordinator/threads
  control-agent-server api PATCH "/api/projects/$P2" --json "{\"coordinator_conversation_id\": \"$CAT\"}" --expect 200
  control-agent-server api GET "/api/projects/$P2/threads" --check coordinator.title eq qa-f36-cat
  ```
  The coordinator cannot be removed as a thread; once cleared it is listed as
  a thread, and setting it again restores it.
- **Message a thread over REST (`F36.message-rest`).**
  Requires: `F36.coordinator`, `F36.unknown-project`
  ```sh
  W2=$(control-agent-server conversation start --no-run --title qa-f36-w2 --print-id)
  control-agent-server api PUT "/api/projects/$P2/threads/$W2" --expect 204
  control-agent-server api POST "/api/projects/$P2/threads/$W2/messages" --json '{"text": "Reply with the single word ok and finish."}' \
    --expect 200 --check run eq started --check previous_status eq idle --save F36.message-rest/post
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  control-agent-server api POST "/api/projects/$P2/threads/$W1/messages" --json '{"text": "hi"}' --expect 404 \
    --check detail eq 'Conversation not in project'
  ```
  The member runs and finishes; `W1` (removed earlier) is refused.
- **No approval over REST (`F36.message-refused`).**
  Requires: `F36.attention`
  ```sh
  control-agent-server api POST "/api/projects/$P2/threads/$ASKS/messages" --json '{"text": "go ahead"}' --expect 409 \
    --check detail eq 'Conversation is waiting for confirmation' --save F36.message-refused/post
  control-agent-server api GET "/api/conversations/$ASKS" --check execution_status eq waiting_for_confirmation
  control-agent-server conversation events "$ASKS" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  ```
  Nothing is saved and the thread keeps waiting for the person.
- **Restart (`F36.persist-restart`).**
  Requires: `F36.create`, `F36.coordinator`
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/projects --check projects len-eq 2 --check projects.1.id eq "$P2" --save F36.persist-restart/list
  control-agent-server api GET "/api/projects/$P2/threads" --check coordinator.title eq qa-f36-cat --save F36.persist-restart/threads
  ```
  Records and membership are read back from disk.
- **Delete a project (`F36.delete`).**
  Requires: `F36.add-thread`, `F36.create`
  ```sh
  control-agent-server api PUT "/api/projects/$P1/threads/$W1" --expect 204
  control-agent-server api DELETE "/api/projects/$P1" --expect 200 --check deleted eq true --save F36.delete/delete
  control-agent-server api GET "/api/projects/$P1" --expect 404
  control-agent-server api GET "/api/projects/$P1/threads" --expect 404
  control-agent-server api GET "/api/conversations/$W1" --check tags.project eq "$P1"
  control-agent-server state cat "$PJ" --not-contains "$P1" --check projects len-eq 1
  ```
  The record is gone; the conversation is untouched, tag included.
- **Model-backed: start and list (`F36.tool-start-and-list`).**
  Requires: `F36.coordinator`
  Needs: `llm`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Start one child conversation titled qa-f36-child with isolation shared whose task is: reply with the single word done and finish. Then call list_project_threads once and tell me what you see. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ActionEvent --contains start_child_conversation --expect-min 1
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains qa-f36-child --expect-min 2 --save F36.tool-start-and-list/events
  CHILD=$(control-agent-server api GET "/api/conversations/$CAT" --field sub_conversation_ids.0)
  control-agent-server api GET "/api/projects/$P2/threads" --check . contains qa-f36-child
  control-agent-server conversation wait "$CHILD" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$CHILD" --check tags.project eq "$P2" \
    --check parent_conversation_id eq "$(control-agent-server api GET "/api/conversations/$CAT" --field id)"
  ```
  The child copies the coordinator's tags, so it is a project thread; the
  list tool's observation names it.
- **Model-backed: message a thread (`F36.tool-message`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use message_project_thread to ask the qa-f36-child thread to reply with the single word again. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'Message saved to thread' --expect-min 1 --save F36.tool-message/events
  control-agent-server conversation wait "$CHILD" --until finished --timeout 180
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"source": "user"' --expect-count 2
  control-agent-server conversation events "$CHILD" --kinds ActionEvent --contains FinishAction --expect-min 2
  ```
  The child receives a second user message and finishes a second run.
- **Model-backed: no approval by message (`F36.tool-message-refuses-confirmation`).**
  Requires: `F36.attention`, `F36.tool-start-and-list`
  Needs: `llm`, `tmux`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use message_project_thread to tell the qa-f36-asks thread: go ahead. Then stop.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'would approve that action' --expect-min 1 \
    --save F36.tool-message-refuses-confirmation/events
  control-agent-server api GET "/api/conversations/$ASKS" --check execution_status eq waiting_for_confirmation
  control-agent-server conversation events "$ASKS" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  ```
  The tool reports an error, the thread keeps waiting for the person, and it
  has only its original message.
- **Corrupted file (`F36.corrupted-file`).** Run last: it replaces the file.
  ```sh
  printf '{not json' > "$PJ_FILE"
  control-agent-server api GET /api/projects --expect 409 --check detail eq 'Projects file is corrupted' --save F36.corrupted-file/get
  control-agent-server api POST /api/projects --json '{"name": "qa-f36-new"}' --expect 409
  test "$(cat "$PJ_FILE")" = '{not json'
  printf '{"schema_version": 99, "projects": []}' > "$PJ_FILE"
  control-agent-server api GET /api/projects --expect 409
  control-agent-server api POST /api/projects --json '{"name": "qa-f36-new"}' --expect 409
  grep -q '"schema_version": 99' "$PJ_FILE"
  rm "$PJ_FILE"
  control-agent-server api GET /api/projects --expect 200 --check projects len-eq 0
  ```
  Neither file is rewritten; removing it brings back an empty list.

## Gotchas

- Membership is the `project` tag, so `PATCH /api/conversations/{id}` with a
  `tags` body that omits `project` removes a conversation from its project.
  Clients that edit tags should re-send the full set, as the Insider App does;
  the project routes merge for you.
- A child copies its parent's tags when it is started. Tagging a parent later
  does not tag children it already has: add them with `PUT` too.
- `attention` comes only from `execution_status`. `finished` means the run
  ended, not that the task succeeded. There is no per-thread "ask" text or
  task progress yet.
- The messages route refuses with 409 only when the thread is already
  waiting. If it reaches a confirmation while the message is being
  delivered, the message is kept and `run` is `waiting_for_confirmation`;
  a run that was already active answers `queued`. A `paused`
  thread is resumed. Delivery to a running thread waits for its state lock,
  which a long tool call holds, so the tool waits up to 300 seconds and, on a
  timeout, tells the agent not to resend.
- The tools read the project from the caller's tags on every call, so adding
  or removing the coordinator from a project takes effect on its next call.
  They are registered only by the agent-server (`tool_router.py`), like
  `start_child_conversation`.
- The projects store is a process-wide singleton like the workspaces store
  (F17): changing `OH_PERSISTENCE_DIR` needs a restart.
