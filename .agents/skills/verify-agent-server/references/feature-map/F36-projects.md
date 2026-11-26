# Projects and coordinator tools

A project groups the conversations that work toward one goal: one coordinator
conversation that the user mainly talks to, and the conversations it starts or is
given. Project records (`id`, `name`, `description`,
`coordinator_conversation_id`, timestamps) live in one JSON document,
`projects.json` in the persistence directory, rewritten atomically under a
file lock. Membership is not stored there: a conversation belongs to a project
when its `project` tag holds the project's id (hex), so a child started with
`start_child_conversation` joins its parent's project with the rest of the
copied tags. `GET /api/projects/{id}/conversations` lists the members grouped by
what they need from the user (`attention`: `needs_you`, `asking`,
`working`, `idle`, `resolved`, derived from `execution_status` and tags). Two server-executed
tools, `list_workers` and `message_worker`, give a
coordinator agent typed access to the same list and a way to steer a conversation.
Messages go through the generic `POST /api/conversations/{conversation_id}/events`.
They run as any message does; `message_worker` refuses a conversation waiting for
confirmation, because that run would approve the pending action. With
`sender_conversation_id` the message records
the sending conversation, which must be in the same project when it is in one. Deleting a project never
changes or deletes conversations. A file the server cannot parse, or one
written by a newer schema, is never overwritten: every route answers 409.

Projects is an experimental feature, `projects`, off by default. Turn it on
first with `PATCH /api/settings/experimental/projects {"enabled": true}`. While
it is off, every `/api/projects` route answers 404, the server starts no
coordinators or workers, a member's child is an ordinary child, copied from
its parent as on main, that keeps its parent's tags, `project` included, so it
joins the board when Projects is back on, and no update or user copy reaches a
coordinator. Projects on disk are kept, and conversations started while it was
on keep their `project` tag and tools; each project tool answers that Projects
is turned off, and outside a project that the conversation is not part of
one.

Source: `openhands-agent-server/openhands/agent_server/projects_router.py`, `openhands-agent-server/openhands/agent_server/project_board.py`, `openhands-agent-server/openhands/agent_server/project_artifacts.py`, `openhands-agent-server/openhands/agent_server/project_profiles.py`, `openhands-agent-server/openhands/agent_server/persistence/models.py`, `openhands-agent-server/openhands/agent_server/persistence/store.py`, `openhands-tools/openhands/tools/project/`

Needs: `llm`

Routes: `GET /api/projects/{project_id}/artifacts`,
`POST /api/projects/{project_id}/artifacts`,
`POST /api/projects/{project_id}/artifacts/from-conversation`,
`GET /api/projects/{project_id}/artifacts/{name}`,
`DELETE /api/projects/{project_id}/artifacts/{name}`,
`GET /api/projects/{project_id}/memory`,
`GET /api/projects/{project_id}/memory/{name}`,
`PUT /api/projects/{project_id}/memory/{name}`,
`DELETE /api/projects/{project_id}/memory/{name}`,
`GET /api/projects`, `POST /api/projects`,
`GET /api/projects/{project_id}`, `PATCH /api/projects/{project_id}`,
`DELETE /api/projects/{project_id}`, `GET /api/projects/{project_id}/conversations`,
`DELETE /api/projects/{project_id}/conversations/{conversation_id}`,
`POST /api/projects/{project_id}/conversations/{conversation_id}/resolve`,
`POST /api/projects/{project_id}/conversations/{conversation_id}/reopen`,
`GET /api/projects/{project_id}/conversations/{conversation_id}/messages`

Projects also changes conversation routes that other families own: the
sender and confirmation guard of `POST .../events` (F06), the coordinator's
workers on `POST .../children` (F04), and the server-owned tags on a start
(F04) and a fork (F10).

## Sub-features

- `F36.list-empty`: on a fresh persistence directory `GET /api/projects` returns `{"projects": []}` and creates no `projects.json`.
- `F36.create`: `POST /api/projects` answers 201 with a hex `id`, the name and description, no coordinator; `GET` by id, the list and the `0600` file (`schema_version` 1) hold the same record.
- `F36.validation`: an empty name or a name over 256 characters is 422 and writes nothing.
- `F36.unknown-project`: every route answers 404 for an unknown project id and leaves the conversation's tags unchanged.
- `F36.patch`: `PATCH` renames and changes the description, keeps `created_at` and moves `updated_at`; omitted fields stay.
- `F36.coordinator`: creating a project with `coordinator` starts a new, idle coordinator conversation tagged `project=<id>` with the client's other tags, the coordinator prompt (naming its own id) and the coordinator tools; the conversations endpoint reports it as `coordinator`, apart from the other conversations. A project never adopts an existing conversation.
- `F36.coordinator-missing`: a coordinator with an unknown `agent_profile_id` is 404 and creates no project.
- `F36.coordinator-active-llm`: when the active agent profile names an LLM profile that is not saved (the seeded `default` profile keeps `llm_profile_ref: "default"` until a real LLM is set up), creating a project with a coordinator answers 201 and builds `project-coordinator` on the active LLM profile instead of the dangling reference.
- `F36.start-member`: only the coordinator starts conversations in its project: its `POST .../children` starts a member tagged `project=<id>`, and a member's own `POST .../children` is 409.
- `F36.client-tags`: a client cannot start or fork a conversation whose `project` tag names an existing project: 422, and nothing is created. The other project keys (`turn`, `ask`, `resolved`, `resolvenote`, `prs`), or a `project` tag naming no project, are ordinary client tags.
- `F36.attention`: conversations are grouped `needs_you` (waiting for confirmation), then `working`, `idle` (finished or never run), newest-updated first within a group; non-members are left out.
- `F36.remove-conversation`: `DELETE .../conversations/{conversation_id}` clears the `project` tag and the board tags (`resolved`, `resolvenote`, `turn`, `ask`, and `prs` per `F36.pull-requests`), and no other tag; a second delete, or a delete through another project, is 404.
- `F36.remove-coordinator`: removing the coordinator from the project is 409.
- `F36.message-rest`: `POST /api/conversations/{id}/events` saves the message and starts a run. Without `sender_conversation_id` the message has no `sender`; a member sending to a conversation outside its project is 422.
- `F36.turn`: when a member's run finishes, its `turn` tag says whether the agent's last message asked something (`asking`, attention `asking`) or not (`finished`), and an `asking` turn saves the question, in one line of at most 200 characters, as the `ask` tag (reported as `ask`); a new user message clears both.
- `F36.resolved`: a `resolved` tag (`user` or `auto`) puts a member in the `resolved` group, reported as `resolved`; a new user message clears it. Children do not copy `resolved` or `turn`. `POST .../resolve` and `.../reopen` set and clear it (204, 404 for a non-member), and a client's tag `PATCH` cannot change `project`, `turn`, `ask`, `resolved`, `resolvenote` or `prs`.
- `F36.auto-resolve`: the resolution judge, on the `resolution_judge` LLM profile the first judged turn creates from the conversation's LLM, resolves a member's plain answer (`resolved=auto`, `resolvenote` like `1.00 nothing`, shown as `resolve_note`) and makes a hand-off `turn=asking` with the reply's last paragraph as `ask`; over that question the member's `{"by": "agent"}` resolve is 409 and a coordinator's message leaves it asking.
- `F36.needs-you`: a turn that leaves part of the task undone, with no question, is `finished` and not resolved; once the rest is done the judge resolves it.
- `F36.judge-failed`: when the resolution judge fails (its `resolution_judge` profile cannot reach a model), a member's finished turn is `turn=asking` with the reply's last paragraph as `ask`, listed as `asking`, and is not resolved.
- `F36.pull-requests`: when a member's agent runs `gh pr create` (or a tool named `create_pull_request`) and the result prints a GitHub pull request URL, the server adds `owner/repo#number` to its `prs` tag, oldest first, kept across turns; `GET .../conversations` reports it as `pull_requests` (`{owner, repo, number}`). A URL printed by another command is not recorded, and a client's tag `PATCH` cannot change `prs`.
- `F36.self-resolve`: a member with `resolve_conversation` resolves its own conversation when nothing is left, such as after the user's "merged": `POST .../resolve` with `{"by": "agent"}` answers 202 at once (the agent is inside its own tool call, which holds the state lock) and sets `resolved=auto` and `resolvenote=agent`, never over the user's `resolved=user`, and the resolution judge does not run on that turn.
- `F36.artifacts-rest`: a project's artifacts can be uploaded (multipart), listed with `source: user`, downloaded byte for byte and deleted; unsafe names are 400. `POST .../artifacts/from-conversation` copies a member's workspace file as `source: agent` with that conversation's id; a path outside its workspace is 400 and a conversation outside the project 404.
- `F36.artifacts-tool`: a member with `write_artifact` saves a file from its workspace; the list shows it with `source: agent` and the conversation's id, and a path outside the workspace is refused. With `list_artifacts` and `read_artifact` it sees the project's files and reads one: small UTF-8 text inline, anything else copied into its workspace (`.agent_tmp/artifacts/<name>` unless it names a path inside the workspace).
- `F36.memory-rest`: `PUT/GET/DELETE /api/projects/{id}/memory/{name}` keep `MEMORY.md` and lowercase `.md` topic files under `projects/<id>/memory/`; `GET .../memory` lists them, index first. A write or delete with a stale `base_revision` (or a create over an existing file) is 409 with the current file; other names are 400.
- `F36.memory-tool`: an agent with `read_project_memory` and `update_project_memory` reads `MEMORY.md` and adds a line to it with the revision it read; a child a project member starts gets `MEMORY.md` in its first message, marked as data.
- `F36.persist-restart`: projects and membership survive a server restart.
- `F36.flag-off`: turning `projects` off makes every `/api/projects` route 404 `Projects are turned off` at once, without a restart, and writes nothing; the projects on disk and the members' `project` tags stay, and turning it on again serves them (200) at once.
- `F36.runtime-env`: `OH_RUNTIME_EXPERIMENTAL_FEATURES` replaces the stored switch for the routes: `{"projects": false}` makes them 404 while the stored switch (and the list) says on, `{"projects": true}` serves them while it says off, and an unparsable value counts as every switch off.
- `F36.flag-off-tools`: while `projects` is off a coordinator's project tool answers `Projects is turned off`, its child is an ordinary child that keeps the `project` tag and wakes no coordinator, and after turning it on again that child is on the board.
- `F36.delete`: `DELETE /api/projects/{id}` removes the record, then the conversations endpoint is 404, and member conversations lose the `project` tag and the board's tags (their other tags stay).
- `F36.corrupted-file`: an unparsable or future-schema `projects.json` makes reads and writes 409 and is never rewritten.
- `F36.tool-start-and-list`: a coordinator with `start_child_conversation` and `list_workers` starts a child that joins the project, and its list call reports that child with its attention group. The child's system prompt names the coordinator, and its first message has `sender: "conversation:<coordinator id hex>"`.
- `F36.wake`: when a member finishes a run the coordinator started (its task or follow-up message), the coordinator gets one user message with the member's final message, `sender: "conversation:<member id hex>"` and an "Update from" header, and runs on it. The coordinator's own turns get a `turn` tag but are never judged or resolved. Each message the user writes in a member, and the member's final reply to it, reaches the coordinator as a copy for its awareness (`[Message from the user in ...`, `[Reply in ...`, sender the member) without running it (`F36.turn`).
- `F36.read`: `GET .../conversations/{conversation_id}/messages` lists a member's latest messages (with senders) and `finish`, oldest first; the `read_worker` tool reads it.
- `F36.tool-message`: `message_worker` delivers a follow-up to a finished conversation and requests a run: the conversation gets a second user message, sent by the coordinator (`sender` and a header line saying it is not the user), and runs again.
- `F36.mention`: tools and headers name conversations as mentions, `[Title](conversation:<id hex>)`: `list_workers` lists each conversation that way, and a coordinator asked which conversation handles a task answers with that mention.
- `F36.restore`: after a server restart a coordinator and a worker keep their prompt, `<PROJECT>` block and project tools, and use a project tool on their next turn; restore is main's, from the agent saved with the conversation.
- `F36.tool-message-refuses-confirmation`: `message_worker` refuses a conversation waiting for confirmation with an error observation; the conversation stays waiting and gets no new message.

