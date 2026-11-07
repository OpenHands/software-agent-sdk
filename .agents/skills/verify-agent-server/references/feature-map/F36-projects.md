# Projects and coordinator tools

A project groups the conversations that work toward one goal: one coordinator
conversation that the person mainly talks to, and the conversations it starts or is
given. Project records (`id`, `name`, `description`,
`coordinator_conversation_id`, timestamps) live in one JSON document,
`projects.json` in the persistence directory, rewritten atomically under a
file lock. Membership is not stored there: a conversation belongs to a project
when its `project` tag holds the project's id (hex), so a child started with
`start_child_conversation` joins its parent's project with the rest of the
copied tags. `GET /api/projects/{id}/conversations` lists the members grouped by
what they need from the person (`attention`: `needs_you`, `working`,
`finished`, `idle`, derived from `execution_status`). Two server-executed
tools, `list_project_conversations` and `message_project_conversation`, give a
coordinator agent typed access to the same list and a way to steer a conversation.
Messages go through the generic `POST /api/conversations/{conversation_id}/events`.
With `refuse_pending_confirmation` it never runs a conversation that is waiting for
confirmation, because that run would approve the pending action: the check is made
under the conversation's run lock. With `sender_conversation_id` the message records
the sending conversation, which must be in the same project when it is in one. Deleting a project never
changes or deletes conversations. A file the server cannot parse, or one
written by a newer schema, is never overwritten: every route answers 409.

Source: `openhands-agent-server/openhands/agent_server/projects_router.py`, `openhands-agent-server/openhands/agent_server/project_board.py`, `openhands-agent-server/openhands/agent_server/project_artifacts.py`, `openhands-agent-server/openhands/agent_server/persistence/models.py`, `openhands-agent-server/openhands/agent_server/persistence/store.py`, `openhands-tools/openhands/tools/project/`

Needs: `llm`

Routes: `GET /api/projects/{project_id}/artifacts`,
`POST /api/projects/{project_id}/artifacts`,
`POST /api/projects/{project_id}/artifacts/from-conversation`,
`GET /api/projects/{project_id}/artifacts/{name}`,
`DELETE /api/projects/{project_id}/artifacts/{name}`,
`GET /api/projects`, `POST /api/projects`,
`GET /api/projects/{project_id}`, `PATCH /api/projects/{project_id}`,
`DELETE /api/projects/{project_id}`, `GET /api/projects/{project_id}/conversations`,
`PUT /api/projects/{project_id}/conversations/{conversation_id}`,
`DELETE /api/projects/{project_id}/conversations/{conversation_id}`,
`GET /api/projects/{project_id}/conversations/{conversation_id}/messages`,
`POST /api/conversations/{conversation_id}/events`

## Sub-features

