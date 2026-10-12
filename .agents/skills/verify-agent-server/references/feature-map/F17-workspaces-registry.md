# Workspace registry and parents

The server keeps the user's saved workspaces (directories the Agent Canvas
workspace picker offers) and workspace parents (folders whose subdirectories
the picker offers as candidate workspaces), so every client of one server sees
the same list instead of each browser keeping its own. Both lists live in one
JSON document, `workspaces.json` in the persistence directory, rewritten
atomically under a file lock. Adding is idempotent by `path`, deleting is by
exact `path` (404 when nothing matched), and the wire format is camelCase
(`workspaceParents`, `parentPath`). A file the server cannot parse, or one
written by a newer schema, is never overwritten: every route answers 409. The
registry emits no events and no webhooks.

Source: `openhands-agent-server/openhands/agent_server/workspaces_router.py`, `openhands-agent-server/openhands/agent_server/persistence/models.py`, `openhands-agent-server/openhands/agent_server/persistence/store.py`, `openhands-sdk/openhands/sdk/utils/path.py`, `clients/typescript/src/client/workspaces-client.ts`

Needs: `git`, `node`

Routes: `GET /api/workspaces`, `POST /api/workspaces`, `DELETE /api/workspaces`,
`POST /api/workspaces/parents`, `DELETE /api/workspaces/parents`

## Sub-features

- `F17.list-empty`: on a fresh persistence directory `GET /api/workspaces` returns `{"workspaces": [], "workspaceParents": []}` and creates no `workspaces.json`.
- `F17.noop-delete-no-file`: on a fresh persistence directory, deleting a path from either list answers 404 and creates no `workspaces.json`, like a read (known bug: the refused delete writes an empty registry file).
- `F17.add-workspaces`: `POST /api/workspaces` registers a git repository and a plain directory in request order and answers the whole document; `GET` and `workspaces.json` (mode `0600`, `schema_version` 1) show the same entries.
- `F17.wire-format`: requests take `parentPath` or `parent_path`; responses and the file use `parentPath` and `workspaceParents`, an unset `parentPath` is omitted rather than `null`, and unknown item fields are dropped.
- `F17.idempotent-add`: re-posting a registered path, or the same path twice in one body, answers 200 and keeps exactly one entry, the first, with its original `id` and `name`.
- `F17.delete-workspace`: `DELETE /api/workspaces?path=` answers `{"deleted": true}` and removes only that entry, from the list and from the file, keeping the others in order.
- `F17.delete-not-found`: deleting a path that is not a saved workspace (a deleted one, or a near miss with a trailing slash) is 404 `Workspace not found` and changes nothing; a delete without `path` is 422.
- `F17.noop-write-untouched`: a write that changes nothing (re-posting saved workspaces, deleting an unknown workspace or parent) leaves `workspaces.json` as it was, same inode and modification time (known bug: every write route rewrites the file).
- `F17.paths-opaque`: paths are stored as given, without existence checks or normalization: a missing directory and a relative path are accepted, `/x` and `/x/` are two entries that delete separately, and a path with spaces, `&`, `?`, `#` or non-ASCII letters round-trips through an encoded delete query.
- `F17.validation`: a body without `workspaces` or `parents`, a non-list, an item with a missing or empty `id`, `name` or `path`, or a field over its limit (name 256, id, path and `parentPath` 4096) is 422 and stores nothing, not even the valid items of the same body; values at the limits are accepted.
- `F17.add-parents`: `POST /api/workspaces/parents` registers parents, deduplicated by `path` like workspaces, answers the whole document and leaves `workspaces` untouched.
- `F17.parent-children`: registering a parent registers none of its subdirectories; the picker lists them with `GET /api/file/search_subdirs`, and a child appears in `workspaces` only once it is posted with `parentPath` set to the parent.
- `F17.delete-parent`: `DELETE /api/workspaces/parents?path=` answers `{"deleted": true}` and removes only the parent: its child workspaces stay listed with their `parentPath`; a second delete is 404 `Workspace parent not found`, a missing `path` 422.
- `F17.lists-independent`: one path can be both a workspace and a parent; each delete route looks only at its own list (404 for a path that is only in the other) and deleting from one leaves the other.
- `F17.persist-restart`: both lists, in order and with every field, read back identically after a server restart.
- `F17.auth-required`: with a session key configured, all five routes answer 401 to a missing or wrong `X-Session-API-Key`, before validating the request, and change nothing.
- `F17.concurrent-adds`: parallel `POST`s of different workspaces and parents to one server all land, and parallel `DELETE`s of all of them all apply.
- `F17.shared-dir`: two servers on one persistence directory share the registry: each sees the other's writes on its next request (nothing is cached), and parallel adds and deletes split across both servers all land (the `.workspaces.lock` file lock serializes them across processes).
- `F17.ts-client`: the TypeScript `WorkspacesClient` lists, adds and deletes workspaces and parents through these routes, and its delete of an unknown path rejects with the server's 404.
- `F17.corrupted-file`: with an unparsable or empty `workspaces.json`, all five routes answer 409 `Workspaces file is corrupted` and leave the file byte for byte; restoring the file restores the lists.
- `F17.future-schema`: a valid document with a `schema_version` newer than 1, or one that is not an integer, or with a wrongly typed list, is 409 on reads and writes and is not overwritten; the same kind of hand-written document without `schema_version` is read, and the next write stamps `schema_version` 1.
- `F17.io-error`: when the file cannot be read (a directory in its place), every route answers 500 with `exception` naming the failed step (`Failed to read workspaces`, `Failed to save workspaces`, `Failed to update workspaces`), writes nothing, and the registry works again once the file is back.
- `F17.noop-write-readonly`: on a persistence directory the server cannot write, `GET` answers 200 and a real add 500 `Failed to save workspaces`, while a write that changes nothing keeps its normal answer: 404 for an unknown path, 200 for a re-post (known bug: they are 500 too).
- `F17.persistence-location`: without `OH_PERSISTENCE_DIR` the registry lives in `<conversations_path>/../.openhands/workspaces.json`, separate from the one under `OH_PERSISTENCE_DIR`.

## How to get to it (agent POV)

- REST: `GET /api/workspaces` (no parameters) returns
  `{"workspaces": [{id, name, path, parentPath?}], "workspaceParents": [{id, name, path}]}`.
- REST: `POST /api/workspaces` with `{"workspaces": [{id, name, path, parentPath?}]}`
  and `POST /api/workspaces/parents` with `{"parents": [{id, name, path}]}`
  append and answer the whole document (same shape as `GET`).
- REST: `DELETE /api/workspaces?path=...` and
  `DELETE /api/workspaces/parents?path=...` remove one entry by exact path and
  answer `{"deleted": true}`.
- TypeScript client: `WorkspacesClient` (`clients/typescript/src/client/workspaces-client.ts`):
  `listWorkspaces()`, `addWorkspaces(items)`, `deleteWorkspace(path)`,
  `addWorkspaceParents(items)`, `deleteWorkspaceParent(path)`. Each call first
  reads `/server_info` and refuses servers older than 1.23.0. Driven by
  `F17.ts-client` (a `node` program on `clients/typescript/dist`).