## How to get to it (agent POV)

- REST: `GET /api/projects` returns `{"projects": [ProjectItem]}`, oldest first.
  `POST /api/projects` with `{"name", "description"?, "worker_agent_profile_id"?,
  "coordinator"?: {"working_dir", "agent_profile_id"?, "title"?, "tags"?, ...}}`
  answers 201 and the `ProjectItem`; with `coordinator` it first starts that
  new coordinator conversation. `GET`, `PATCH` (`name`, `description`,
  `worker_agent_profile_id`) and `DELETE` act on `/api/projects/{project_id}`.
- REST: `GET /api/projects/{project_id}/conversations` returns
  `{"project_id", "coordinator": ProjectConversation | null, "conversations": [ProjectConversation]}`
  where a conversation is `{conversation_id, title, execution_status, attention,
  resolved, turn, automation_id, parent_conversation_id, pull_requests,
  created_at, updated_at}`. `pull_requests` lists `{owner, repo, number}`
  from the server-owned `prs` tag. `attention` is `needs_you`, `asking`, `working`,
  `idle` (finished or never run, not resolved) or `resolved`.
- Tags the server sets on project members (`openhands.agent_server.project_board`):
  `turn=asking|finished` when a run finishes, and `resolved=auto` plus
  `resolvenote` when a finished turn leaves nothing for the user (never
  over `resolved=user`, never on an `asking` turn). The resolution judge
  (`openhands.sdk.conversation.resolution`, `resolution_judge.py`) asks typed
  questions of the saved LLM profile `resolution_judge`, which the first
  judged turn creates as a copy of the conversation's own LLM (edit it in
  `/api/profiles` to use another model). It sees the turn plus the conversation's first message and earlier
  requests. The model answers seven narrow yes/no judgments (handled,
  task_done, asks_user, hands_off_review, offers_choice, asks_to_check,
  reports_problem) and code decides: resolved when handled and task_done are
  >= 0.85 and every hand-off < 0.2 (`resolvenote` like `0.93 nothing`). A
  substantive question, choice, review or check (>= 0.5) makes the turn
  `asking` (Needs you), with the reply's last paragraph as `ask`; work left
  that waits on no one stays `finished` and unresolved (Idle). There are no
  text heuristics: without a judge a turn is `finished`, and a critic score
  >= 0.9 still resolves (`0.95 critic`). When the judge fails or times out,
  the turn is `asking` (Needs you) with the reply's last paragraph as `ask`,
  and nothing resolves it. The coordinator's turns get `turn` (and `ask` from
  an `ask_user` card) but are never judged or resolved automatically.
  The board shows the note as `resolve_note`.
  Clients resolve with `POST /api/projects/{id}/conversations/{cid}/resolve`
  and reopen with `.../reopen` (both 204, idempotent, and both clear
  `resolvenote`); a new user message removes both tags. These routes are
  the only way to resolve: the conversation `PATCH` leaves `resolved` as it
  is.
- REST: `GET /api/projects/{project_id}/artifacts` lists
  `{name, size, source, conversation_id, created_at, updated_at}` newest
  first; `POST` there uploads (multipart `file`, optional `name` form field);
  `POST .../artifacts/from-conversation` with `{conversation_id, path, name?}`
  copies a member's workspace file; `GET`/`DELETE .../artifacts/{name}`
  download and delete. A download is sent like a workspace file or
  trajectory download: `application/octet-stream`, `Content-Disposition:
  attachment`, plus `X-Content-Type-Options: nosniff`, so an uploaded HTML
  or SVG file is saved, never rendered from the server's origin. Names are flat (letters, digits, spaces, `._()+-`,
  no leading dot, no trailing dot or space, no Windows device names), and a
  name differing from another only in case is 409. Files live in
  `<persistence dir>/projects/<project id>/artifacts/`, up to 100 MiB each.