- `F36.list-empty`: on a fresh persistence directory `GET /api/projects` returns `{"projects": []}` and creates no `projects.json`.
- `F36.create`: `POST /api/projects` answers 201 with a hex `id`, the name and description, no coordinator; `GET` by id, the list and the `0600` file (`schema_version` 1) hold the same record.
- `F36.validation`: an empty name or a name over 256 characters is 422 and writes nothing.
- `F36.unknown-project`: every route answers 404 for an unknown project id, and a conversation `PUT` to it leaves the conversation's tags unchanged.
- `F36.patch`: `PATCH` renames and changes the description, keeps `created_at` and moves `updated_at`; omitted fields stay.
- `F36.coordinator`: creating a project with `coordinator_conversation_id` tags that conversation `project=<id>` and keeps its other tags; the conversations endpoint reports it as `coordinator`, apart from the other conversations.
- `F36.coordinator-missing`: an unknown coordinator id is 404 and creates no project.
- `F36.coordinator-unique`: a conversation that coordinates one project cannot coordinate or join another (409), and its tag is unchanged.
- `F36.add-conversation`: `PUT .../conversations/{conversation_id}` answers 204, is idempotent, sets `project` and keeps the conversation's other tags.
- `F36.move-conversation`: adding a member of one project to another moves it: the first project's conversations no longer list it.
- `F36.attention`: conversations are grouped `needs_you` (waiting for confirmation), then `working`, `finished`, `idle`, newest-updated first within a group; non-members are left out.
- `F36.remove-conversation`: `DELETE .../conversations/{conversation_id}` clears only the `project` tag; a second delete, or a delete through another project, is 404.
- `F36.remove-coordinator`: removing the coordinator from the project is 409; clearing it with `PATCH {"coordinator_conversation_id": null}` keeps it a member, now listed with the other conversations.
- `F36.message-rest`: `POST /api/conversations/{id}/events` with `refuse_pending_confirmation` saves the message, starts a run and answers `run: started` with the previous status. Without `sender_conversation_id` the message has no `sender`; a member sending to a conversation outside its project is 422.
- `F36.message-refused`: `refuse_pending_confirmation` on a conversation waiting for confirmation is 409, saves nothing, and the conversation keeps waiting.
- `F36.turn`: when a member's run finishes, its `turn` tag says whether the agent's last message asked something (`asked`, attention `needs_input`) or not (`answered`); a new user message clears it.
- `F36.resolved`: a `resolved` tag (`user` or `auto`) puts a member in the `resolved` group, reported as `resolved`; a new user message clears it. Children do not copy `resolved` or `turn`.
- `F36.artifacts-rest`: a project's artifacts can be uploaded (multipart), listed with `source: user`, downloaded byte for byte and deleted; unsafe names are 400.
- `F36.artifacts-tool`: a member with `save_project_artifact` saves a file from its workspace; the list shows it with `source: agent` and the conversation's id, and a path outside the workspace is refused.
- `F36.persist-restart`: projects and membership survive a server restart.
- `F36.delete`: `DELETE /api/projects/{id}` removes the record, then the conversations endpoint is 404, and member conversations keep their tags.
- `F36.corrupted-file`: an unparsable or future-schema `projects.json` makes reads and writes 409 and is never rewritten.
- `F36.tool-start-and-list`: a coordinator with `start_child_conversation` and `list_project_conversations` starts a child that joins the project, and its list call reports that child with its attention group. The child's system prompt names the coordinator, and its first message has `sender: "conversation:<coordinator id hex>"`.
- `F36.wake`: when a member finishes a run the coordinator started (its task or follow-up message), the coordinator gets one user message with the member's final message, `sender: "conversation:<member id hex>"` and an "Update from" header, and runs on it. Turns the person starts in a member (`F36.turn`, `F36.resolved`) do not wake it.
- `F36.read`: `GET .../conversations/{conversation_id}/messages` lists a member's latest messages (with senders) and `finish`, oldest first; the `read_project_conversation` tool reads it.
- `F36.tool-message`: `message_project_conversation` delivers a follow-up to a finished conversation and requests a run: the conversation gets a second user message, sent by the coordinator (`sender` and a header line saying it is not the person), and runs again.
- `F36.tool-message-refuses-confirmation`: `message_project_conversation` refuses a conversation waiting for confirmation with an error observation; the conversation stays waiting and gets no new message.

## How to get to it (agent POV)

- REST: `GET /api/projects` returns `{"projects": [ProjectItem]}`, oldest first.
  `POST /api/projects` with `{"name", "description"?, "coordinator_conversation_id"?}`
  answers 201 and the `ProjectItem`. `GET`, `PATCH` (`name`, `description`,
  `coordinator_conversation_id`; an explicit null clears the coordinator) and
  `DELETE` act on `/api/projects/{project_id}`.
- REST: `GET /api/projects/{project_id}/conversations` returns
  `{"project_id", "coordinator": ProjectConversation | null, "conversations": [ProjectConversation]}`
  where a conversation is `{conversation_id, title, execution_status, attention,
  resolved, turn, automation_id, parent_conversation_id, created_at,
  updated_at}`. `attention` is `needs_you`, `needs_input`, `working`,
  `finished`, `idle` or `resolved`.