- SDK: no Python consumer; `RemoteWorkspace` does not call these routes.
- Configuration: `OH_PERSISTENCE_DIR` chooses the directory of
  `workspaces.json`; without it the file sits next to the conversations
  directory (`F17.persistence-location`). Servers pointed at one directory
  share one registry (`F17.shared-dir`).
- Agent Canvas: the workspace picker reads and edits these lists and offers
  each parent's subdirectories from `GET /api/file/search_subdirs` (context
  only; that route belongs to the files family and is used here as a second
  view).
- Recipes: every bullet calls the routes with `api`; `state cat` reads the
  stored file, `stat` stamps it to catch a rewrite with identical content,
  and plain shell rewrites or replaces it (or `chattr +i` freezes its
  directory) to reach the corrupted, future-schema, unreadable and
  read-only states that no API call can produce.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new`), `doctor` is ok, and
  nothing has touched `/api/workspaces` on it yet (`F17.list-empty` needs a
  fresh persistence directory, and so does `F17.noop-delete-no-file`). No
  LLM profile is needed. `git` (for the repository fixture) and `node` (for
  `F17.ts-client`) are on `PATH`; the TypeScript client is built if
  `clients/typescript/dist` is missing (`npm ci` then needs the npm
  registry). `F17.noop-write-readonly` runs `chattr +i`, so it needs root
  and a filesystem with the immutable attribute.
- The block below creates a git repository (`REPO`), a plain directory
  (`PLAIN`) and a parent directory with two subdirectories (`PARENT`), writes
  the request bodies that carry those run-specific paths, and the TypeScript
  program. `WS` is the stored file under the run's `OH_PERSISTENCE_DIR`.
- Bullets run in order and build on each other's lists; each states the list
  it leaves. `F17.persist-restart` and `F17.persistence-location` restart the
  run; the last one restores the launch environment. `F17.shared-dir`
  launches a second server on this run's persistence directory and stops it.

  ```sh
  F17="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f17"
  PLAIN="$F17/plain"
  PARENT="$F17/parent"
  WS="$AGENT_SERVER_VERIFY_RUN/home/.openhands/workspaces.json"
  mkdir -p "$PLAIN" "$PARENT/alpha" "$PARENT/beta"
  REPO=$(control-agent-server fixture git-repo --name qa-f17-repo --print-path)
  test -d "$REPO/.git"
  cat > "$F17/add.json" <<EOF
  {"workspaces": [
    {"id": "$REPO", "name": "qa-f17-repo", "path": "$REPO"},
    {"id": "$PLAIN", "name": "qa-f17-plain", "path": "$PLAIN"}
  ]}
  EOF
  cat > "$F17/readd.json" <<EOF
  {"workspaces": [
    {"id": "qa-f17-other-id", "name": "qa-f17-renamed", "path": "$REPO"},
    {"id": "/qa-f17/dup", "name": "qa-f17-dup-first", "path": "/qa-f17/dup"},
    {"id": "qa-f17-dup-second", "name": "qa-f17-dup-second", "path": "/qa-f17/dup"}
  ]}
  EOF
  cat > "$F17/parents.json" <<EOF
  {"parents": [
    {"id": "$PARENT", "name": "qa-f17-parent", "path": "$PARENT"},
    {"id": "qa-f17-parent-dup", "name": "qa-f17-parent-dup", "path": "$PARENT"},
    {"id": "/qa-f17/other-parent", "name": "qa-f17-other-parent", "path": "/qa-f17/other-parent"}
  ]}
  EOF
  cat > "$F17/child.json" <<EOF
  {"workspaces": [{"id": "$PARENT/alpha", "name": "alpha", "path": "$PARENT/alpha", "parentPath": "$PARENT"}]}
  EOF
  cat > "$F17/plain-parent.json" <<EOF
  {"parents": [{"id": "$PLAIN", "name": "qa-f17-plain", "path": "$PLAIN"}]}
  EOF
  cat > "$F17/ts_workspaces.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { WorkspacesClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const client = new WorkspacesClient({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY });
  const before = await client.listWorkspaces();
  const item = { id: '/qa-f17/ts', name: 'qa-f17-ts', path: '/qa-f17/ts', parentPath: '/qa-f17/ts-parent' };
  const added = await client.addWorkspaces([item]);
  assert.equal(added.workspaces.length, before.workspaces.length + 1);
  assert.deepEqual(added.workspaces.at(-1), item);
  const again = await client.addWorkspaces([{ ...item, name: 'qa-f17-ts-renamed' }]);
  assert.deepEqual(again.workspaces.at(-1), item);
  const parent = { id: '/qa-f17/ts-parent', name: 'qa-f17-ts-parent', path: '/qa-f17/ts-parent' };
  const withParent = await client.addWorkspaceParents([parent]);
  assert.deepEqual(withParent.workspaceParents.at(-1), parent);
  assert.equal(withParent.workspaces.length, before.workspaces.length + 1);
  assert.deepEqual(await client.deleteWorkspaceParent(parent.path), { deleted: true });
  assert.deepEqual(await client.deleteWorkspace(item.path), { deleted: true });
  await assert.rejects(client.deleteWorkspace(item.path), (err) => err.status === 404 && err.detail === 'Workspace not found');
  await assert.rejects(client.deleteWorkspaceParent(parent.path), (err) => err.status === 404 && err.detail === 'Workspace parent not found');
  assert.deepEqual(await client.listWorkspaces(), before);
  client.close();
  console.log('QA_F17_TS_OK');
  JS
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  ```

- **Empty registry (`F17.list-empty`).** List on a fresh persistence
  directory.
  ```sh
  control-agent-server api GET /api/workspaces --expect 200 \
    --check workspaces len-eq 0 --check workspaceParents len-eq 0 --save F17.list-empty/list
  control-agent-server state ls home/.openhands/workspaces.json --expect-count 0
  ```
  Both lists are empty and reading created no file.
- **A refused delete on a fresh directory (`F17.noop-delete-no-file`), known bug.**
  Delete an unknown path from each list while `workspaces.json` does not
  exist yet.
  ```sh
  control-agent-server state ls home/.openhands/workspaces.json --expect-count 0
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/never --expect 404 \
    --check detail eq 'Workspace not found' --save F17.noop-delete-no-file/delete
  control-agent-server api DELETE /api/workspaces/parents --query path=/qa-f17/never --expect 404 \
    --check detail eq 'Workspace parent not found'
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 0 --check workspaceParents len-eq 0
  control-agent-server state ls home/.openhands/workspaces.json --expect-count 0  # bug
  ```
  Expected: both deletes are 404 and, like the read before them, leave no
  file. Today the first 404 creates `workspaces.json` with two empty lists
  (`FileWorkspacesStore.update` saves whatever the route's `apply` returns,
  also when nothing matched), so the last `state ls` finds one file. The
  lists stay empty either way, and the next bullet's first add writes the
  file in both cases.
- **Register a repository and a plain directory (`F17.add-workspaces`).**
  ```sh
  control-agent-server api POST /api/workspaces --json-file "$F17/add.json" --expect 200 \
    --check workspaces len-eq 2 --check workspaces.0.path eq "$REPO" --check workspaces.1.path eq "$PLAIN" \
    --check workspaceParents len-eq 0 --save F17.add-workspaces/post
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 2 \
    --check workspaces.0.id eq "$REPO" --check workspaces.0.name eq qa-f17-repo \
    --check workspaces.1.id eq "$PLAIN" --check workspaces.1.name eq qa-f17-plain --save F17.add-workspaces/get
  control-agent-server state cat home/.openhands/workspaces.json --mode 600 --check schema_version eq 1 \
    --check workspaces len-eq 2 --check workspaces.0.path eq "$REPO" --check workspaces.1.path eq "$PLAIN"
  ```
  The response is the whole document; `GET` and the `0600` file hold the
  repository first and the plain directory second. List left: `REPO`, `PLAIN`.
- **Wire format (`F17.wire-format`).** Post one item with snake_case
  `parent_path`, one with `parentPath`, one with `parentPath: null` and an
  unknown field.
  ```sh
  control-agent-server api POST /api/workspaces --expect 200 --json '{"workspaces": [
      {"id": "/qa-f17/snake", "name": "qa-f17-snake", "path": "/qa-f17/snake", "parent_path": "/qa-f17"},
      {"id": "/qa-f17/camel", "name": "qa-f17-camel", "path": "/qa-f17/camel", "parentPath": "/qa-f17"},
      {"id": "/qa-f17/null", "name": "qa-f17-null", "path": "/qa-f17/null", "parentPath": null, "qa_f17_extra": "x"}]}' \
    --check workspaces len-eq 5 --check workspaces.2.parentPath eq /qa-f17 --check workspaces.3.parentPath eq /qa-f17 \
    --check workspaces.4 not-contains parentPath --check workspaces.4 not-contains qa_f17_extra \
    --check workspaces.0 not-contains parentPath --check . not-contains parent_path --save F17.wire-format/post
  control-agent-server api GET /api/workspaces --check workspaceParents len-eq 0 \
    --check workspaces.2.parentPath eq /qa-f17 --check workspaces.4 not-contains parentPath --save F17.wire-format/get
  control-agent-server state cat home/.openhands/workspaces.json --not-contains parent_path \
    --check workspaceParents len-eq 0 --check workspaces.2.parentPath eq /qa-f17 --check workspaces.4 not-contains parentPath
  ```
  Both spellings store `parentPath: "/qa-f17"`; the third item has no
  `parentPath` key and no `qa_f17_extra`, on the wire and on disk, where the
  parents list is stored as `workspaceParents`. List left: `REPO`, `PLAIN`,
  `snake`, `camel`, `null`.
- **Idempotent re-add (`F17.idempotent-add`).** Re-post the repository under
  another id and name, and one new path twice in one body; then post the same
  body again.
  ```sh
  control-agent-server api POST /api/workspaces --json-file "$F17/readd.json" --expect 200 \
    --check workspaces len-eq 6 --check workspaces.0.id eq "$REPO" --check workspaces.0.name eq qa-f17-repo \
    --check workspaces.5.id eq /qa-f17/dup --check workspaces.5.name eq qa-f17-dup-first --save F17.idempotent-add/first
  control-agent-server api POST /api/workspaces --json-file "$F17/readd.json" --expect 200 --check workspaces len-eq 6
  control-agent-server api GET /api/workspaces --check workspaces len-eq 6 \
    --check . not-contains qa-f17-renamed --check . not-contains qa-f17-dup-second --save F17.idempotent-add/get
  ```
  Both posts are 200 and the list grows by exactly one entry, `/qa-f17/dup`
  with the first id and name; the repository keeps its original id and name.
  List left: `REPO`, `PLAIN`, `snake`, `camel`, `null`, `dup`.
- **Delete a workspace (`F17.delete-workspace`).**
  ```sh
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/camel --expect 200 --check deleted eq true \
    --save F17.delete-workspace/delete
  control-agent-server api GET /api/workspaces --check workspaces len-eq 5 --check . not-contains /qa-f17/camel \
    --check workspaces.2.path eq /qa-f17/snake --check workspaces.3.path eq /qa-f17/null \
    --check workspaces.4.path eq /qa-f17/dup --save F17.delete-workspace/get
  control-agent-server state cat home/.openhands/workspaces.json --not-contains /qa-f17/camel --check workspaces len-eq 5
  ```
  The answer is `{"deleted": true}`; the entry is gone from the list and the
  file and the others keep their order. List left: `REPO`, `PLAIN`, `snake`,
  `null`, `dup`.
- **Unknown paths (`F17.delete-not-found`).** Delete the removed path again,
  a trailing-slash variant of a saved path, and no path at all.
  ```sh
  BEFORE=$(control-agent-server api GET /api/workspaces --field .)
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/camel --expect 404 \
    --check detail eq 'Workspace not found' --save F17.delete-not-found/deleted-twice
  control-agent-server api DELETE /api/workspaces --query "path=$REPO/" --expect 404 --check detail eq 'Workspace not found'
  control-agent-server api DELETE /api/workspaces --expect 422 --check detail.0.type eq missing \
    --check detail.0.loc contains path --save F17.delete-not-found/no-path
  control-agent-server api GET /api/workspaces --check . eq "$BEFORE" --save F17.delete-not-found/unchanged
  ```
  Both deletes are 404 `Workspace not found`, the bare delete is 422 naming
  the `path` query parameter, and the list reads back identical.
- **No-op writes leave the file alone (`F17.noop-write-untouched`), known bug.**
  Stamp the file (inode and nanosecond modification time), read, then
  re-post saved workspaces and delete unknown paths from both lists.
  ```sh
  STAMP=$(stat -c '%i %y' "$WS")
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 5
  test "$(stat -c '%i %y' "$WS")" = "$STAMP"
  control-agent-server api POST /api/workspaces --json-file "$F17/readd.json" --expect 200 --check workspaces len-eq 5 \
    --check workspaces.0.name eq qa-f17-repo --save F17.noop-write-untouched/readd
  test "$(stat -c '%i %y' "$WS")" = "$STAMP"  # bug
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/never --expect 404
  test "$(stat -c '%i %y' "$WS")" = "$STAMP"  # bug
  control-agent-server api DELETE /api/workspaces/parents --query path=/qa-f17/never --expect 404
  test "$(stat -c '%i %y' "$WS")" = "$STAMP"  # bug
  ```
  The read leaves the stamp, which shows the comparison can hold. Expected:
  the re-post (every path in `readd.json` is already saved: 200, still five
  entries, the repository keeps its name) and both 404 deletes leave it too.
  Today the re-post already replaces the file (a new inode from the
  temp-file rename and a new mtime), so the first marked `test` fails; the
  content stays the same, which is why only the stamp shows it. List left:
  `REPO`, `PLAIN`, `snake`, `null`, `dup`.
- **Paths are opaque strings (`F17.paths-opaque`).** Register a relative path,
  and a missing directory with and without a trailing slash.
  ```sh
  control-agent-server api GET /api/file/search_subdirs --query path=/qa-f17/missing --expect 404 --check detail eq 'Directory not found'
  control-agent-server api POST /api/workspaces --expect 200 --json '{"workspaces": [
      {"id": "qa-f17/relative", "name": "qa-f17-relative", "path": "qa-f17/relative"},
      {"id": "/qa-f17/missing/", "name": "qa-f17-missing-slash", "path": "/qa-f17/missing/"},
      {"id": "/qa-f17/missing", "name": "qa-f17-missing", "path": "/qa-f17/missing"}]}' \
    --check workspaces len-eq 8 --check workspaces.5.path eq qa-f17/relative \
    --check workspaces.6.path eq /qa-f17/missing/ --check workspaces.7.path eq /qa-f17/missing --save F17.paths-opaque/post
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/missing --expect 200 --check deleted eq true
  control-agent-server api GET /api/workspaces --check workspaces len-eq 7 --check workspaces.6.path eq /qa-f17/missing/ \
    --save F17.paths-opaque/after-delete
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/missing/ --expect 200
  control-agent-server api DELETE /api/workspaces --query path=qa-f17/relative --expect 200
  control-agent-server api POST /api/workspaces --expect 200 --check workspaces.5.path eq '/qa-f17/with space & ü?#frag' \
    --json '{"workspaces": [{"id": "/qa-f17/with space & ü?#frag", "name": "qa-f17-odd", "path": "/qa-f17/with space & ü?#frag"}]}'
  control-agent-server api DELETE /api/workspaces --query 'path=/qa-f17/with space & ü?#frag' --expect 200 --check deleted eq true \
    --save F17.paths-opaque/odd-characters
  control-agent-server api GET /api/workspaces --check workspaces len-eq 5 --check . not-contains qa-f17/relative \
    --check . not-contains qa-f17-odd
  ```
  The server's own directory listing says `/qa-f17/missing` does not exist,
  yet all three are stored as given; deleting `/qa-f17/missing` leaves
  `/qa-f17/missing/`. A path with a space, `&`, `?`, `#` and a non-ASCII
  letter is stored verbatim and deleted through a URL-encoded query (the CLI
  encodes `--query`; a client that does not would delete the wrong path or
  get 404). The bullet removes what it added. List left: `REPO`, `PLAIN`,
  `snake`, `null`, `dup`.