- REST: membership is the conversation's `project` tag, and only the server
  sets it: on the coordinator `POST /api/projects` starts, and on each
  conversation the coordinator
  starts with `POST /api/conversations/{coordinator}/children`. No client
  adds an existing conversation to a project. `DELETE
  /api/projects/{project_id}/conversations/{conversation_id}`
  (`{"deleted": true}`) clears the tag and keeps the others. The
  server owns `project`, `turn`, `ask`, `resolved` and `resolvenote`:
  `PATCH /api/conversations/{id}` with `tags` replaces the other tags and
  keeps these (except `resolved`, above), so it cannot change membership.
- REST: `POST /api/conversations/{conversation_id}/events` (F04) takes an
  optional `sender_conversation_id`, which records
  `sender: "conversation:<id hex>"` and adds a "Sent by" first line naming the
  coordinator, another conversation in the project, or another conversation;
  422 when the sender is unknown, is the target, or is in a project the
  target is not in.
- Agent tools (registered by the agent-server, not part of the default
  preset): `list_workers`, `message_worker`,
  `write_artifact`, `list_artifacts` and `read_artifact` from
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
  and an active agent profile on it: the `F36.tool-*` bullets and
  `F36.attention` run a model, and workers start from the predefined worker
  profile, which the server builds from the active one (with no agent
  profile, a worker's start is 422). The other REST bullets use placeholder
  agents.
- The `projects` experimental feature is on:
  `control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": true}' --expect 200`.
  Off, every `/api/projects` route answers 404.
  `F36.attention` and `F36.tool-message-refuses-confirmation` need `tmux`
  for the terminal tool.
- Bullets run in order and build on each other's projects and
  conversations; `F36.persist-restart` restarts the run.

  ```sh
  control-agent-server llm preset deepseek
  control-agent-server api POST /api/agent-profiles/qa-f36-agent \
    --json '{"llm_profile_ref": "deepseek-flash", "tools": [{"name": "terminal"}, {"name": "file_editor"}]}' --expect 201
  control-agent-server api POST "/api/agent-profiles/$(control-agent-server api GET /api/agent-profiles/qa-f36-agent --field profile.id)/activate" --expect 200
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": true}' --expect 200
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
  mkdir -p "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat"
  P2=$(control-agent-server api POST /api/projects \
    --json "{\"name\": \"qa-f36-two\", \"coordinator\": {\"working_dir\": \"$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat\", \"title\": \"qa-f36-cat\", \"tags\": {\"insidercat\": \"self\"}}}" \
    --expect 201 --check coordinator_conversation_id matches '^[0-9a-f]{32}$' --save F36.coordinator/post --field id)
  CAT=$(python3 -c 'import sys, uuid; print(uuid.UUID(sys.argv[1]))' \
    "$(control-agent-server api GET "/api/projects/$P2" --field coordinator_conversation_id)")
  control-agent-server api GET "/api/conversations/$CAT" --check tags.project eq "$P2" --check tags.insidercat eq self \
    --check execution_status eq idle --check agent.system_prompt_filename eq system_prompt_project_coordinator.j2 \
    --check . contains list_workers --check . contains message_worker --save F36.coordinator/get
  control-agent-server api GET "/api/conversations/$CAT" \
    --check agent.agent_context.project_context contains "You are this project's coordinator, conversation $CAT."
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator.title eq qa-f36-cat --check conversations len-eq 0 \
    --save F36.coordinator/conversations
  ```
  The new coordinator is idle, carries `project=<P2>` next to `insidercat`,
  runs the coordinator prompt (whose `<PROJECT>` block names its id) with the
  coordinator tools, and the conversations endpoint reports it as the
  coordinator.
- **Missing coordinator profile (`F36.coordinator-missing`).**
  Requires: `F36.coordinator`
  ```sh
  control-agent-server api POST /api/projects \
    --json "{\"name\": \"qa-f36-ghost\", \"coordinator\": {\"working_dir\": \"$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat\", \"agent_profile_id\": \"00000000-0000-0000-0000-000000000001\"}}" \
    --expect 404 --save F36.coordinator-missing/post
  control-agent-server api GET /api/projects --check . not-contains qa-f36-ghost
  ```
  Nothing is created.
- **A coordinator on the active LLM profile (`F36.coordinator-active-llm`).**
  A second server whose `default` agent profile is seeded before any LLM
  profile exists, so it names the unsaved `default` LLM profile; then a stub
  LLM profile becomes active and a project starts a coordinator. The stub
  profile has no key; the recipe asserts only the create and the saved
  profile, so no model call is needed.
  ```sh
  SEEDED=$(control-agent-server launch --new --name f36-seeded --print-run)
  trap 'control-agent-server stop --run "$SEEDED" >/dev/null' EXIT
  control-agent-server api GET /api/agent-profiles --run "$SEEDED" --check profiles.0.name eq default \
    --check profiles.0.llm_profile_ref eq default
  control-agent-server llm set --run "$SEEDED" --profile qa-f36-stub --model deepseek/deepseek-v4-flash --no-api-key --no-validate
  control-agent-server api PATCH /api/settings/experimental/projects --run "$SEEDED" --json '{"enabled": true}' --expect 200
  mkdir -p "$SEEDED/fixtures/qa-f36-seeded"
  control-agent-server api POST /api/projects --run "$SEEDED" \
    --json "{\"name\": \"qa-f36-seeded\", \"coordinator\": {\"working_dir\": \"$SEEDED/fixtures/qa-f36-seeded\"}}" \
    --expect 201 --save F36.coordinator-active-llm/post
  control-agent-server api GET /api/agent-profiles/project-coordinator --run "$SEEDED" --check profile.llm_profile_ref eq qa-f36-stub
  control-agent-server stop --run "$SEEDED"
  ```
  The project is created and `project-coordinator` is saved with
  `llm_profile_ref` set to the active LLM profile (`qa-f36-stub`), as a chat
  started from `default` runs on it.
- **The coordinator starts a member (`F36.start-member`).**
  Requires: `F36.coordinator`
  Needs: `llm`
  ```sh
  M1=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-m1", "task": "Reply with the single word ok and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --check parent_conversation_id eq "$CAT" --save F36.start-member/start --field conversation_id)
  control-agent-server conversation wait "$M1" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$M1" --check tags.project eq "$P2" --check parent_conversation_id eq "$CAT"
  control-agent-server api GET "/api/projects/$P2/conversations" --check . contains qa-f36-m1 --save F36.start-member/conversations
  control-agent-server api POST "/api/conversations/$M1/children" \
    --json '{"title": "qa-f36-m1-child", "task": "Reply with ok.", "isolation": "shared"}' \
    --expect 409 --check detail eq "Only the project's coordinator starts conversations in a project" --save F36.start-member/refused
  control-agent-server api GET "/api/projects/$P2/conversations" --check . not-contains qa-f36-m1-child
  ```
  The coordinator's child joins the project with `project=<P2>`; a member
  cannot start conversations of its own there.
- **Clients cannot claim a project (`F36.client-tags`).**
  Requires: `F36.coordinator`, `F36.unknown-project`
  ```sh
  if control-agent-server conversation start --no-run --placeholder-agent --title qa-f36-claim --tag "project=$P2" --print-id; then false; fi
  control-agent-server api POST "/api/conversations/$W1/fork" --json "{\"tags\": {\"project\": \"$P2\"}}" --expect 422 \
    --check detail contains 'started by its coordinator' --save F36.client-tags/fork
  control-agent-server api GET "/api/projects/$P2/conversations" --check . not-contains qa-f36-claim
  OWN=$(control-agent-server conversation start --no-run --placeholder-agent --title qa-f36-own-tags --tag turn=asking --tag "project=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')" --print-id)
  control-agent-server api GET "/api/conversations/$OWN" --check tags.turn eq asking --save F36.client-tags/own
  ```
  A start or fork that would join an existing project is 422 and creates
  nothing; project keys that name no project are the client's own tags.
- **Attention groups (`F36.attention`).**
  Requires: `F36.start-member`
  Needs: `llm`, `tmux`
  ```sh
  ASKS=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-asks", "task": "Reply with the single word ready and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$ASKS" --until finished --timeout 180
  control-agent-server api POST "/api/conversations/$ASKS/confirmation_policy" --json '{"policy": {"kind": "AlwaysConfirm"}}' --expect 200
  control-agent-server conversation send "$ASKS" --wait --until waiting_for_confirmation --timeout 180 \
    --text 'Run the shell command: echo qa-f36 > qa-f36.txt'
  OUT=$(control-agent-server conversation start --no-run --placeholder-agent --title qa-f36-outsider --print-id)
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations len-eq 2 \
    --check conversations.0.title eq qa-f36-asks --check conversations.0.attention eq needs_you \
    --check conversations.0.execution_status eq waiting_for_confirmation \
    --check conversations.1.title eq qa-f36-m1 --check conversations.1.attention matches '^(idle|resolved)$' \
    --check . not-contains qa-f36-outsider --save F36.attention/conversations
  ```
  The conversation waiting for approval comes first as `needs_you`; the
  finished one follows (`resolved` when the judge resolved it); the untagged
  conversation is not listed. A member copies its coordinator's confirmation
  policy when it starts, so this one asks for approval only after it is set.
- **Remove a conversation (`F36.remove-conversation`).**
  Requires: `F36.start-member`, `F36.create`
  ```sh
  control-agent-server api PATCH "/api/conversations/$M1" --json '{"tags": {"qa": "f36"}}' --expect 200
  control-agent-server api GET "/api/conversations/$M1" --check tags.project eq "$P2" --check tags.turn exists --until-ok 30
  control-agent-server api DELETE "/api/projects/$P1/conversations/$M1" --expect 404 \
    --check detail eq 'Conversation not in project' --save F36.remove-conversation/other-project
  control-agent-server api DELETE "/api/projects/$P2/conversations/$M1" --expect 200 --check deleted eq true --save F36.remove-conversation/delete
  control-agent-server api DELETE "/api/projects/$P2/conversations/$M1" --expect 404
  control-agent-server api GET "/api/conversations/$M1" --check tags not-contains project --check tags.qa eq f36 \
    --check tags.turn missing --check tags.ask missing --check tags.resolved missing --check tags.resolvenote missing \
    --save F36.remove-conversation/after
  ```
  Only the owning project can remove it, only once, and the `project` tag
  and the board's tags (here `turn`, and `resolved` when the judge resolved
  it) go while the client's own tag stays. `F36.pull-requests` shows `prs`
  cleared the same way.