- Tags the server sets on project members (`openhands.agent_server.project_board`):
  `turn=asked|answered` when a run finishes, and `resolved=auto` when the
  critic scored the finished task at least 0.9 (never over `resolved=user`).
  Clients resolve a conversation by setting `resolved=user` with the conversation
  `PATCH` (full tag set); a new user message removes both tags.
- REST: `GET /api/projects/{project_id}/artifacts` lists
  `{name, size, source, conversation_id, created_at, updated_at}` newest
  first; `POST` there uploads (multipart `file`, optional `name` form field);
  `POST .../artifacts/from-conversation` with `{conversation_id, path, name?}`
  copies a member's workspace file; `GET`/`DELETE .../artifacts/{name}`
  download and delete. Names are flat (letters, digits, spaces, `._()+-`,
  no leading dot, no trailing dot or space, no Windows device names), and a
  name differing from another only in case is 409. Files live in
  `<persistence dir>/project-artifacts/<project id>/`, up to 100 MiB each.
- REST: `PUT /api/projects/{project_id}/conversations/{conversation_id}` (204) and
  `DELETE` on the same path (`{"deleted": true}`) change membership by setting
  or clearing the conversation's `project` tag; other tags are kept.
  `PATCH /api/conversations/{id}` with `tags` reaches the same membership but
  replaces the whole tag set (F04).
- REST: `POST /api/conversations/{conversation_id}/events` (F04) takes two
  optional fields. `refuse_pending_confirmation` (with `run: true`) answers 409
  when the conversation is waiting for confirmation, and otherwise
  `{success, previous_status, run}`, where `run` is `started`, `queued`,
  `waiting_for_confirmation` or `not_run`. `sender_conversation_id` records
  `sender: "conversation:<id hex>"` and adds a "Sent by" first line naming the
  coordinator, another conversation in the project, or another conversation;
  422 when the sender is unknown, is the target, or is in a project the
  target is not in.
- Agent tools (registered by the agent-server, not part of the default
  preset): `list_project_conversations`, `message_project_conversation` and
  `save_project_artifact` from
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
  control-agent-server api GET "/api/projects/$NOPE/conversations" --expect 404
  control-agent-server api PUT "/api/projects/$NOPE/conversations/$W1" --expect 404
  control-agent-server api DELETE "/api/projects/$NOPE/conversations/$W1" --expect 404
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
    --tools start_child_conversation,list_project_conversations,message_project_conversation,read_project_conversation --print-id)
  P2=$(control-agent-server api POST /api/projects --json "{\"name\": \"qa-f36-two\", \"coordinator_conversation_id\": \"$CAT\"}" \
    --expect 201 --field id)
  control-agent-server api GET "/api/projects/$P2" --check coordinator_conversation_id eq "$(echo "$CAT" | tr -d -)" --save F36.coordinator/get
  control-agent-server api GET "/api/conversations/$CAT" --check tags.project eq "$P2" --check tags.insidercat eq self
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator.title eq qa-f36-cat --check conversations len-eq 0 \
    --save F36.coordinator/conversations
  ```
  The coordinator carries `project=<P2>` next to `insidercat`, and the conversations
  endpoint reports it as the coordinator with no conversations yet.
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
  control-agent-server api PUT "/api/projects/$P1/conversations/$CAT" --expect 409
  control-agent-server api GET "/api/conversations/$CAT" --check tags.project eq "$P2"
  ```
  Both are 409 and the coordinator stays in its project.
- **Add a conversation (`F36.add-conversation`).**
  Requires: `F36.unknown-project`, `F36.create`
  ```sh
  control-agent-server api PUT "/api/projects/$P1/conversations/$W1" --expect 204 --save F36.add-conversation/put
  control-agent-server api PUT "/api/projects/$P1/conversations/$W1" --expect 204
  control-agent-server api GET "/api/conversations/$W1" --check tags.project eq "$P1" --check tags.insiderrole eq worker
  control-agent-server api GET "/api/projects/$P1/conversations" --check conversations len-eq 1 --check conversations.0.title eq qa-f36-w1 \
    --check conversations.0.attention eq idle --check coordinator eq null --save F36.add-conversation/conversations
  ```
  Adding twice is fine, the other tag stays, and the conversation is listed as idle.