- **Validation (`F17.validation`).** Malformed bodies, a body mixing a valid
  and an invalid item, and the length limits.
  ```sh
  N256=$(printf 'n%.0s' $(seq 256))
  P4096="/$(printf 'p%.0s' $(seq 4095))"
  control-agent-server api POST /api/workspaces --json '{}' --expect 422 \
    --check detail.0.type eq missing --check detail.0.loc contains workspaces --save F17.validation/no-list
  control-agent-server api POST /api/workspaces --json '{"workspaces": {"id": "/qa-f17/v", "name": "v", "path": "/qa-f17/v"}}' \
    --expect 422 --check detail.0.type eq list_type
  control-agent-server api POST /api/workspaces --expect 422 --json '{"workspaces": [
      {"id": "/qa-f17/batch-valid", "name": "qa-f17-batch-valid", "path": "/qa-f17/batch-valid"},
      {"id": "", "name": "qa-f17-empty-id", "path": "/qa-f17/empty-id"}]}' \
    --check detail.0.type eq string_too_short --check detail.0.loc contains id --save F17.validation/mixed-batch
  control-agent-server api POST /api/workspaces --json '{"workspaces": [{"id": "/qa-f17/v", "path": "/qa-f17/v"}]}' \
    --expect 422 --check detail.0.type eq missing --check detail.0.loc contains name
  control-agent-server api POST /api/workspaces --json '{"workspaces": [{"id": "/qa-f17/v", "name": "", "path": "/qa-f17/v"}]}' \
    --expect 422 --check detail.0.loc contains name
  control-agent-server api POST /api/workspaces --json '{"workspaces": [{"id": "/qa-f17/v", "name": "v", "path": ""}]}' \
    --expect 422 --check detail.0.loc contains path
  control-agent-server api POST /api/workspaces/parents --json '{"workspaces": []}' --expect 422 --check detail.0.loc contains parents
  control-agent-server api POST /api/workspaces/parents --json '{"parents": [{"id": "/qa-f17/v", "name": "v"}]}' \
    --expect 422 --check detail.0.loc contains path
  control-agent-server api POST /api/workspaces --expect 422 --check detail.0.type eq string_too_long --check detail.0.loc contains name \
    --json "{\"workspaces\": [{\"id\": \"/qa-f17/v\", \"name\": \"${N256}n\", \"path\": \"/qa-f17/v\"}]}"
  control-agent-server api POST /api/workspaces --expect 422 --check detail.0.type eq string_too_long --check detail.0.loc contains path \
    --json "{\"workspaces\": [{\"id\": \"/qa-f17/v\", \"name\": \"v\", \"path\": \"${P4096}p\"}]}"
  control-agent-server api POST /api/workspaces --expect 422 --check detail.0.type eq string_too_long --check detail.0.loc contains id \
    --json "{\"workspaces\": [{\"id\": \"${P4096}p\", \"name\": \"v\", \"path\": \"/qa-f17/v\"}]}"
  control-agent-server api POST /api/workspaces --expect 422 --check detail.0.type eq string_too_long --check detail.0.loc contains parentPath \
    --json "{\"workspaces\": [{\"id\": \"/qa-f17/v\", \"name\": \"v\", \"path\": \"/qa-f17/v\", \"parentPath\": \"${P4096}p\"}]}"
  control-agent-server api POST /api/workspaces/parents --expect 422 --check detail.0.type eq string_too_long \
    --json "{\"parents\": [{\"id\": \"${P4096}p\", \"name\": \"v\", \"path\": \"/qa-f17/v\"}]}"
  control-agent-server api GET /api/workspaces --check workspaces len-eq 5 --check workspaceParents len-eq 0 \
    --check . not-contains /qa-f17/v --check . not-contains batch-valid --save F17.validation/unchanged
  control-agent-server api POST /api/workspaces --expect 200 --quiet --check workspaces len-eq 6 \
    --check workspaces.5.name eq "$N256" --check workspaces.5.path eq "$P4096" --check workspaces.5.parentPath eq "$P4096" \
    --json "{\"workspaces\": [{\"id\": \"$P4096\", \"name\": \"$N256\", \"path\": \"$P4096\", \"parentPath\": \"$P4096\"}]}"
  control-agent-server api DELETE /api/workspaces --query "path=$P4096" --expect 200 --check deleted eq true
  ```
  Every malformed body is 422 with the offending field in `detail[0].loc`;
  the mixed body stores neither item. A 256-character name with 4096-character
  id, path and `parentPath` is accepted (and then removed). List left:
  `REPO`, `PLAIN`, `snake`, `null`, `dup`.