- **Coordinator membership (`F36.remove-coordinator`).**
  Requires: `F36.coordinator`
  ```sh
  control-agent-server api DELETE "/api/projects/$P2/conversations/$CAT" --expect 409 \
    --check detail eq "Conversation is the project's coordinator" --save F36.remove-coordinator/delete
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator.title eq qa-f36-cat
  ```
  The coordinator cannot be removed from its project.
- **Message a conversation over REST (`F36.message-rest`).**
  Requires: `F36.start-member`, `F36.unknown-project`
  Needs: `llm`
  ```sh
  W2=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-w2", "task": "Reply with the single word ready and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server conversation wait "$CAT" --until idle,finished --timeout 300
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Reply with the single word ok and finish."}], "run": true}' \
    --expect 200 --save F36.message-rest/post
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"source": "user"' --expect-count 2
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 1
  control-agent-server api POST "/api/conversations/$W1/events" --json "{\"role\": \"user\", \"content\": [{\"type\": \"text\", \"text\": \"hi\"}], \"sender_conversation_id\": \"$W2\"}" --expect 422 \
    --check detail eq 'Sender is not a conversation of this project'
  control-agent-server conversation events "$W1" --kinds MessageEvent --contains 'not by the user' --expect-count 0
  ```
  The coordinator's task is the member's first message, with the coordinator
  as its sender; the user's message has none. A member cannot message `W1`,
  which is not in the project.
- **How a turn ended (`F36.turn`).**
  Requires: `F36.message-rest`
  Needs: `llm`
  ```sh
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Ask me which colour I prefer, as one short question, then finish. Do not use tools."}], "run": true}' \
    --expect 200
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$W2" --check tags.turn eq asking --check tags.ask matches '(?i)colou?r.*\?$' --until-ok 60 --save F36.turn/asking
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains '"attention": "asking"' \
    --check conversations contains '"ask": "' --save F36.turn/conversations
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Blue. Reply with the single word noted and finish."}], "run": true}' \
    --expect 200
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$W2" --check tags.turn eq finished --check tags not-contains ask --until-ok 60 --save F36.turn/finished
  W2_HEX=$(printf %s "$W2" | tr -d -)
  for _ in $(seq 30); do
    control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation:$W2_HEX), a conversation in your project, to the user" --expect-min 2 >/dev/null 2>&1 && break
    sleep 2
  done
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation:$W2_HEX), a conversation in your project. For your awareness" --expect-min 2 \
    --save F36.turn/coordinator-copies
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation:$W2_HEX), a conversation in your project, to the user" --expect-min 2
  control-agent-server api GET "/api/conversations/$CAT" --check execution_status matches '^(idle|finished)$'
  ```
  A question finishes as `asking` with the question as `ask` and lists as
  `asking`; the answer reopens the conversation and the next run
  finishes as `finished`, without an `ask`. The coordinator gets a copy of
  each of the user's messages and of each reply, and is not run.
- **Resolved (`F36.resolved`).**
  Requires: `F36.turn`
  Needs: `llm`
  ```sh
  control-agent-server api PATCH "/api/conversations/$W2" --json "{\"tags\": {\"project\": \"qa-f36-elsewhere\", \"turn\": \"asking\", \"resolved\": \"user\"}}" --expect 200
  control-agent-server api GET "/api/conversations/$W2" --check tags.project eq "$P2" --check tags.turn eq finished \
    --check tags.resolved ne user
  control-agent-server api POST "/api/projects/$P2/conversations/$W2/resolve" --expect 204
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains '"resolved": "user"' \
    --check conversations contains '"attention": "resolved"' --save F36.resolved/conversations
  control-agent-server api POST "/api/projects/$P2/conversations/$W2/reopen" --expect 204 --save F36.resolved/reopen
  control-agent-server api GET "/api/conversations/$W2" --check tags.project eq "$P2" --check tags.resolved missing \
    --check tags.resolvenote missing --save F36.resolved/reopened-by-route
  control-agent-server api POST "/api/projects/$P2/conversations/$W2/reopen" --expect 204
  control-agent-server api POST "/api/projects/$P2/conversations/$W1/reopen" --expect 404
  control-agent-server api POST "/api/projects/$P2/conversations/$W2/resolve" --expect 204
  control-agent-server api GET "/api/conversations/$W2" --check tags.resolved eq user
  control-agent-server api POST "/api/conversations/$W2/events" --json '{"role": "user", "content": [{"type": "text", "text": "Reply with the single word reopened and finish."}], "run": true}' --expect 200
  control-agent-server conversation wait "$W2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$W2" --check tags.resolved ne user --check tags.turn eq finished --until-ok 10 \
    --save F36.resolved/reopened
  control-agent-server conversation events "$W2" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 1
  ```
  A client's tag `PATCH` keeps the membership and the `turn` the server set.
  The user's resolve shows as `resolved: "user"`; `reopen` clears it (again
  204 when nothing is left to clear, 404 for `W1`, which is not a member),
  and after a second resolve a new message reopens the conversation. The reply to that message is judged again, so a trivial turn
  may come back as `resolved: "auto"`, never `user`.