- **Move a conversation (`F36.move-conversation`).**
  Requires: `F36.add-conversation`, `F36.coordinator`
  ```sh
  control-agent-server api PUT "/api/projects/$P2/conversations/$W1" --expect 204 --save F36.move-conversation/put
  control-agent-server api GET "/api/projects/$P1/conversations" --check conversations len-eq 0
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations len-eq 1 --check conversations.0.title eq qa-f36-w1
  ```
  A conversation belongs to one project at a time.
- **Attention groups (`F36.attention`).**
  Requires: `F36.move-conversation`
  Needs: `llm`, `tmux`
  ```sh
  ASKS=$(control-agent-server conversation start --title qa-f36-asks --tools terminal --confirmation-policy always \
    --prompt 'Run the shell command: echo qa-f36 > qa-f36.txt' --wait --until waiting_for_confirmation --timeout 180 --print-id)
  OUT=$(control-agent-server conversation start --no-run --placeholder-agent --title qa-f36-outsider --print-id)
  control-agent-server api PUT "/api/projects/$P2/conversations/$ASKS" --expect 204
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations len-eq 2 \
    --check conversations.0.title eq qa-f36-asks --check conversations.0.attention eq needs_you \
    --check conversations.0.execution_status eq waiting_for_confirmation \
    --check conversations.1.title eq qa-f36-w1 --check conversations.1.attention eq idle \
    --check . not-contains qa-f36-outsider --save F36.attention/conversations
  ```
  The conversation waiting for approval comes first as `needs_you`; the idle one
  follows; the untagged conversation is not listed.
- **Remove a conversation (`F36.remove-conversation`).**
  Requires: `F36.move-conversation`
  ```sh
  control-agent-server api DELETE "/api/projects/$P1/conversations/$W1" --expect 404 \
    --check detail eq 'Conversation not in project' --save F36.remove-conversation/other-project
  control-agent-server api DELETE "/api/projects/$P2/conversations/$W1" --expect 200 --check deleted eq true --save F36.remove-conversation/delete
  control-agent-server api DELETE "/api/projects/$P2/conversations/$W1" --expect 404
  control-agent-server api GET "/api/conversations/$W1" --check tags not-contains project --check tags.insiderrole eq worker
  ```
  Only the owning project can remove it, only once, and only the `project`
  tag goes.
- **Coordinator membership (`F36.remove-coordinator`).**
  Requires: `F36.coordinator`
  ```sh
  control-agent-server api DELETE "/api/projects/$P2/conversations/$CAT" --expect 409 \
    --check detail eq "Conversation is the project's coordinator" --save F36.remove-coordinator/delete
  control-agent-server api PATCH "/api/projects/$P2" --json '{"coordinator_conversation_id": null}' --expect 200 \
    --check coordinator_conversation_id eq null
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator eq null --check . contains qa-f36-cat \
    --save F36.remove-coordinator/conversations
  control-agent-server api PATCH "/api/projects/$P2" --json "{\"coordinator_conversation_id\": \"$CAT\"}" --expect 200
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator.title eq qa-f36-cat
  ```
  The coordinator cannot be removed from the project; once cleared it is listed
  with the other conversations, and setting it again restores it.
- **Message a conversation over REST (`F36.message-rest`).**
  Requires: `F36.coordinator`, `F36.unknown-project`
  ```sh
  W2=$(control-agent-server conversation start --no-run --title qa-f36-w2 --print-id)
  control-agent-server api PUT "/api/projects/$P2/conversations/$W2" --expect 204
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Reply with the single word ok and finish."}], "run": true, "refuse_pending_confirmation": true}' \
    --expect 200 --check run eq started --check previous_status eq idle --save F36.message-rest/post
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 0
  control-agent-server api POST "/api/conversations/$W1/events" --json "{\"role\": \"user\", \"content\": [{\"type\": \"text\", \"text\": \"hi\"}], \"sender_conversation_id\": \"$W2\"}" --expect 422 \
    --check detail eq 'Sender is not a conversation of this project'
  control-agent-server conversation events "$W1" --kinds MessageEvent --contains 'not by the person' --expect-count 0
  ```
  The member runs and finishes; a member cannot message `W1`, which left the
  project earlier.