- **Register parents (`F17.add-parents`).** Post a parent twice in one body
  plus another parent, then re-post the first under a new name.
  ```sh
  control-agent-server api POST /api/workspaces/parents --json-file "$F17/parents.json" --expect 200 \
    --check workspaceParents len-eq 2 --check workspaceParents.0.id eq "$PARENT" \
    --check workspaceParents.0.name eq qa-f17-parent --check workspaceParents.1.path eq /qa-f17/other-parent \
    --check workspaces len-eq 5 --save F17.add-parents/post
  control-agent-server api POST /api/workspaces/parents --expect 200 --check workspaceParents len-eq 2 \
    --check workspaceParents.0.name eq qa-f17-parent \
    --json "{\"parents\": [{\"id\": \"qa-f17-again\", \"name\": \"qa-f17-renamed\", \"path\": \"$PARENT\"}]}"
  control-agent-server api GET /api/workspaces --check workspaceParents len-eq 2 --check workspaces len-eq 5 \
    --check . not-contains qa-f17-parent-dup --save F17.add-parents/get
  control-agent-server state cat home/.openhands/workspaces.json --check workspaceParents len-eq 2 \
    --check workspaceParents.0.path eq "$PARENT" --check workspaces len-eq 5
  ```
  Two parents are stored, the first id and name win, and the workspaces list
  is untouched. Parents left: `PARENT`, `/qa-f17/other-parent`.