- **Resolved automatically (`F36.auto-resolve`).**
  Requires: `F36.message-rest`
  Needs: `llm`
  ```sh
  A1=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-answer", "task": "What is 17 times 23? Answer in one sentence and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  A2=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-handoff", "task": "Suggest two names for my new cat, with one line on each. End your final message with exactly: Tell me which one you prefer. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$A1" --until finished --timeout 180
  control-agent-server conversation wait "$A2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$A1" --check tags.resolved eq auto --check tags.resolvenote matches '^(0\.(8[5-9]|9\d)|1\.00) nothing$' --until-ok 30 \
    --save F36.auto-resolve/answer
  control-agent-server api GET "/api/conversations/$A2" --check tags.turn eq asking --check tags.ask matches 'Tell me which one you prefer\.$' --check tags not-contains resolved --until-ok 30 \
    --save F36.auto-resolve/handoff
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains '"resolve_note": "' --save F36.auto-resolve/board
  control-agent-server api GET /api/profiles --check profiles contains '"name": "resolution_judge"' --save F36.auto-resolve/judge-profile
  control-agent-server api POST "/api/projects/$P2/conversations/$A2/resolve" --json '{"by": "agent"}' --expect 409 --save F36.auto-resolve/agent-refused
  control-agent-server conversation wait "$CAT" --until idle,finished --timeout 300
  control-agent-server api POST "/api/conversations/$A2/events" --json "{\"role\": \"user\", \"content\": [{\"type\": \"text\", \"text\": \"You are finished, thanks. Reply with the single word ok.\"}], \"run\": true, \"sender_conversation_id\": \"$CAT\"}" --expect 200
  control-agent-server conversation wait "$A2" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$A2" --check tags.turn eq asking --check tags.ask matches 'Tell me which one you prefer\.$' --check tags not-contains resolved --until-ok 30 \
    --save F36.auto-resolve/still-asking
  ```
  Both are the coordinator's tasks. The judge runs on the `resolution_judge`
  profile, created from the conversation's own LLM. A question for the user stays theirs, also
  in a turn the coordinator started: the member cannot resolve over it (409),
  and a coordinator's message ("you are finished") leaves it on Needs you.
  A plain answer resolves as `auto` with a note such as `1.00 nothing`. A
  hand-off that ends without a question mark (`Tell me which one you prefer.`)
  becomes `turn=asking` with the reply's last paragraph as `ask` (Needs you).
- **Unfinished work stays open (`F36.needs-you`).**
  Requires: `F36.auto-resolve`
  Needs: `llm`
  ```sh
  A3=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-two-haiku", "task": "In this conversation we will write two haiku: one about rain, then one about snow. Write only the rain haiku now, and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$A3" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$A3" --check tags.turn eq finished --check tags not-contains resolved --until-ok 30 --save F36.needs-you/unfinished
  control-agent-server api POST "/api/conversations/$A3/events" --json '{"role": "user", "content": [{"type": "text", "text": "Now the snow one. Do not use tools."}], "run": true}' --expect 200
  control-agent-server conversation wait "$A3" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$A3" --check tags.resolved eq auto --until-ok 30 --save F36.needs-you/finished
  ```
  With half the task left and no question, the turn is `finished` and not
  resolved (Idle). Once the second haiku is written, the judge reads the first
  message as context and resolves. Verified 2026-11-10 with deepseek-flash.
- **The judge fails (`F36.judge-failed`).**
  Requires: `F36.auto-resolve`
  Needs: `llm`
  ```sh
  control-agent-server api POST /api/profiles/resolution_judge \
    --json '{"llm": {"model": "openai/qa-f36-judge", "api_key": "qa-f36-judge-key", "base_url": "http://127.0.0.1:9/v1", "num_retries": 0}}' --expect 201
  JF=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-judge-down", "task": "What is 17 times 23? Answer in one sentence and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$JF" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$JF" --check tags.turn eq asking --check tags.ask matches '391' \
    --check tags.resolved missing --until-ok 120 --save F36.judge-failed/asking
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains qa-f36-judge-down \
    --check conversations contains '"attention": "asking"' --save F36.judge-failed/board
  control-agent-server logs --grep 'Resolution judge failed' --expect-min 1
  control-agent-server api DELETE /api/profiles/resolution_judge --expect 200
  ```
  The same plain answer that `F36.auto-resolve` resolves waits on the user
  when nobody could judge it: `turn=asking`, the reply's last paragraph as
  `ask`, no `resolved`. The recipe deletes the broken judge profile, so the
  next judged turn copies the conversation's LLM again.
- **Pull requests a member opened (`F36.pull-requests`).** Translated: a
  stub `gh` in the shared workspace prints a pull request URL, as the real
  `gh pr create` does; no GitHub account is used.
  Requires: `F36.coordinator`
  Needs: `llm`, `tmux`
  ```sh
  mkdir -p "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat/qa-bin"
  cat > "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat/qa-bin/gh" <<'EOF'
  #!/bin/sh
  n=$(cat "$0.count" 2>/dev/null || echo 40); n=$((n + 1)); echo "$n" > "$0.count"
  echo "https://github.com/qa-f36/qa-repo/pull/$n"
  EOF
  chmod +x "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat/qa-bin/gh"
  PRW=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-prs", "task": "Run these two shell commands with the terminal tool, one at a time, exactly as written: first `echo https://github.com/qa-f36/qa-repo/pull/99`, then `qa-bin/gh pr create --title qa --body qa`. Then reply with the URL the second one printed and finish.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$PRW" --until finished --timeout 300
  control-agent-server conversation events "$PRW" --kinds ActionEvent --contains 'gh pr create' --expect-min 1
  control-agent-server api GET "/api/conversations/$PRW" --check tags.prs eq 'qa-f36/qa-repo#41' --until-ok 30 --save F36.pull-requests/tag
  control-agent-server api GET "/api/projects/$P2/conversations" --field conversations \
    | jq -e '.[] | select(.title == "qa-f36-prs") | .pull_requests == [{"owner": "qa-f36", "repo": "qa-repo", "number": 41}]'
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains '"number": 41' --save F36.pull-requests/board
  control-agent-server conversation wait "$CAT" --until idle,finished --timeout 300
  control-agent-server conversation send "$PRW" --wait --timeout 300 \
    --text 'Run `qa-bin/gh pr create --title qa2 --body qa2` once with the terminal tool, then reply with the URL it printed and finish.'
  control-agent-server api GET "/api/conversations/$PRW" --check tags.prs eq 'qa-f36/qa-repo#41,qa-f36/qa-repo#42' --until-ok 30 \
    --save F36.pull-requests/second
  control-agent-server api PATCH "/api/conversations/$PRW" --json '{"tags": {"prs": "qa-f36/qa-other#1", "qa": "f36"}}' --expect 200
  control-agent-server api GET "/api/conversations/$PRW" --check tags.prs eq 'qa-f36/qa-repo#41,qa-f36/qa-repo#42' --check tags.qa eq f36
  control-agent-server conversation wait "$CAT" --until idle,finished --timeout 300
  control-agent-server api DELETE "/api/projects/$P2/conversations/$PRW" --expect 200 --check deleted eq true
  control-agent-server api GET "/api/conversations/$PRW" --check tags.prs missing --check tags.project missing --check tags.turn missing \
    --check tags.qa eq f36 --save F36.pull-requests/removed
  ```
  The `echo` of a URL is not a pull request and is not recorded; the stub's
  `gh pr create` adds `qa-f36/qa-repo#41`, the board lists it as
  `pull_requests`, and the next turn's `gh pr create` appends `#42` to it.
  A client's tag `PATCH` keeps the server's value, and removing the
  conversation from the project clears `prs` with the board's other tags.
  Needs `jq`.
- **The worker resolves itself (`F36.self-resolve`).**
  Requires: `F36.auto-resolve`
  Needs: `llm`
  ```sh
  SR=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-self-resolve", "task": "You opened PR #12 that fixes a typo in README.md (pretend; do not use tools). Say in one sentence that it is ready for my review, and finish.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$SR" --until finished --timeout 300
  control-agent-server conversation send "$SR" --wait --timeout 300 --text 'merged'
  control-agent-server conversation events "$SR" --kinds ActionEvent --contains resolve_conversation --expect-min 1
  control-agent-server api GET "/api/conversations/$SR" --check tags.resolved eq auto --check tags.resolvenote eq agent --until-ok 30 \
    --save F36.self-resolve/resolved
  control-agent-server api POST "/api/projects/$P2/conversations/$SR/resolve" --expect 204
  control-agent-server api POST "/api/projects/$P2/conversations/$SR/resolve" --json '{"by": "agent"}' --expect 202
  control-agent-server api GET "/api/conversations/$SR" --check tags.resolved eq user --check tags not-contains resolvenote
  ```
  The user's "merged" leaves nothing to do: the agent calls
  `resolve_conversation` and ends its turn, and the note is `agent`, not a
  judge's probability. An agent's resolve never replaces the user's.