- **How a turn ended (`F36.turn`).**
  Requires: `F36.message-rest`
  Needs: `llm`
  ```sh
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Ask me which colour I prefer, as one short question, then finish. Do not use tools."}], "run": true, "refuse_pending_confirmation": true}' \
    --expect 200 --check run eq started
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$W2" --check tags.turn eq asked --until-ok 10 --save F36.turn/asked
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains '"attention": "needs_input"' --save F36.turn/conversations
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Blue. Reply with the single word noted and finish."}], "run": true, "refuse_pending_confirmation": true}' \
    --expect 200 --check run eq started
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$W2" --check tags.turn eq answered --until-ok 10 --save F36.turn/answered
  ```
  A question finishes as `asked` and lists as `needs_input`; the answer
  reopens the conversation and the next run finishes as `answered`.
- **Resolved (`F36.resolved`).**
  Requires: `F36.turn`
  Needs: `llm`
  ```sh
  control-agent-server api PATCH "/api/conversations/$W2" --json "{\"tags\": {\"project\": \"$P2\", \"turn\": \"answered\", \"resolved\": \"user\"}}" --expect 200
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains '"resolved": "user"' \
    --check conversations contains '"attention": "resolved"' --save F36.resolved/conversations
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Reply with the single word reopened and finish."}], "run": true, "refuse_pending_confirmation": true}' --expect 200
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$W2" --check tags not-contains resolved --check tags.turn eq answered --until-ok 10 \
    --save F36.resolved/reopened
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 0
  ```
  The person's resolve shows as `resolved: "user"`; a new message reopens the
  conversation. (`resolved=auto` needs a critic; the unit tests cover it.)
- **Artifacts over REST (`F36.artifacts-rest`).**
  Requires: `F36.coordinator`
  ```sh
  printf '# qa-f36 notes\n' > "$AGENT_SERVER_VERIFY_RUN/qa-f36-notes.md"
  control-agent-server api POST "/api/projects/$P2/artifacts" --file "file=$AGENT_SERVER_VERIFY_RUN/qa-f36-notes.md" \
    --expect 201 --check name eq qa-f36-notes.md --check source eq user --check size eq 15 --save F36.artifacts-rest/upload
  control-agent-server api GET "/api/projects/$P2/artifacts" --check artifacts len-eq 1 --check artifacts.0.source eq user
  control-agent-server api GET "/api/projects/$P2/artifacts/qa-f36-notes.md" --expect 200 --raw-out "$AGENT_SERVER_VERIFY_RUN/qa-f36-down.md"
  cmp "$AGENT_SERVER_VERIFY_RUN/qa-f36-notes.md" "$AGENT_SERVER_VERIFY_RUN/qa-f36-down.md"
  control-agent-server api POST "/api/projects/$P2/artifacts" --file "file=$AGENT_SERVER_VERIFY_RUN/qa-f36-notes.md" --form name=.hidden --expect 400
  control-agent-server api DELETE "/api/projects/$P2/artifacts/qa-f36-notes.md" --expect 200 --check deleted eq true
  control-agent-server api GET "/api/projects/$P2/artifacts" --check artifacts len-eq 0
  ```
  The download matches the upload byte for byte; a dot name is refused.