- **Children of a parent (`F17.parent-children`).** The parent's
  subdirectories are candidates, not workspaces, until one is posted.
  ```sh
  control-agent-server api GET /api/file/search_subdirs --query "path=$PARENT" --expect 200 \
    --check items len-eq 2 --check items.0.path eq "$PARENT/alpha" --check items.1.path eq "$PARENT/beta" \
    --save F17.parent-children/subdirs
  control-agent-server api GET /api/workspaces --check workspaces len-eq 5 --check . not-contains "$PARENT/alpha"
  control-agent-server api POST /api/workspaces --json-file "$F17/child.json" --expect 200 \
    --check workspaces len-eq 6 --check workspaces.5.path eq "$PARENT/alpha" --check workspaces.5.parentPath eq "$PARENT" \
    --save F17.parent-children/register-child
  control-agent-server api GET /api/workspaces --check workspaces.5.parentPath eq "$PARENT" --check . not-contains "$PARENT/beta"
  ```
  `search_subdirs` offers `alpha` and `beta`; neither is a workspace until
  `alpha` is posted with `parentPath` set to the parent, and `beta` stays a
  candidate. List left: `REPO`, `PLAIN`, `snake`, `null`, `dup`, `alpha`.
- **Delete a parent (`F17.delete-parent`).**
  ```sh
  control-agent-server api DELETE /api/workspaces/parents --query "path=$PARENT" --expect 200 --check deleted eq true \
    --save F17.delete-parent/delete
  control-agent-server api GET /api/workspaces --check workspaceParents len-eq 1 \
    --check workspaceParents.0.path eq /qa-f17/other-parent --check workspaces len-eq 6 \
    --check workspaces.5.parentPath eq "$PARENT" --save F17.delete-parent/after
  control-agent-server state cat home/.openhands/workspaces.json --check workspaceParents len-eq 1 \
    --check workspaces.5.parentPath eq "$PARENT"
  control-agent-server api DELETE /api/workspaces/parents --query "path=$PARENT" --expect 404 \
    --check detail eq 'Workspace parent not found' --save F17.delete-parent/again
  control-agent-server api DELETE /api/workspaces/parents --expect 422 --check detail.0.loc contains path
  ```
  The parent is gone from the list and the file; `alpha` is still a
  workspace whose `parentPath` names the removed parent. Parents left:
  `/qa-f17/other-parent`.
- **Two independent lists (`F17.lists-independent`).** Make the plain
  directory a parent too, then delete across lists.
  ```sh
  control-agent-server api POST /api/workspaces/parents --json-file "$F17/plain-parent.json" --expect 200 \
    --check workspaceParents len-eq 2 --check workspaceParents.1.path eq "$PLAIN" \
    --check workspaces len-eq 6 --check workspaces.1.path eq "$PLAIN"
  control-agent-server api DELETE /api/workspaces/parents --query "path=$REPO" --expect 404 --check detail eq 'Workspace parent not found'
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/other-parent --expect 404 --check detail eq 'Workspace not found'
  control-agent-server api DELETE /api/workspaces --query "path=$PLAIN" --expect 200 --check deleted eq true
  control-agent-server api GET /api/workspaces --check workspaces len-eq 5 --check workspaces not-contains "$PLAIN" \
    --check workspaceParents len-eq 2 --check workspaceParents.1.path eq "$PLAIN" --save F17.lists-independent/after
  ```
  A workspace path is 404 on the parents route and a parent path is 404 on
  the workspaces route; deleting `PLAIN` as a workspace leaves it as a parent.
  List left: `REPO`, `snake`, `null`, `dup`, `alpha`; parents
  `/qa-f17/other-parent`, `PLAIN`.
- **Persistence across a restart (`F17.persist-restart`).**
  ```sh
  BEFORE=$(control-agent-server api GET /api/workspaces --field .)
  control-agent-server restart
  control-agent-server api GET /api/workspaces --expect 200 --check . eq "$BEFORE" \
    --check workspaces.4.parentPath eq "$PARENT" --check workspaceParents.1.path eq "$PLAIN" --save F17.persist-restart/after
  ```
  The document after the restart is identical to the one before, including
  order and `parentPath`.
- **Session key required (`F17.auth-required`).** Every route without a key
  and with a wrong key, then the positive control.
  ```sh
  BEFORE=$(control-agent-server api GET /api/workspaces --field .)
  control-agent-server api GET /api/workspaces --auth none --expect 401 --check detail eq Unauthorized --save F17.auth-required/get-no-key
  control-agent-server api GET /api/workspaces --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api POST /api/workspaces --auth none --expect 401 \
    --json '{"workspaces": [{"id": "/qa-f17/unauth", "name": "qa-f17-unauth", "path": "/qa-f17/unauth"}]}' --save F17.auth-required/post-no-key
  control-agent-server api POST /api/workspaces --auth bad --expect 401 \
    --json '{"workspaces": [{"id": "/qa-f17/unauth", "name": "qa-f17-unauth", "path": "/qa-f17/unauth"}]}'
  control-agent-server api DELETE /api/workspaces --auth none --query "path=$REPO" --expect 401
  control-agent-server api DELETE /api/workspaces --auth bad --query "path=$REPO" --expect 401
  control-agent-server api POST /api/workspaces/parents --auth none --expect 401 \
    --json '{"parents": [{"id": "/qa-f17/unauth", "name": "qa-f17-unauth", "path": "/qa-f17/unauth"}]}'
  control-agent-server api POST /api/workspaces/parents --auth bad --expect 401 \
    --json '{"parents": [{"id": "/qa-f17/unauth", "name": "qa-f17-unauth", "path": "/qa-f17/unauth"}]}'
  control-agent-server api DELETE /api/workspaces/parents --auth none --query "path=$PLAIN" --expect 401
  control-agent-server api DELETE /api/workspaces/parents --auth bad --query "path=$PLAIN" --expect 401
  control-agent-server api POST /api/workspaces --auth none --json '{}' --expect 401 --check detail eq Unauthorized
  control-agent-server api DELETE /api/workspaces --auth none --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/workspaces --expect 200 --check . eq "$BEFORE" --save F17.auth-required/unchanged
  control-agent-server state cat home/.openhands/workspaces.json --not-contains /qa-f17/unauth
  ```
  All twelve calls are 401 `{"detail": "Unauthorized"}`, including an
  invalid body and a delete without `path` (the key is checked before the
  request is validated); with the run's key the document is unchanged and
  nothing was stored.