- **Artifacts over REST (`F36.artifacts-rest`).**
  Requires: `F36.coordinator`, `F36.unknown-project`
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
  printf 'qa f36 from cat\n' > "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat/qa-f36-cat-notes.txt"
  control-agent-server api POST "/api/projects/$P2/artifacts/from-conversation" \
    --json "{\"conversation_id\": \"$CAT\", \"path\": \"qa-f36-cat-notes.txt\", \"name\": \"qa-f36-copied.txt\"}" \
    --expect 201 --check name eq qa-f36-copied.txt --check source eq agent --check size eq 16 \
    --check conversation_id eq "$(printf %s "$CAT" | tr -d -)" --save F36.artifacts-rest/from-conversation
  control-agent-server api GET "/api/projects/$P2/artifacts/qa-f36-copied.txt" --expect 200 --raw-out "$AGENT_SERVER_VERIFY_RUN/qa-f36-copied.txt"
  cmp "$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat/qa-f36-cat-notes.txt" "$AGENT_SERVER_VERIFY_RUN/qa-f36-copied.txt"
  control-agent-server api POST "/api/projects/$P2/artifacts/from-conversation" \
    --json "{\"conversation_id\": \"$CAT\", \"path\": \"/etc/hostname\"}" --expect 400
  control-agent-server api POST "/api/projects/$P2/artifacts/from-conversation" \
    --json "{\"conversation_id\": \"$W1\", \"path\": \"qa-f36-cat-notes.txt\"}" --expect 404 --check detail eq 'Conversation not in project'
  control-agent-server api DELETE "/api/projects/$P2/artifacts/qa-f36-copied.txt" --expect 200
  control-agent-server api GET "/api/projects/$P2/artifacts" --check artifacts len-eq 0
  ```
  The download matches the upload byte for byte; a dot name is refused. A
  file in the coordinator's workspace is copied in as an `agent` artifact
  with its conversation's id; a path outside the workspace is 400 and `W1`,
  which is not a member, is 404. The recipe deletes both artifacts again.
- **Model-backed: an agent saves an artifact (`F36.artifacts-tool`).**
  Requires: `F36.artifacts-rest`
  Needs: `llm`
  ```sh
  ART=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-art", "task": "Create the file qa-f36-report.md in the working directory containing the line f36 report. Then save it with write_artifact, then try write_artifact with path /etc/hostname. Then use list_artifacts, and read_artifact for qa-f36-report.md with path copies/report.md, then finish.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$ART" --until finished --timeout 300
  control-agent-server api GET "/api/projects/$P2/artifacts" --check artifacts len-eq 1 --check artifacts.0.name eq qa-f36-report.md \
    --check artifacts.0.source eq agent --check artifacts.0.conversation_id eq "$(printf %s "$ART" | tr -d -)" \
    --save F36.artifacts-tool/list
  control-agent-server conversation events "$ART" --kinds ObservationEvent --contains "outside the conversation's workspace" --expect-min 1
  control-agent-server conversation events "$ART" --kinds ObservationEvent --contains "1 project artifact(s)" --expect-min 1
  control-agent-server conversation events "$ART" --kinds ObservationEvent --contains "Copied artifact 'qa-f36-report.md'" --expect-min 1
  ```
  The file is copied in as an agent artifact; the path outside the workspace
  is refused; the agent lists the artifact and copies it back to the path it
  named.
- **Project memory over REST (`F36.memory-rest`).**
  Requires: `F36.coordinator`
  ```sh
  for N in $(control-agent-server api GET "/api/projects/$P2/memory" --expect 200 --field files | jq -r '.[].name'); do
    control-agent-server api DELETE "/api/projects/$P2/memory/$N" --expect 200 --check deleted eq true
  done
  control-agent-server api GET "/api/projects/$P2/memory" --expect 200 --check files len-eq 0
  REV=$(control-agent-server api PUT "/api/projects/$P2/memory/MEMORY.md" --json '{"content": "- The user prefers short answers.\n"}' \
    --expect 200 --check revision matches '^[0-9a-f]{16}$' --field revision)
  control-agent-server api PUT "/api/projects/$P2/memory/MEMORY.md" --json '{"content": "clobber"}' --expect 409 \
    --check detail.current.revision eq "$REV" --save F36.memory-rest/stale-create
  control-agent-server api PUT "/api/projects/$P2/memory/deploy.md" --json '{"content": "Deploy from main."}' --expect 200
  control-agent-server api GET "/api/projects/$P2/memory" --check files.0.name eq MEMORY.md --check files.1.name eq deploy.md --save F36.memory-rest/list
  control-agent-server api PUT "/api/projects/$P2/memory/Notes.md" --json '{"content": "x"}' --expect 400
  control-agent-server api DELETE "/api/projects/$P2/memory/deploy.md" --query base_revision=0000000000000000 --expect 409
  control-agent-server api DELETE "/api/projects/$P2/memory/deploy.md" --expect 200 --check deleted eq true
  control-agent-server api GET "/api/projects/$P2/memory/MEMORY.md" --check revision eq "$REV" --check content contains 'short answers'
  ```
  The coordinator may already have written to the project's memory on its
  own (earlier bullets give it turns), so the recipe first deletes what is
  there. The index is created, a blind overwrite is refused with the current
  file, and a topic file is added, listed after the index and deleted.
- **Model-backed: an agent keeps project memory (`F36.memory-tool`).**
  Requires: `F36.memory-rest`
  Needs: `llm`
  ```sh
  MEM=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-mem", "task": "Read the project memory with read_project_memory, then add the line - Deploys go through staging. to MEMORY.md with update_project_memory, keeping the existing line. Then finish.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation wait "$MEM" --until finished --timeout 300
  control-agent-server api GET "/api/projects/$P2/memory/MEMORY.md" --check content contains 'short answers' --check content contains 'staging' \
    --save F36.memory-tool/memory
  MEMCHILD=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-mem-child", "task": "Reply with the single word ok and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --field conversation_id)
  control-agent-server conversation events "$MEMCHILD" --kinds SystemPromptEvent \
    --contains 'Project memory: MEMORY.md as it was when this conversation started' --expect-count 1 \
    --save F36.memory-tool/child
  control-agent-server conversation events "$MEMCHILD" --kinds SystemPromptEvent --contains 'Deploys go through staging.' --expect-count 1
  control-agent-server conversation wait "$MEMCHILD" --until finished --timeout 300
  control-agent-server api GET "/api/conversations/$CAT/events/search" --query kind=MessageEvent --query 'body=Update from' \
    --query sort_order=TIMESTAMP_DESC --check items contains "conversation:${MEM//-/}" \
    --check items contains "conversation:${MEMCHILD//-/}" --until-ok 120
  control-agent-server conversation wait "$CAT" --until idle,finished --timeout 300
  ```
  The member merges its line into the index it read, and the next
  conversation the coordinator starts sees the index, with that line, in the
  `<PROJECT>` block of its system prompt. Both members' updates reach the
  coordinator, and the recipe waits for its run on them, so a later restart
  does not leave an update unanswered.
- **Restart (`F36.persist-restart`).**
  Requires: `F36.create`, `F36.coordinator`
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/projects --check projects len-eq 2 --check projects.1.id eq "$P2" --save F36.persist-restart/list
  control-agent-server api GET "/api/projects/$P2/conversations" --check coordinator.title eq qa-f36-cat --save F36.persist-restart/conversations
  ```
  Records and membership are read back from disk.