- **Model-backed: an agent saves an artifact (`F36.artifacts-tool`).**
  Requires: `F36.artifacts-rest`
  Needs: `llm`
  ```sh
  ART=$(control-agent-server conversation start --no-run --title qa-f36-art --tag "project=$P2" \
    --tools file_editor,save_project_artifact --print-id)
  control-agent-server conversation send "$ART" --wait --timeout 300 --text 'Create the file qa-f36-report.md in the working directory containing the line f36 report. Then save it with save_project_artifact, then try save_project_artifact with path /etc/hostname, then finish.'
  control-agent-server api GET "/api/projects/$P2/artifacts" --check artifacts len-eq 1 --check artifacts.0.name eq qa-f36-report.md \
    --check artifacts.0.source eq agent --check artifacts.0.conversation_id eq "$(printf %s "$ART" | tr -d -)" \
    --save F36.artifacts-tool/list
  control-agent-server conversation events "$ART" --kinds ObservationEvent --contains "outside the conversation's workspace" --expect-min 1
  ```
  The file is copied in as an agent artifact; the path outside the workspace
  is refused.
- **No approval over REST (`F36.message-refused`).**
  Requires: `F36.attention`
  ```sh
  control-agent-server api POST "/api/conversations/$ASKS/events" --json '{"role": "user", "content": [{"type": "text", "text": "go ahead"}], "run": true, "refuse_pending_confirmation": true}' --expect 409 \
    --check detail eq 'Conversation is waiting for confirmation' --save F36.message-refused/post
  control-agent-server api GET "/api/conversations/$ASKS" --check execution_status eq waiting_for_confirmation
  control-agent-server conversation events "$ASKS" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  ```
  Nothing is saved and the conversation keeps waiting for the person.
- **Restart (`F36.persist-restart`).**
  Requires: `F36.create`, `F36.coordinator`
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/projects --check projects len-eq 2 --check projects.1.id eq "$P2" --save F36.persist-restart/list
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator.title eq qa-f36-cat --save F36.persist-restart/conversations
  ```
  Records and membership are read back from disk.
- **Delete a project (`F36.delete`).**
  Requires: `F36.add-conversation`, `F36.create`
  ```sh
  control-agent-server api PUT "/api/projects/$P1/conversations/$W1" --expect 204
  control-agent-server api DELETE "/api/projects/$P1" --expect 200 --check deleted eq true --save F36.delete/delete
  control-agent-server api GET "/api/projects/$P1" --expect 404
  control-agent-server api GET "/api/projects/$P1/conversations" --expect 404
  control-agent-server api GET "/api/conversations/$W1" --check tags.project eq "$P1"
  control-agent-server state cat "$PJ" --not-contains "$P1" --check projects len-eq 1
  ```
  The record is gone; the conversation is untouched, tag included.
- **Model-backed: start and list (`F36.tool-start-and-list`).**
  Requires: `F36.coordinator`
  Needs: `llm`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Start one child conversation titled qa-f36-child with isolation shared whose task is: reply with the single word done and finish. Then call list_project_conversations once and tell me what you see. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ActionEvent --contains start_child_conversation --expect-min 1
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains qa-f36-child --expect-min 2 --save F36.tool-start-and-list/events
  CHILD=$(control-agent-server api GET "/api/conversations/$CAT" --field sub_conversation_ids.0)
  control-agent-server api GET "/api/projects/$P2/conversations" --check . contains qa-f36-child
  control-agent-server conversation wait "$CHILD" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$CHILD" --check tags.project eq "$P2" \
    --check parent_conversation_id eq "$(control-agent-server api GET "/api/conversations/$CAT" --field id)"
  control-agent-server conversation events "$CHILD" --kinds SystemPromptEvent --contains 'You are working on a project with a coordinator.' --expect-count 1
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 1
  ```
  The child copies the coordinator's tags, so it is a project conversation; the
  list tool's observation names it. Its system prompt says who started it, and
  its task message records the coordinator as sender.