- **Concurrent writes (`F17.concurrent-adds`).** Eight workspaces and eight
  parents posted in parallel, then all sixteen deleted in parallel.
  ```sh
  PIDS=""
  for i in 1 2 3 4 5 6 7 8; do
    (control-agent-server api POST /api/workspaces --quiet --expect 200 \
      --json "{\"workspaces\": [{\"id\": \"/qa-f17/par-$i\", \"name\": \"qa-f17-par-$i\", \"path\": \"/qa-f17/par-$i\"}]}" > "$F17/par-ws-$i.json") &
    PIDS="$PIDS $!"
    (control-agent-server api POST /api/workspaces/parents --quiet --expect 200 \
      --json "{\"parents\": [{\"id\": \"/qa-f17/ppar-$i\", \"name\": \"qa-f17-ppar-$i\", \"path\": \"/qa-f17/ppar-$i\"}]}" > "$F17/par-parent-$i.json") &
    PIDS="$PIDS $!"
  done
  for p in $PIDS; do wait "$p"; done
  control-agent-server api GET /api/workspaces --check workspaces len-eq 13 --check workspaceParents len-eq 10 \
    --check workspaces contains /qa-f17/par-8 --check workspaceParents contains /qa-f17/ppar-8 --save F17.concurrent-adds/after-adds
  control-agent-server state cat home/.openhands/workspaces.json --check workspaces len-eq 13 --check workspaceParents len-eq 10
  PIDS=""
  for i in 1 2 3 4 5 6 7 8; do
    (control-agent-server api DELETE /api/workspaces --quiet --query "path=/qa-f17/par-$i" --expect 200 --check deleted eq true > "$F17/del-ws-$i.json") &
    PIDS="$PIDS $!"
    (control-agent-server api DELETE /api/workspaces/parents --quiet --query "path=/qa-f17/ppar-$i" --expect 200 --check deleted eq true > "$F17/del-parent-$i.json") &
    PIDS="$PIDS $!"
  done
  for p in $PIDS; do wait "$p"; done
  control-agent-server api GET /api/workspaces --check workspaces len-eq 5 --check workspaceParents len-eq 2 \
    --check . not-contains /qa-f17/par- --check . not-contains /qa-f17/ppar- --save F17.concurrent-adds/after-deletes
  ```
  Every parallel call succeeds (each `wait` returns its exit status); after
  the adds the lists hold 13 and 10 entries, after the deletes 5 and 2 again.
  One process runs these handlers one at a time (they are `async` and block
  the event loop while they read and write the file), so this bullet proves
  that parallel callers lose nothing on one server, not that the file lock
  works; `F17.shared-dir` puts a second process on the same file.
- **Two servers, one file (`F17.shared-dir`).** A second server whose
  `OH_PERSISTENCE_DIR` is this run's, then parallel writes split across both.
  ```sh
  SHARED=$(control-agent-server launch --new --name f17-shared --print-run \
    --env OH_PERSISTENCE_DIR="$AGENT_SERVER_VERIFY_RUN/home/.openhands")
  trap 'control-agent-server stop --run "$SHARED" >/dev/null' EXIT
  BEFORE=$(control-agent-server api GET /api/workspaces --field .)
  control-agent-server api GET /api/workspaces --run "$SHARED" --expect 200 --check . eq "$BEFORE"
  control-agent-server api POST /api/workspaces --run "$SHARED" --expect 200 --check workspaces len-eq 6 \
    --json '{"workspaces": [{"id": "/qa-f17/from-b", "name": "qa-f17-from-b", "path": "/qa-f17/from-b"}]}'
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 6 \
    --check workspaces.5.path eq /qa-f17/from-b --save F17.shared-dir/a-reads-b
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/from-b --expect 200 --check deleted eq true
  control-agent-server api GET /api/workspaces --run "$SHARED" --expect 200 --check . eq "$BEFORE"
  PIDS=""
  for i in 1 2 3 4 5 6 7 8; do
    (control-agent-server api POST /api/workspaces --quiet --expect 200 \
      --json "{\"workspaces\": [{\"id\": \"/qa-f17/via-a-$i\", \"name\": \"qa-f17-via-a-$i\", \"path\": \"/qa-f17/via-a-$i\"}]}" > "$F17/shared-a-$i.json") &
    PIDS="$PIDS $!"
    (control-agent-server api POST /api/workspaces --run "$SHARED" --quiet --expect 200 \
      --json "{\"workspaces\": [{\"id\": \"/qa-f17/via-b-$i\", \"name\": \"qa-f17-via-b-$i\", \"path\": \"/qa-f17/via-b-$i\"}]}" > "$F17/shared-b-$i.json") &
    PIDS="$PIDS $!"
  done
  for p in $PIDS; do wait "$p"; done
  control-agent-server api GET /api/workspaces --check workspaces len-eq 21 --check workspaces contains /qa-f17/via-a-8 \
    --check workspaces contains /qa-f17/via-b-8 --save F17.shared-dir/after-adds
  control-agent-server api GET /api/workspaces --run "$SHARED" --check workspaces len-eq 21
  PIDS=""
  for i in 1 2 3 4 5 6 7 8; do
    (control-agent-server api DELETE /api/workspaces --run "$SHARED" --quiet --query "path=/qa-f17/via-a-$i" --expect 200 > "$F17/shared-del-a-$i.json") &
    PIDS="$PIDS $!"
    (control-agent-server api DELETE /api/workspaces --quiet --query "path=/qa-f17/via-b-$i" --expect 200 > "$F17/shared-del-b-$i.json") &
    PIDS="$PIDS $!"
  done
  for p in $PIDS; do wait "$p"; done
  control-agent-server api GET /api/workspaces --expect 200 --check . eq "$BEFORE" --save F17.shared-dir/after-deletes
  control-agent-server stop --run "$SHARED"
  ```
  The second server lists this run's registry as is; an entry it adds is
  listed here on the next `GET`, and one deleted here is gone there. All
  sixteen parallel adds (eight through each server) land, each server's list
  has 21 entries, and deleting every entry through the other server returns
  the registry to `BEFORE`. A lost update would show as a missing entry after
  the adds or a leftover one after the deletes. The second server is stopped
  at the end of the bullet (and by the `trap` if a check fails).
- **TypeScript client (`F17.ts-client`).** Every `WorkspacesClient` method
  from the built client.
  ```sh
  BEFORE=$(control-agent-server api GET /api/workspaces --field .)
  control-agent-server exec --timeout 120 --expect-output QA_F17_TS_OK --save F17.ts-client/program -- node "$F17/ts_workspaces.mjs"
  control-agent-server api GET /api/workspaces --check . eq "$BEFORE" --check . not-contains /qa-f17/ts --save F17.ts-client/after
  ```
  The program prints `QA_F17_TS_OK`: the added workspace and parent come back
  verbatim, a re-add under another name keeps the first, both deletes answer
  `{deleted: true}`, the repeated deletes reject with an `HttpError` whose
  `status` is 404 and whose `detail` is the server's message, and the final
  list equals the first. The server-side read confirms it.