- **Projects turned off and on (`F36.flag-off`).**
  Requires: `F36.create`, `F36.coordinator`, `F36.memory-rest`
  ```sh
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": false}' --expect 200 --check enabled eq false
  control-agent-server api GET /api/projects --expect 404 --check detail eq 'Projects are turned off' --save F36.flag-off/list
  control-agent-server api GET "/api/projects/$P2" --expect 404 --check detail eq 'Projects are turned off'
  control-agent-server api GET "/api/projects/$P2/conversations" --expect 404 --check detail eq 'Projects are turned off'
  control-agent-server api GET "/api/projects/$P2/memory/MEMORY.md" --expect 404 --check detail eq 'Projects are turned off'
  control-agent-server api GET "/api/projects/$P2/artifacts" --expect 404 --check detail eq 'Projects are turned off'
  control-agent-server api POST /api/projects --json '{"name": "qa-f36-while-off"}' --expect 404 --save F36.flag-off/create
  control-agent-server state cat "$PJ" --check projects len-eq 2 --not-contains qa-f36-while-off
  control-agent-server api GET "/api/conversations/$CAT" --check tags.project eq "$P2"
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": true}' --expect 200 --check enabled eq true
  control-agent-server api GET /api/projects --expect 200 --check projects len-eq 2 --check projects.1.id eq "$P2" --save F36.flag-off/on-again
  control-agent-server api GET "/api/projects/$P2/conversations" --expect 200 --check coordinator.title eq qa-f36-cat
  control-agent-server api GET "/api/projects/$P2/memory/MEMORY.md" --expect 200 --check content contains 'short answers'
  ```
  Both switches take effect on the next request, with no restart. While off
  every route, a create included, is 404 and nothing is written; the
  projects, their memory and the coordinator's `project` tag are kept and
  served again once it is on.