- **Model-backed: the coordinator hears back (`F36.wake`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  for _ in $(seq 60); do
    control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation $(printf %s "$CHILD" | tr -d -))" --expect-min 1 >/dev/null 2>&1 && break
    sleep 2
  done
  CHILD_HEX=$(printf %s "$CHILD" | tr -d -)
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation $CHILD_HEX), a conversation in your project" \
    --expect-count 1 --save F36.wake/update
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "\"sender\": \"conversation:$CHILD_HEX\"" --expect-count 1
  control-agent-server conversation wait "$CAT" --until finished --timeout 300
  ```
  When the child finished, its final message reached the coordinator as one
  update (sender: the child), and the coordinator ran on it.
- **Model-backed: read a conversation (`F36.read`).**
  Requires: `F36.wake`
  Needs: `llm`
  ```sh
  control-agent-server api GET "/api/projects/$P2/conversations/$CHILD/messages" --expect 200 \
    --check messages.0.source eq user --check messages.0.sender matches '^conversation:' --save F36.read/rest
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use read_project_conversation on the qa-f36-child conversation and tell me its last message. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'oldest first' --expect-min 1 --save F36.read/events
  ```
  The transcript lists the coordinator's task message first; the tool reads it.
- **Model-backed: message a conversation (`F36.tool-message`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use message_project_conversation to ask the qa-f36-child conversation to reply with the single word again. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'Message saved to conversation' --expect-min 1 --save F36.tool-message/events
  control-agent-server conversation wait "$CHILD" --until finished --timeout 180
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"source": "user"' --expect-count 2
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains 'the coordinator of your project, not by the person' --expect-count 1
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 2
  control-agent-server conversation events "$CHILD" --kinds ActionEvent,MessageEvent --contains '"source": "agent"' --expect-min 2
  ```
  The child receives a second user message, marked as the coordinator's, and
  finishes a second run.
- **Model-backed: no approval by message (`F36.tool-message-refuses-confirmation`).**
  Requires: `F36.attention`, `F36.tool-start-and-list`
  Needs: `llm`, `tmux`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use message_project_conversation to tell the qa-f36-asks conversation: go ahead. Then stop.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'would approve that action' --expect-min 1 \
    --save F36.tool-message-refuses-confirmation/events
  control-agent-server api GET "/api/conversations/$ASKS" --check execution_status eq waiting_for_confirmation
  control-agent-server conversation events "$ASKS" --kinds MessageEvent --contains '"source": "user"' --expect-count 1
  ```
  The tool reports an error, the conversation keeps waiting for the person, and it
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
- `attention` comes from `execution_status`, refined by the `turn` and
  `resolved` tags. `finished` means the run ended, not that the task
  succeeded. `turn` is a heuristic: the last paragraph of the agent's final
  message, outside code, ends a sentence with `?`. Once the agent stops on a
  question it stays `asked` until a user message, so a Stop hook that makes
  it call `finish` after a plain message does not hide the question. The tags are written just
  after the run's final state update, so poll (`--until-ok`) rather than read
  once. There is no per-conversation "ask" text or task progress yet.
- A `resolved` conversation is listed as `resolved` unless it is running
  (`working`) or blocked on the person (`needs_you`).
- `automation_id` comes from the `automationid` tag that OpenHands
  automations set; children of an automation's conversation copy it, so they group
  with that automation. An automation's conversations join a project only when
  it starts them with the project's `project` tag.
- Artifacts are copies: saving the same name again replaces the file.
  Deleting a project deletes its artifacts.
- `refuse_pending_confirmation` answers 409 only when the conversation is already
  waiting. If it reaches a confirmation while the message is being
  delivered, the message is kept and `run` is `waiting_for_confirmation`;
  a run that was already active answers `queued`. A `paused`
  conversation is resumed. Delivery to a running conversation waits for its state lock,
  which a long tool call holds, so the tool waits up to 300 seconds and, on a
  timeout, tells the agent not to resend.
- The tools read the project from the caller's tags on every call, so adding
  or removing the coordinator from a project takes effect on its next call.
  They are registered only by the agent-server (`tool_router.py`), like
  `start_child_conversation`.
- The projects store is a process-wide singleton like the workspaces store
  (F17): changing `OH_PERSISTENCE_DIR` needs a restart.