- **Corrupted file (`F17.corrupted-file`).** Back up the file, overwrite it
  with broken JSON, call every route, then restore it.
  ```sh
  cp "$WS" "$F17/workspaces.good.json"
  printf '{"workspaces": [qa-f17-corrupt\n' > "$WS"
  control-agent-server api GET /api/workspaces --expect 409 --check detail eq 'Workspaces file is corrupted' --save F17.corrupted-file/get
  control-agent-server api POST /api/workspaces --expect 409 --check detail eq 'Workspaces file is corrupted' \
    --json '{"workspaces": [{"id": "/qa-f17/c", "name": "qa-f17-c", "path": "/qa-f17/c"}]}' --save F17.corrupted-file/post
  control-agent-server api DELETE /api/workspaces --query "path=$REPO" --expect 409 --check detail eq 'Workspaces file is corrupted' \
    --save F17.corrupted-file/delete
  control-agent-server api POST /api/workspaces/parents --expect 409 --check detail eq 'Workspaces file is corrupted' \
    --json '{"parents": [{"id": "/qa-f17/c", "name": "qa-f17-c", "path": "/qa-f17/c"}]}'
  control-agent-server api DELETE /api/workspaces/parents --query "path=$PLAIN" --expect 409 --check detail eq 'Workspaces file is corrupted'
  printf '{"workspaces": [qa-f17-corrupt\n' | cmp - "$WS"
  : > "$WS"
  control-agent-server api GET /api/workspaces --expect 409 --check detail eq 'Workspaces file is corrupted' --save F17.corrupted-file/empty-get
  control-agent-server api POST /api/workspaces --expect 409 \
    --json '{"workspaces": [{"id": "/qa-f17/c", "name": "qa-f17-c", "path": "/qa-f17/c"}]}'
  test -f "$WS" && test ! -s "$WS"
  cp "$F17/workspaces.good.json" "$WS"
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 5 --check workspaces.0.path eq "$REPO" \
    --check workspaceParents.1.path eq "$PLAIN" --save F17.corrupted-file/restored
  ```
  All five routes are 409 `Workspaces file is corrupted`, `cmp` finds the
  broken bytes untouched, a zero-byte file is refused the same way and stays
  empty, and the restored file lists everything again.
- **Future or invalid schema (`F17.future-schema`).** Valid JSON the server
  must not interpret: a newer `schema_version`, a string `schema_version`, a
  wrongly typed list.
  ```sh
  printf '{"schema_version": 2, "workspaces": [], "workspaceParents": []}\n' > "$WS"
  control-agent-server api GET /api/workspaces --expect 409 --check detail eq 'Workspaces file is corrupted' --save F17.future-schema/get-v2
  control-agent-server api POST /api/workspaces --expect 409 \
    --json '{"workspaces": [{"id": "/qa-f17/f", "name": "qa-f17-f", "path": "/qa-f17/f"}]}' --save F17.future-schema/post-v2
  control-agent-server api DELETE /api/workspaces/parents --query "path=$PLAIN" --expect 409
  printf '{"schema_version": 2, "workspaces": [], "workspaceParents": []}\n' | cmp - "$WS"
  printf '{"schema_version": "1", "workspaces": [], "workspaceParents": []}\n' > "$WS"
  control-agent-server api GET /api/workspaces --expect 409 --check detail eq 'Workspaces file is corrupted'
  control-agent-server api POST /api/workspaces/parents --expect 409 \
    --json '{"parents": [{"id": "/qa-f17/f", "name": "qa-f17-f", "path": "/qa-f17/f"}]}'
  printf '{"schema_version": "1", "workspaces": [], "workspaceParents": []}\n' | cmp - "$WS"
  printf '{"schema_version": 1, "workspaces": "qa-f17-not-a-list"}\n' > "$WS"
  control-agent-server api GET /api/workspaces --expect 409 --check detail eq 'Workspaces file is corrupted'
  control-agent-server api DELETE /api/workspaces --query "path=$REPO" --expect 409
  printf '{"schema_version": 1, "workspaces": "qa-f17-not-a-list"}\n' | cmp - "$WS"
  control-agent-server logs --grep 'Failed to load workspaces' --expect-min 7
  printf '{"workspaces": [{"id": "/qa-f17/legacy", "name": "qa-f17-legacy", "path": "/qa-f17/legacy"}]}\n' > "$WS"
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 1 --check workspaces.0.path eq /qa-f17/legacy \
    --check workspaceParents len-eq 0 --save F17.future-schema/unversioned
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/legacy --expect 200 --check deleted eq true
  control-agent-server state cat home/.openhands/workspaces.json --check schema_version eq 1 --check workspaces len-eq 0
  cp "$F17/workspaces.good.json" "$WS"
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 5 --save F17.future-schema/restored
  ```
  Each variant is 409 on reads and writes and `cmp` shows the file was not
  rewritten. Each of the seven refused loads logs `Failed to load workspaces`
  with a traceback, so `doctor`'s optional `log-tracebacks` check reports
  them after this bullet (without failing `doctor`). The positive control, a
  hand-written document without `schema_version`, is read normally, and the
  delete stamps `schema_version` 1 on it; restoring the backup restores the
  lists.
- **Unreadable file (`F17.io-error`).** Put an empty directory where the file
  was (permissions would not stop a root server), call every route, then put
  the file back.
  ```sh
  mv "$WS" "$F17/workspaces.moved.json"
  mkdir "$WS"
  control-agent-server api GET /api/workspaces --expect 500 --check exception contains 'Failed to read workspaces' --save F17.io-error/get
  control-agent-server api POST /api/workspaces --expect 500 --check exception contains 'Failed to save workspaces' \
    --json '{"workspaces": [{"id": "/qa-f17/io", "name": "qa-f17-io", "path": "/qa-f17/io"}]}' --save F17.io-error/post
  control-agent-server api DELETE /api/workspaces --query "path=$REPO" --expect 500 --check exception contains 'Failed to update workspaces' \
    --save F17.io-error/delete
  control-agent-server api POST /api/workspaces/parents --expect 500 --check exception contains 'Failed to save workspaces' \
    --json '{"parents": [{"id": "/qa-f17/io", "name": "qa-f17-io", "path": "/qa-f17/io"}]}'
  control-agent-server api DELETE /api/workspaces/parents --query "path=$PLAIN" --expect 500 --check exception contains 'Failed to update workspaces'
  control-agent-server state ls 'home/.openhands/workspaces.tmp*' --expect-count 0
  control-agent-server logs --grep 'Cannot access workspaces file' --expect-min 5
  rmdir "$WS"
  mv "$F17/workspaces.moved.json" "$WS"
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 5 --check workspaces.0.path eq "$REPO" \
    --check . not-contains /qa-f17/io --save F17.io-error/restored
  ```
  Every route is 500 with `detail` `Internal Server Error` (the server's 5xx
  rewrite) and the step in `exception` (`500: Failed to read workspaces`);
  `rmdir` succeeds because nothing was written into the directory, no
  temporary file is left next to it, and the restored file lists everything
  again. The log has one `Cannot access workspaces file: [Errno 21] Is a
  directory` ERROR line per call and no traceback: the 500 is a deliberate
  `HTTPException`, which the server logs without a stack.