- **The host's switch wins (`F36.runtime-env`).** A Docker runtime's
  server gets the user's switches from its host in
  `OH_RUNTIME_EXPERIMENTAL_FEATURES`; a restart with `--env` stands in for
  that host.
  Requires: `F36.flag-off`
  ```sh
  control-agent-server restart --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES={"projects": false}'
  control-agent-server api GET /api/settings/experimental --check 5.key eq projects --check 5.enabled eq true
  control-agent-server api GET /api/projects --expect 404 --check detail eq 'Projects are turned off' --save F36.runtime-env/host-off
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": false}' --expect 200
  control-agent-server restart --reset-config --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES={"projects": true}'
  control-agent-server api GET /api/settings/experimental --check 5.key eq projects --check 5.enabled eq false
  control-agent-server api GET /api/projects --expect 200 --check projects len-eq 2 --save F36.runtime-env/host-on
  control-agent-server restart --reset-config --env 'OH_RUNTIME_EXPERIMENTAL_FEATURES=qa-not-json'
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": true}' --expect 200
  control-agent-server api GET /api/projects --expect 404 --check detail eq 'Projects are turned off' --save F36.runtime-env/host-unparsable
  control-agent-server restart --reset-config
  control-agent-server api GET /api/projects --expect 200 --check projects len-eq 2
  ```
  With the variable set, the routes follow the host's value and ignore the
  stored switch, which `GET /api/settings/experimental` still lists (it
  shows the stored switch, not the host's). Without it, the stored switch
  (on) applies again.
- **Delete a project (`F36.delete`).**
  Requires: `F36.create`, `F36.coordinator`
  ```sh
  P3=$(control-agent-server api POST /api/projects \
    --json "{\"name\": \"qa-f36-gone\", \"coordinator\": {\"working_dir\": \"$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f36-cat\", \"title\": \"qa-f36-gone-cat\", \"tags\": {\"qa\": \"f36\"}}}" \
    --expect 201 --field id)
  GONE=$(control-agent-server api GET "/api/projects/$P3" --field coordinator_conversation_id)
  control-agent-server api DELETE "/api/projects/$P3" --expect 200 --check deleted eq true --save F36.delete/delete
  control-agent-server api GET "/api/projects/$P3" --expect 404
  control-agent-server api GET "/api/projects/$P3/conversations" --expect 404
  control-agent-server api GET "/api/conversations/$GONE" --check tags.project missing --check tags.qa eq f36
  control-agent-server state cat "$PJ" --not-contains "$P3" --check projects len-eq 2
  ```
  The record is gone; its coordinator stays, without the project's tags.
- **Model-backed: start and list (`F36.tool-start-and-list`).**
  Requires: `F36.coordinator`
  Needs: `llm`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Start one child conversation titled qa-f36-child with isolation shared whose task is: reply with the single word done and finish. Then call list_workers once and tell me what you see. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ActionEvent --contains start_child_conversation --expect-min 1
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains qa-f36-child --expect-min 2 --save F36.tool-start-and-list/events
  CHILD=$(control-agent-server api GET "/api/projects/$P2/conversations" --field conversations \
    | jq -r '.[] | select(.title == "qa-f36-child") | .conversation_id')
  control-agent-server api GET "/api/projects/$P2/conversations" --check . contains qa-f36-child
  control-agent-server conversation wait "$CHILD" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$CHILD" --check tags.project eq "$P2" \
    --check parent_conversation_id eq "$(control-agent-server api GET "/api/conversations/$CAT" --field id)"
  control-agent-server conversation events "$CHILD" --kinds SystemPromptEvent --contains 'the coordinator of this project. Your report goes to it.' --expect-count 1
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 1
  ```
  The child joins the coordinator's project; the list tool's observation
  names it. Needs `jq`. Its system prompt says who started it, and
  its task message records the coordinator as sender.
- **Model-backed: the coordinator hears back (`F36.wake`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  for _ in $(seq 60); do
    control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation:$(printf %s "$CHILD" | tr -d -))" --expect-min 1 >/dev/null 2>&1 && break
    sleep 2
  done
  CHILD_HEX=$(printf %s "$CHILD" | tr -d -)
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation:$CHILD_HEX), a conversation in your project" \
    --expect-count 1 --save F36.wake/update
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "\"sender\": \"conversation:$CHILD_HEX\"" --expect-count 1
  control-agent-server conversation wait "$CAT" --until finished --timeout 300
  control-agent-server api GET "/api/conversations/$CAT" --check tags.turn matches '^(finished|asking)$' --check tags.resolved missing \
    --check tags.resolvenote missing --until-ok 30 --save F36.wake/coordinator-tags
  ```
  When the child finished, its final message reached the coordinator as one
  update (sender: the child), and the coordinator ran on it. Its own turn
  is recorded in `turn` but never resolved.
- **Model-backed: read a conversation (`F36.read`).**
  Requires: `F36.wake`
  Needs: `llm`
  ```sh
  control-agent-server api GET "/api/projects/$P2/conversations/$CHILD/messages" --expect 200 \
    --check messages.0.source eq user --check messages.0.sender matches '^conversation:' --save F36.read/rest
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use read_worker on the qa-f36-child conversation and tell me its last message. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'oldest first' --expect-min 1 --save F36.read/events
  ```
  The transcript lists the coordinator's task message first; the tool reads it.
- **Model-backed: message a conversation (`F36.tool-message`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use message_worker to ask the qa-f36-child conversation to reply with the single word again. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'Message saved to conversation' --expect-min 1 --save F36.tool-message/events
  control-agent-server conversation wait "$CHILD" --until finished --timeout 180
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"source": "user"' --expect-count 2
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains 'the coordinator of your project, not by the user' --expect-count 2
  control-agent-server conversation events "$CHILD" --kinds MessageEvent --contains '"sender": "conversation:' --expect-count 2
  control-agent-server conversation events "$CHILD" --kinds ActionEvent,MessageEvent --contains '"source": "agent"' --expect-min 2
  ```
  The child receives a second user message, marked as the coordinator's like
  its brief, and finishes a second run.
- **Model-backed: mentions (`F36.mention`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  CHILD_HEX=$(printf %s "$CHILD" | tr -d -)
  control-agent-server conversation events "$CAT" --kinds SystemPromptEvent \
    --contains 'Name a conversation as a mention' --expect-count 1 --save F36.mention/guidance
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains "[qa-f36-child](conversation:$CHILD_HEX)" --expect-min 1 \
    --save F36.mention/list
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Which conversation did you start for the qa-f36-child task? Answer in one sentence that names it for me. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ActionEvent,MessageEvent --show 5 --full --save F36.mention/answer \
    | jq -r '[.events[] | select(.source == "agent")][-1] | (.llm_message.content[0].text // .action.message // "")' \
    | grep -Fi -e "](conversation:$CHILD_HEX)" -e "](conversation:$CHILD)"
  ```
  The list observation names the child as a mention, and the coordinator's
  last message, its answer, carries the same link although the prompt does not
  say how to write it (the routing guidance does). The recipe reads the last
  agent event among the last five, since the child's update can land right
  after the answer. Canvas accepts the target
  UUID with or without dashes, case-insensitively. Needs `jq`.
- **Model-backed: no approval by message (`F36.tool-message-refuses-confirmation`).**
  Requires: `F36.attention`, `F36.tool-start-and-list`
  Needs: `llm`, `tmux`
  ```sh
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Use message_worker to tell the qa-f36-asks conversation: go ahead. Then stop.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'would approve that action' --expect-min 0 \
    --save F36.tool-message-refuses-confirmation/events
  control-agent-server api GET "/api/conversations/$ASKS" --check execution_status eq waiting_for_confirmation
  control-agent-server conversation events "$ASKS" --kinds MessageEvent --contains '"source": "user"' --expect-count 2
  ```
  The conversation keeps waiting for the user and has only its two earlier
  messages. The coordinator either calls message_worker, which refuses with
  "would approve that action" (saved above), or declines on its own because
  its prompt says only the user approves; both keep the approval with the user.
- **Model-backed: a coordinator and a worker restore (`F36.restore`).**
  Requires: `F36.tool-start-and-list`
  Needs: `llm`
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$CAT" --check agent.system_prompt_filename eq system_prompt_project_coordinator.j2 \
    --check agent.agent_context.project_context contains "You are this project's coordinator" \
    --check agent.tools contains message_worker --save F36.restore/coordinator
  control-agent-server api GET "/api/conversations/$CHILD" --check agent.system_prompt_filename eq system_prompt_project_worker.j2 \
    --check agent.agent_context.project_context contains 'the coordinator of this project' \
    --check agent.tools contains resolve_conversation --check agent.tools not-contains message_worker --save F36.restore/worker
  BEFORE=$(control-agent-server conversation events "$CAT" --kinds ActionEvent --contains list_workers --expect-min 0 | jq .matching)
  control-agent-server conversation send "$CAT" --wait --timeout 300 --text 'Call list_workers once and tell me how many conversations it lists. Do nothing else.'
  control-agent-server conversation events "$CAT" --kinds ActionEvent --contains list_workers --expect-min $((BEFORE + 1)) --save F36.restore/coordinator-acts
  control-agent-server conversation send "$CHILD" --wait --timeout 300 --text 'Call list_artifacts once and tell me what it says. Do nothing else.'
  control-agent-server conversation events "$CHILD" --kinds ActionEvent --contains list_artifacts --expect-min 1 --save F36.restore/worker-acts
  control-agent-server conversation events "$CAT" --kinds SystemPromptEvent --expect-count 1
  ```
  After a restart both come back as main restores any conversation, from the
  agent saved with it: the coordinator and worker keep their prompt, their
  `<PROJECT>` block and their tools, and each uses a project tool on its next
  turn. There is no Projects-specific restore code. Needs `jq`.
- **Model-backed: project tools while Projects is off (`F36.flag-off-tools`).**
  Requires: `F36.restore`
  Needs: `llm`
  ```sh
  control-agent-server conversation wait "$CAT" --until idle,finished --timeout 300
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": false}' --expect 200
  control-agent-server conversation send "$CAT" --wait --timeout 300 \
    --text 'This is the user, writing to you directly (not a copy from another conversation): call your list_workers tool now, exactly once, even if you called it before, and tell me what it returned.'
  control-agent-server conversation events "$CAT" --kinds ObservationEvent --contains 'Projects is turned off' --expect-min 1 \
    --save F36.flag-off-tools/refused
  OFFCHILD=$(control-agent-server api POST "/api/conversations/$CAT/children" \
    --json '{"title": "qa-f36-off-child", "task": "Reply with the single word ok and finish. Do not use tools.", "isolation": "shared"}' \
    --expect 201 --check parent_conversation_id eq "$CAT" --field conversation_id)
  control-agent-server conversation wait "$OFFCHILD" --until finished --timeout 180
  control-agent-server api GET "/api/conversations/$OFFCHILD" --check tags.project eq "$P2" \
    --save F36.flag-off-tools/child
  OFF_HEX=$(printf %s "$OFFCHILD" | tr -d -)
  control-agent-server conversation events "$CAT" --kinds MessageEvent --contains "conversation:$OFF_HEX" --expect-count 0
  control-agent-server api PATCH /api/settings/experimental/projects --json '{"enabled": true}' --expect 200
  control-agent-server api GET "/api/projects/$P2/conversations" --check conversations contains qa-f36-off-child --save F36.flag-off-tools/board
  ```
  The coordinator keeps its tools, and `list_workers` answers that Projects
  is turned off. Its child starts as any child does, copying its parent's
  tags (`project` included), and sends the coordinator no update; it joins the board when Projects is on again.
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

- Membership is the `project` tag, but a client's `PATCH /api/conversations/{id}`
  cannot change it (nor `turn`, `ask`, `resolved`, `resolvenote`): a `tags` body that
  omits `project` keeps the conversation in its project. Use the project
  routes for membership and resolving.
- Only a project's coordinator starts conversations in it, so every member's
  work is the coordinator's to follow. A client cannot start or fork a
  conversation with `project` (or another server-owned tag), and no route adds
  an existing conversation; making a conversation the coordinator is the one
  way an existing conversation joins.
- These guards hold only while `projects` is on. With it off, a client can
  start a conversation tagged `project=<existing project>` and `PATCH`
  `project`, `prs`, `resolved` and the other board tags onto any
  conversation; once `projects` is back on, those conversations are members
  with that board state (a faked `resolved=user` and `pull_requests`), and a
  client can no longer remove the tags. Whether that is intended needs a
  product decision
  ([#7054](https://github.com/OpenHands/software-agent-sdk/issues/7054), open).
- Each turn the coordinator starts in a member (its task, its messages) wakes
  the coordinator when it ends, so the coordinator runs after every member
  recipe here. Wait for it (`conversation wait "$CAT" --until idle,finished`)
  before checking its status.
- A graceful restart while the coordinator runs on a member's update pauses
  that run, as for any running conversation (`F05.restart-running-graceful`),
  and nothing runs it again: the coordinator stays `paused` with the update
  unanswered, because startup catch-up counts a saved update as delivered
  ([#7058](https://github.com/OpenHands/software-agent-sdk/issues/7058), open).
  Recipes wait for the coordinator's run before a restart.
- A question for the user (`turn=asking`) stays open until the user writes in
  the conversation, or answers the coordinator's `ask_user` card made for it
  (`for_conversation`), which passes their answer on. Other messages from the
  coordinator keep it open, and the member's `resolve_conversation` is 409.
- `attention` comes from `execution_status`, refined by the `turn` and
  `resolved` tags. `idle` means nothing runs, not that the task
  succeeded. `turn` is the judge's: `asking` when it says the final reply
  waits on the user (or the turn ended on an `ask_user` card), else
  `finished`, which is also shown while the judge runs. The tags are written just
  after the run's final state update, so poll (`--until-ok`) rather than read
  once. There is no per-conversation "ask" text or task progress yet.
- A `resolved` conversation is listed as `resolved` unless it is running
  (`working`) or blocked on the user (`needs_you`).
- `automation_id` comes from the `automationid` tag that OpenHands
  automations set; children of an automation's conversation copy it, so they group
  with that automation. An automation's conversations do not join projects.
- Artifacts are copies: saving the same name again replaces the file.
  Deleting a project deletes its artifacts.
- A message to a project's conversation runs it as any message does: on a
  conversation waiting for confirmation, that run approves the pending
  actions, as on main. `message_worker` refuses such a conversation, and a
  coordinator update to one is saved without a run. Delivery to a running conversation waits for its state lock,
  which a long tool call holds, so the tool waits up to 300 seconds and, on a
  timeout, tells the agent not to resend.
- The tools read the project from the caller's tags on every call, so adding
  or removing the coordinator from a project takes effect on its next call.
  They are registered only by the agent-server (`tool_router.py`), like
  `start_child_conversation`.
- The projects store is a process-wide singleton like the workspaces store
  (F17): changing `OH_PERSISTENCE_DIR` needs a restart.