- **No-op writes on read-only storage (`F17.noop-write-readonly`), known bug.**
  Make the persistence directory immutable (`chattr +i`, standing in for a
  read-only mount: the server owns the directory and re-applies mode `0700`
  before each save, so permissions would not stop it), then write.
  ```sh
  PDIR="$AGENT_SERVER_VERIFY_RUN/home/.openhands"
  BEFORE=$(control-agent-server api GET /api/workspaces --field .)
  chattr +i "$PDIR"
  trap 'chattr -i "$PDIR"' EXIT
  control-agent-server api GET /api/workspaces --expect 200 --check . eq "$BEFORE" --save F17.noop-write-readonly/get
  control-agent-server api POST /api/workspaces --expect 500 --check exception contains 'Failed to save workspaces' \
    --json '{"workspaces": [{"id": "/qa-f17/ro", "name": "qa-f17-ro", "path": "/qa-f17/ro"}]}' --save F17.noop-write-readonly/real-add
  control-agent-server state ls 'home/.openhands/workspaces.tmp*' --expect-count 0
  control-agent-server api DELETE /api/workspaces --query path=/qa-f17/never --expect 404 \
    --check detail eq 'Workspace not found' --save F17.noop-write-readonly/delete-unknown  # bug
  control-agent-server api POST /api/workspaces --json-file "$F17/readd.json" --expect 200 --check workspaces len-eq 5  # bug
  control-agent-server api DELETE /api/workspaces/parents --query path=/qa-f17/never --expect 404  # bug
  control-agent-server api POST /api/workspaces/parents --json-file "$F17/plain-parent.json" --expect 200 \
    --check workspaceParents len-eq 2  # bug
  chattr -i "$PDIR"
  trap - EXIT
  control-agent-server api GET /api/workspaces --expect 200 --check . eq "$BEFORE" --check . not-contains /qa-f17/ro
  ```
  Reads still answer the stored document and a real add is 500 `Failed to
  save workspaces` with no temporary file left, which shows the storage is
  read-only. Expected: a delete of an unknown path is still 404 and
  re-posting saved workspaces or the saved `PLAIN` parent still 200, since
  none of them has anything to write. Today the first delete is 500 (in
  `exception`: `Failed to update workspaces`), because the no-op is saved
  like a change. The `trap` clears the flag whether the bullet passes or
  fails, and the lists are unchanged. Needs root (`CAP_LINUX_IMMUTABLE`) and
  a filesystem with the immutable attribute (ext4, xfs, btrfs); without them
  `chattr` is the failing command and the bullet reports `fail`, not
  `xfail`.
- **Location without `OH_PERSISTENCE_DIR` (`F17.persistence-location`).**
  Restart with the variable empty, write, then restore the launch
  environment.
  ```sh
  control-agent-server restart --env OH_PERSISTENCE_DIR=
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 0 --check workspaceParents len-eq 0 \
    --save F17.persistence-location/empty
  control-agent-server api POST /api/workspaces --expect 200 --check workspaces len-eq 1 \
    --json '{"workspaces": [{"id": "/qa-f17/moved", "name": "qa-f17-moved", "path": "/qa-f17/moved"}]}' --save F17.persistence-location/post
  control-agent-server state cat server/workspace/.openhands/workspaces.json --mode 600 --check workspaces.0.path eq /qa-f17/moved
  control-agent-server state cat home/.openhands/workspaces.json --not-contains /qa-f17/moved --check workspaces.0.path eq "$REPO"
  control-agent-server restart --reset-config
  control-agent-server api GET /api/workspaces --expect 200 --check workspaces len-eq 5 --check . not-contains /qa-f17/moved \
    --save F17.persistence-location/restored
  ```
  With the variable empty the server reads and writes
  `server/workspace/.openhands/workspaces.json` (the parent of the run's
  `conversations_path`, plus `.openhands`) and leaves the file under `home/`
  alone; after `restart --reset-config` the original lists are back.

## Gotchas

- The workspaces store is a process-wide singleton created on the first
  request (`get_workspaces_store`), so the file location is fixed for the
  life of the process: changing `OH_PERSISTENCE_DIR` needs a restart.
  Without it, the location follows `conversations_path`, which for the
  default relative `workspace/conversations` is `./workspace/.openhands`
  under the server's working directory. The contents are not cached: every
  request reads the file, so a hand edit or another server on the same
  directory shows up on the next call (`F17.shared-dir`).
- Every write route rewrites the whole file, also when nothing changed
  (`FileWorkspacesStore.update` saves whatever the route's `apply` returns),
  a low-severity known bug with three faces: on a fresh persistence
  directory a 404 `DELETE` creates `workspaces.json` (empty lists)
  (`F17.noop-delete-no-file`); an idempotent re-add or a 404 `DELETE`
  replaces the file with identical content (`F17.noop-write-untouched`); and
  on a directory the server cannot write, a `DELETE` of an unknown path is
  500 `Failed to update workspaces` instead of 404 and a re-post of a saved
  path 500 instead of 200 (`F17.noop-write-readonly`, which needs root for
  `chattr +i`). Any write, including a refused delete, also creates
  `.workspaces.lock`, which is the lock and not part of the bug. Only `GET`
  and the refusals that stop before the store (401, 422, the 409 of a
  corrupted or too-new document and the 500 of an unreadable file) leave the
  disk alone, which is why `F17.list-empty` and `F17.noop-delete-no-file`
  run before any other call. A zero-byte `workspaces.json` counts as
  corrupted: the registry stays 409 until someone fixes or removes the file.
- Matching is exact string comparison on `path`: no normalization, no
  existence check, relative paths allowed. `/x` and `/x/` are separate
  entries, and the `id` is not unique-checked (two entries may share an id
  if their paths differ). The GUI sets `id` equal to `path` (see the comment
  on `WorkspaceItem`); `search_subdirs` returns paths without a trailing
  slash, so picking from it avoids near-duplicates.
- Deleting a parent does not touch workspaces whose `parentPath` names it;
  clients must tolerate a dangling `parentPath`.
- Unknown item fields are dropped silently (Pydantic's default `ignore`), on
  the wire and on disk, so a client cannot round-trip extra metadata through
  the registry.
- The 409 detail is `Workspaces file is corrupted` for every refusal,
  including a document from a newer schema: the API does not distinguish
  "broken" from "too new". An unreadable file is 500, and the 5xx handler
  replaces `detail` with `Internal Server Error`: read the step from
  `exception` (`F17.io-error`). A write fails while loading, before any
  temporary file is created.
- The routes sit behind the regular `/api` dependencies: 401 without the
  session key, and 503 on a dormant deferred-init server (see the deferred
  init family). They emit no WebSocket events and no webhooks, so the only
  second views are `GET /api/workspaces` and the file.
- The TypeScript client checks `/server_info` before every call and throws
  `AgentServerVersionError` for servers older than 1.23.0. `F17.ts-client`
  imports the built `clients/typescript/dist` and builds it only when it is
  missing: after changing `clients/typescript/src`, run `npm run build`
  there first or the bullet tests the old client.
