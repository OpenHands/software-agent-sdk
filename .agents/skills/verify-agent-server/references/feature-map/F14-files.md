# File upload, download, archive and directory helpers

Byte-level file transfer on the agent server's host and the directory helpers
around it. Consumers upload one file per multipart request (field `file`,
destination in the `path` query), download it back as an
`application/octet-stream` attachment, create directories (`mkdir -p`), pack a
directory as a git patch or a `tar.gz` for persistence, and export a
conversation's persisted trajectory as a zip with credentials redacted. The
workspace picker lists the server user's home and the subdirectories of any
absolute path. Every operation except `home` and `search_subdirs` also exists
under `/api/conversations/{runtime_conversation_id}/file/...`, where the path
must resolve inside that conversation's workspace; the global routes accept
any absolute path, so the session key is their only guard.

Source: `openhands-agent-server/openhands/agent_server/file_router.py`, `openhands-agent-server/openhands/agent_server/runtime_router.py`, `openhands-agent-server/openhands/agent_server/_secret_redaction.py`, `openhands-sdk/openhands/sdk/git/utils.py`, `openhands-sdk/openhands/sdk/workspace/remote/remote_workspace_mixin.py`, `clients/typescript/src/client/file-client.ts`, `clients/typescript/src/workspace/remote-workspace.ts`

Needs: `llm`, `git`, `node`

Routes: `GET /api/file/home`, `GET /api/file/search_subdirs`,
`POST /api/conversations/{runtime_conversation_id}/file/upload`,
`GET /api/conversations/{runtime_conversation_id}/file/download`,
`POST /api/conversations/{runtime_conversation_id}/file/create_directory`,
`GET /api/conversations/{runtime_conversation_id}/file/download-trajectory/{conversation_id}`,
`GET /api/conversations/{runtime_conversation_id}/file/archive`,
`POST /api/file/upload`, `GET /api/file/download`,
`POST /api/file/create_directory`,
`GET /api/file/download-trajectory/{conversation_id}`, `GET /api/file/archive`

## Sub-features

- `F14.auth`: every global and conversation-scoped file route answers 401 without a valid session key, and nothing is written.
- `F14.upload-download`: a multipart upload creates missing parent directories, a download returns byte-identical bytes as an `application/octet-stream` attachment named after the basename, and a second upload to the same path replaces the content.
- `F14.upload-errors`: an upload without the `file` part or without `path` is 422, and a relative `path` is 400 `Path must be absolute`.
- `F14.upload-onto-directory`: uploading onto an existing directory, or to a path whose parent is a file, is a client error (4xx); today it is a masked 500.
- `F14.upload-size`: the SDK's `RemoteWorkspace.file_upload` and the TypeScript `RemoteWorkspace.fileUpload` report the uploaded `file_size`, as `LocalWorkspace.file_upload` does; today the server sends only `{"success": true}` and both report no size.
- `F14.upload-atomic`: an overwrite that fails mid-write leaves the previous file intact; today the target is truncated before streaming and keeps a partial file.
- `F14.download-errors`: downloading a missing file is 404 `File not found`, a directory 400 `Path is not a file`, a relative path 400.
- `F14.mkdir`: `create_directory` creates missing parents, is idempotent and keeps existing contents, the new directory is listed by `search_subdirs`, and a path that is or runs through a file is 400.
- `F14.home`: `GET /api/file/home` reports the server user's `HOME`, its visible top-level directories as case-insensitively sorted `favorites` (no files, no symlinks), `/` as the only location, and hidden directories only with `include_hidden=true`.
- `F14.subdirs`: `search_subdirs` lists only the immediate real subdirectories of an absolute path (no files, no symlinks, hidden only with `include_hidden=true`), sorted case-insensitively, with absolute paths.
- `F14.subdirs-pagination`: `limit` and the `next_page_id` cursor page through the entries; `limit` outside 1..100 is 422.
- `F14.subdirs-errors`: `search_subdirs` on a missing directory is 404, on a file 400, on a relative path 400.
- `F14.subdirs-case-pagination`: following `next_page_id` across two directories that differ only by case (`Foo`, `foo`) reaches the second one instead of repeating the first page forever.
- `F14.archive-tar`: `format=tar.gz` returns an `application/gzip` archive named `<dir>.tar.gz`, rooted at `<dir>/`, with `archive_manifest.json`, the repository identity headers but no `X-Archive-Base-*`, the default excludes applied (`.git/`, `node_modules/`), symlinks skipped, and no temp file left in the parent directory.
- `F14.archive-excludes`: `use_default_excludes=false` keeps `node_modules` and `.git` internals but never `.git/config` or reflogs; `exclude=<glob>` (repeatable) drops matching files and is recorded in the manifest.
- `F14.archive-git-delta`: the default `git-delta` format with `base_ref=HEAD` is a `text/x-patch` of the uncommitted and untracked changes that applies to a clone of HEAD, carries `X-Archive-Base-Commit`, `X-Archive-Base-Ref`, `X-Archive-Branch` and `X-Archive-Head-Commit`, and leaves the repository's index untouched; without `base_ref` on a remote-less repo the patch is against the empty tree and has no base headers; a subdirectory of a repository yields only that subtree's delta.
- `F14.archive-auto-base`: without `base_ref`, a clone with an `origin` remote is diffed against `origin/<branch>` (the patch holds the local commit and the uncommitted edit, applies to a clone of the origin tip, and carries `X-Archive-Base-Ref: auto`, `X-Archive-Base-Commit` = the origin tip and `X-Archive-Repo-Remote`); a detached HEAD is diffed against HEAD itself.
- `F14.archive-nested-repo`: archiving a non-repo directory that holds exactly one git repository archives that repository; with two repositories `git-delta` is 400 and `tar.gz` archives the directory itself.
- `F14.archive-format-doc`: the OpenAPI description of `format` matches what a non-repository directory holding one repository archives; today it promises a tarball of "the entire directory" while only the nested repository is packed.
- `F14.archive-errors`: a relative path is 400, a missing directory 404, a file 400, a `base_ref` starting with `-` 400, an unknown `base_ref` 400, an unknown `format` 422, and `git-delta` of a non-repo directory 400.
- `F14.trajectory`: `GET /api/file/download-trajectory/{id}` returns `<hex>.zip` with `meta.json`, `base_state.json` and one file per event, the LLM `api_key` redacted to `**********` and no Fernet ciphertext, for dashed and hex ids, and leaves no temp zip behind.
- `F14.trajectory-errors`: an unknown conversation id is 404 `Conversation not found`, a malformed id 422.
- `F14.trajectory-concurrent`: concurrent downloads of the same trajectory each return 200 and a valid zip (five rounds of six parallel downloads, 30 in all).
- `F14.trajectory-init-path`: after `POST /api/init` sets `conversations_path` on a deferred-init server, trajectories stored there are downloadable.
- `F14.scoped-transfer`: conversation-scoped upload, download and `create_directory` work inside the conversation's workspace, and the scoped download returns the file the agent wrote.
- `F14.scoped-path-guard`: scoped routes reject paths outside the workspace (another conversation's workspace, `..` traversal, a symlink escape, a sibling with the same prefix, a relative path) with 422 and write nothing, while the global route serves the same path.
- `F14.scoped-conversation-errors`: a scoped route for an unknown conversation is 404 and does nothing; a malformed conversation id is 422.
- `F14.scoped-trajectory-archive`: the scoped trajectory route serves only its own conversation (another or malformed id is 422), and the scoped archive packs the workspace, including the agent's file, but refuses a path outside it with 422.
- `F14.sdk-workspace`: the SDK's `RemoteWorkspace.file_upload`/`file_download` round-trip bytes through the global routes and, with `runtime_conversation_id`, through the scoped routes; a scoped upload outside the workspace returns `success=False` with the 422 and writes nothing.
- `F14.restart`: after a restart, uploaded files, the scoped download and the trajectory are still served.

## How to get to it (agent POV)

- REST, global: `POST /api/file/upload?path=...` (multipart field `file`),
  `GET /api/file/download?path=...`, `POST /api/file/create_directory?path=...`,
  `GET /api/file/archive?path=...&format=git-delta|tar.gz&base_ref=...&use_default_excludes=...&exclude=...`,
  `GET /api/file/download-trajectory/{conversation_id}`. Every recipe below
  drives these.
- REST, conversation-scoped: the same five operations under
  `/api/conversations/{runtime_conversation_id}/file/...`; a dependency
  (`bind_local_conversation_runtime`) resolves `path` and refuses anything
  outside `workspace.working_dir`, and the trajectory id must equal the
  runtime id. Driven by the `F14.scoped-*` and `F14.restart` bullets.
- REST, discovery (global only, there is no scoped variant):
  `GET /api/file/home?include_hidden=...` and
  `GET /api/file/search_subdirs?path=...&limit=...&page_id=...&include_hidden=...`.
- SDK: `RemoteWorkspace.file_upload()` / `file_download()` (and the async
  `AsyncRemoteWorkspace` versions) call `/api/file/upload|download`, or the
  scoped routes when the workspace has `runtime_conversation_id`. Driven by
  `F14.sdk-workspace` (an `exec` program, both scopes) and `F14.upload-size`
  (the upload result's `file_size`, against `LocalWorkspace.file_upload`).
- TypeScript client: `FileClient.uploadFile`, `uploadTextFile`, `downloadFile`,
  `downloadTextFile`, `downloadTrajectory` (scoped through the runtime
  transport when a `conversationId` is set), `getHome`,
  `searchSubdirectories`; `RemoteConversation.downloadTrajectory()` uses the
  global trajectory route; `RemoteWorkspace.fileUpload`/`fileDownload` call
  `/api/file/upload|download` (the TypeScript `write_file` tool reports
  `fileUpload`'s `file_size`). `F14.upload-size` drives
  `RemoteWorkspace.fileUpload` from the built `clients/typescript/dist`.
- Agent Canvas (context only): the workspace picker uses `home` and
  `search_subdirs`; file upload and "download trajectory" use the transfer
  routes. `archive` and `create_directory` have no in-repo consumer; the
  hosting app calls them to persist a workspace before deleting a runtime.

## Driving it with control-agent-server

Preconditions:

- A run is live and exported, `doctor` is ok, `$DEEPSEEK_API_KEY` is set and
  `git` is installed. No launch flags are needed; the init bullet starts its
  own deferred-init server and stops it.
- The block below first restarts the server with
  `GIT_CEILING_DIRECTORIES` set to the run directory, so git on the server
  cannot discover a repository above the run (a stray `/tmp/.git` turns every
  "non-repo" fixture into part of that repository and stops the server from
  `git init`ing workspaces; see Gotchas). It then activates `deepseek-flash`,
  creates the run-owned fixtures
  under `$AGENT_SERVER_VERIFY_RUN/fixtures/` (a 70 kB random blob, a small text
  file, the `qa-f14-repo` git fixture with a `node_modules/` and a `qa.log`
  added), runs one tiny model conversation `CID` whose agent writes
  `qa-f14-agent.txt` with the file editor (no `tmux` needed), and creates a
  second, never-run conversation `CID2` with its own workspace.
  ```sh
  control-agent-server restart --env GIT_CEILING_DIRECTORIES="$AGENT_SERVER_VERIFY_RUN"
  control-agent-server llm preset deepseek
  F="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f14"
  mkdir -p "$F"
  head -c 70000 /dev/urandom > "$F/blob.bin"
  printf 'qa-f14 v2\n' > "$F/v2.txt"
  REPO=$(control-agent-server fixture git-repo --name qa-f14-repo --print-path)
  mkdir -p "$REPO/node_modules/pkg"
  printf 'j\n' > "$REPO/node_modules/pkg/j.js"
  printf 'log\n' > "$REPO/qa.log"
  CID=$(control-agent-server conversation start --tools file_editor --no-autotitle \
    --prompt 'Use the file_editor tool to create the file qa-f14-agent.txt in the current working directory containing exactly: hi' \
    --wait --until finished --timeout 300 --print-id)
  WS=$(control-agent-server api GET "/api/conversations/$CID" --field workspace.working_dir)
  control-agent-server conversation events "$CID" --kinds ObservationEvent --contains qa-f14-agent.txt --expect-kind ObservationEvent
  test -d "$WS/.git"
  CID2=$(control-agent-server conversation start --tools file_editor --no-autotitle --print-id)
  WS2=$(control-agent-server api GET "/api/conversations/$CID2" --field workspace.working_dir)
  ```

- **Session key required (`F14.auth`).** Call every route without a key (one
  with a wrong key).
  ```sh
  control-agent-server api GET /api/file/home --auth none --expect 401
  control-agent-server api GET /api/file/search_subdirs --auth none --query path="$F" --expect 401
  control-agent-server api POST /api/file/upload --auth none --query path="$F/noauth/x.bin" --file file="$F/blob.bin" --expect 401
  control-agent-server api GET /api/file/download --auth bad --query path="$F/blob.bin" --expect 401
  control-agent-server api POST /api/file/create_directory --auth none --query path="$F/noauth-dir" --expect 401
  control-agent-server api GET /api/file/archive --auth none --query path="$REPO" --expect 401
  control-agent-server api GET "/api/file/download-trajectory/$CID" --auth none --expect 401
  control-agent-server api POST "/api/conversations/$CID/file/upload" --auth none --query path="$WS/noauth.bin" --file file="$F/blob.bin" --expect 401
  control-agent-server api GET "/api/conversations/$CID/file/download" --auth none --query path="$WS/qa-f14-agent.txt" --expect 401
  control-agent-server api POST "/api/conversations/$CID/file/create_directory" --auth none --query path="$WS/noauth-dir" --expect 401
  control-agent-server api GET "/api/conversations/$CID/file/archive" --auth none --query path="$WS" --expect 401
  control-agent-server api GET "/api/conversations/$CID/file/download-trajectory/$CID" --auth none --expect 401 \
    --check detail eq Unauthorized --save F14.auth/scoped-trajectory
  test ! -e "$F/noauth"
  test ! -e "$F/noauth-dir"
  test ! -e "$WS/noauth.bin"
  test ! -e "$WS/noauth-dir"
  ```
  All twelve answer `401 {"detail": "Unauthorized"}` and no file or
  directory appears.
- **Upload and download round trip (`F14.upload-download`).** Upload a binary
  blob three directories deep, download it, then overwrite it.
  ```sh
  control-agent-server api POST /api/file/upload --query path="$F/xfer/a/b/blob.bin" --file file="$F/blob.bin" \
    --expect 200 --check success eq true --save F14.upload-download/upload
  test -d "$F/xfer/a/b"
  control-agent-server api GET /api/file/download --query path="$F/xfer/a/b/blob.bin" --expect 200 \
    --check-header content-disposition eq 'attachment; filename="blob.bin"' \
    --check-header content-type eq application/octet-stream \
    --raw-out "$F/blob.out" --save F14.upload-download/download
  cmp "$F/blob.bin" "$F/blob.out"
  control-agent-server api POST /api/file/upload --query path="$F/xfer/a/b/blob.bin" --file file="$F/v2.txt" --expect 200
  control-agent-server api GET /api/file/download --query path="$F/xfer/a/b/blob.bin" --expect 200 \
    --raw-out "$F/v2.out" --save F14.upload-download/overwritten
  cmp "$F/v2.txt" "$F/v2.out"
  ```
  Upload answers `{"success": true}` (no size field), the parents exist, the
  downloaded bytes equal the 70 kB blob, the response is an attachment named
  `blob.bin`, and after the second upload the same path holds the text file.
- **Upload validation (`F14.upload-errors`).** Missing parts and a relative
  path.
  ```sh
  control-agent-server api POST /api/file/upload --query path="$F/xfer/no-part.txt" --expect 422 --check detail.0.loc contains file
  control-agent-server api POST /api/file/upload --file file="$F/blob.bin" --expect 422 --check detail.0.loc contains path
  control-agent-server api POST /api/file/upload --query path="$F/xfer/wrong-field.txt" --file upload="$F/blob.bin" --expect 422
  control-agent-server api POST /api/file/upload --query path=qa-f14-relative.txt --file file="$F/blob.bin" \
    --expect 400 --check detail eq 'Path must be absolute' --save F14.upload-errors/relative
  test ! -e "$F/xfer/no-part.txt"
  test ! -e "$F/xfer/wrong-field.txt"
  ```
  A missing `file` part (or one sent under another field name) and a missing
  `path` are 422 with the missing location; a relative path is 400.
- **Upload onto a directory (`F14.upload-onto-directory`), known bug.** The
  caller's path is wrong, which `create_directory` reports as 400 for the
  same collision; each of the two uploads alone encodes the bug.
  ```sh
  mkdir -p "$F/xfer/is-a-dir"
  control-agent-server api POST /api/file/create_directory --query path="$F/v2.txt/child" --expect 400 \
    --check detail eq 'Path exists and is not a directory'
  control-agent-server api POST /api/file/upload --query path="$F/xfer/is-a-dir" --file file="$F/v2.txt" \
    --expect 4xx --save F14.upload-onto-directory/upload  # bug
  control-agent-server api POST /api/file/upload --query path="$F/v2.txt/child" --file file="$F/v2.txt" \
    --expect 4xx --save F14.upload-onto-directory/parent-is-file  # bug
  ```
  Expected: a 4xx such as 400 `Path exists and is not a directory`, as
  `create_directory` answers for the same collisions. Actual:
  `500 {"detail": "Internal Server Error", "exception": "500: Failed to upload file: [Errno 21] Is a directory: ..."}`
  (`_upload_file` maps every exception to 500). A path whose parent is a file
  (`$F/v2.txt/child`) fails the same way (`[Errno 17] File exists`).
- **Upload size in the consumers' result (`F14.upload-size`), known bug.**
  The same 70 kB blob through `LocalWorkspace.file_upload` (the contract the
  remote workspaces mirror), the SDK's `RemoteWorkspace.file_upload` and the
  TypeScript client's `RemoteWorkspace.fileUpload`; each program checks the
  upload landed byte for byte and writes the `file_size` it got.
  ```sh
  cat > "$F/sdk_size.py" <<'PY'
  import os
  import sys
  from pathlib import Path

  from openhands.sdk.workspace import LocalWorkspace
  from openhands.sdk.workspace.remote.base import RemoteWorkspace

  fixtures = Path(sys.argv[1])
  blob = (fixtures / "blob.bin").read_bytes()
  local = LocalWorkspace(working_dir=str(fixtures))
  done = local.file_upload(fixtures / "blob.bin", fixtures / "size" / "local.bin")
  assert done.success and done.file_size == len(blob), done
  remote = RemoteWorkspace(
      host=os.environ["AGENT_SERVER_URL"],
      api_key=os.environ["SESSION_API_KEY"],
      working_dir=str(fixtures),
  )
  target = fixtures / "size" / "sdk.bin"
  up = remote.file_upload(fixtures / "blob.bin", target)
  assert up.success and up.error is None and target.read_bytes() == blob, up
  (fixtures / "sdk-size.txt").write_text(str(up.file_size))
  print("SDK_UPLOAD_OK")
  PY
  cat > "$F/ts_size.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { readFileSync, writeFileSync } from 'node:fs';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { RemoteWorkspace } = await import(pathToFileURL(resolve('clients/typescript/dist/index.js')).href);
  const [fixtures] = process.argv.slice(2);
  const blob = readFileSync(`${fixtures}/blob.bin`);
  const ws = new RemoteWorkspace({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY, workingDir: fixtures });
  const up = await ws.fileUpload(new Blob([blob]), `${fixtures}/size/ts.bin`, 'ts.bin');
  assert.equal(up.success, true);
  assert.ok(readFileSync(`${fixtures}/size/ts.bin`).equals(blob));
  writeFileSync(`${fixtures}/ts-size.txt`, String(up.file_size));
  console.log('TS_UPLOAD_OK');
  JS
  control-agent-server exec --expect-output SDK_UPLOAD_OK --save F14.upload-size/sdk -- .venv/bin/python "$F/sdk_size.py" "$F"
  control-agent-server exec --expect-output TS_UPLOAD_OK --save F14.upload-size/ts -- node "$F/ts_size.mjs" "$F"
  control-agent-server api POST /api/file/upload --query path="$F/size/rest.bin" --file file="$F/blob.bin" --expect 200 \
    --check success eq true --save F14.upload-size/rest
  test "$(cat "$F/sdk-size.txt")" = 70000  # bug
  test "$(cat "$F/ts-size.txt")" = 70000  # bug
  ```
  Expected: both remote workspaces report `file_size` 70000, as
  `LocalWorkspace` does. Actual: every upload lands intact, but the server
  answers only `{"success": true}` and both clients copy a `file_size` that is
  never sent: the SDK reports `None`, the TypeScript client `undefined` (its
  `write_file` tool then says `Successfully wrote undefined bytes`). Either
  fix (the server sends the size, or the clients compute it) passes.
- **Failed overwrite (`F14.upload-atomic`), known bug.** A second server
  (stopped by the trap) has its file-size limit lowered to 500 kB with
  `prlimit`, the way a full disk or a quota fails a write mid-stream, while a
  900 kB upload overwrites a small file. The limit is lifted before the final
  check.
  ```sh
  B2=$(control-agent-server launch --new --name f14-atomic --print-run)
  trap 'control-agent-server stop --run "$B2" > /dev/null' EXIT
  AT="$B2/fixtures/qa-f14-atomic"
  mkdir -p "$AT"
  head -c 900000 /dev/urandom > "$AT/big.bin"
  control-agent-server api POST /api/file/upload --run "$B2" --query path="$AT/big-copy.bin" --file file="$AT/big.bin" --expect 200
  cmp "$AT/big.bin" "$AT/big-copy.bin"
  control-agent-server api POST /api/file/upload --run "$B2" --query path="$AT/target.txt" --file file="$F/v2.txt" --expect 200
  cmp "$F/v2.txt" "$AT/target.txt"
  B2_PID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["pid"])' "$B2/run.json")
  prlimit --pid "$B2_PID" --fsize=500000:
  control-agent-server api POST /api/file/upload --run "$B2" --query path="$AT/target.txt" --file file="$AT/big.bin" \
    --expect 5xx --check exception contains 'File too large' --save F14.upload-atomic/failed-overwrite
  prlimit --pid "$B2_PID" --fsize=unlimited:
  control-agent-server doctor --run "$B2"
  cmp "$F/v2.txt" "$AT/target.txt"  # bug
  ```
  Without the limit the 900 kB blob uploads intact (so the size alone is not
  the problem) and the small file is in place. Under the limit the overwrite
  fails with `500 ... [Errno 27] File too large`, and the server is healthy
  once the limit is lifted. Expected: the failed upload leaves `target.txt`
  holding the previous small file (write to a temporary file, then rename).
  Actual: `_upload_file` opens the target with `"wb"` before streaming, so
  `target.txt` now holds the first 500000 bytes of the blob: the old content
  is gone and a partial file is left behind.
- **Download errors (`F14.download-errors`).** Missing file, directory,
  relative path, no path.
  ```sh
  control-agent-server api GET /api/file/download --query path="$F/xfer/missing.bin" --expect 404 \
    --check detail eq 'File not found' --save F14.download-errors/missing
  control-agent-server api GET /api/file/download --query path="$F/xfer" --expect 400 --check detail eq 'Path is not a file'
  control-agent-server api GET /api/file/download --query path=xfer/a/b/blob.bin --expect 400 --check detail eq 'Path must be absolute'
  control-agent-server api GET /api/file/download --expect 422
  ```
  The statuses are 404, 400, 400 and 422.
- **Create directories (`F14.mkdir`).** `mkdir -p`, idempotent, then collisions
  with files.
  ```sh
  control-agent-server api POST /api/file/create_directory --query path="$F/tree/x/y/z" --expect 200 \
    --check success eq true --save F14.mkdir/create
  control-agent-server api POST /api/file/upload --query path="$F/tree/x/y/z/keep.txt" --file file="$F/v2.txt" --expect 200
  control-agent-server api POST /api/file/create_directory --query path="$F/tree/x/y/z" --expect 200 --check success eq true
  test -d "$F/tree/x/y/z"
  cmp "$F/v2.txt" "$F/tree/x/y/z/keep.txt"
  control-agent-server api GET /api/file/search_subdirs --query path="$F/tree/x/y" --expect 200 \
    --check items len-eq 1 --check items.0.name eq z --check items.0.path eq "$F/tree/x/y/z" --save F14.mkdir/listed
  control-agent-server api POST /api/file/create_directory --query path="$F/blob.bin" --expect 400 \
    --check detail eq 'Path exists and is not a directory'
  control-agent-server api POST /api/file/create_directory --query path="$F/blob.bin/sub" --expect 400 \
    --check detail eq 'Path exists and is not a directory'
  control-agent-server api POST /api/file/create_directory --query path=qa-f14/relative --expect 400 --check detail eq 'Path must be absolute'
  ```
  Both creates answer `{"success": true}`, the file inside survives the second
  one, `search_subdirs` lists `z` with its absolute path, and a file in the
  way (as the target or as a parent) is 400.
- **Home and favorites (`F14.home`).** The launcher gives the server a private
  `HOME` that holds only hidden entries, so the favorites are exactly what
  this bullet creates.
  ```sh
  H="$AGENT_SERVER_VERIFY_RUN/home"
  mkdir -p "$H/qa-f14-Beta" "$H/qa-f14-alpha" "$H/.qa-f14-hidden"
  ln -s "$H/qa-f14-alpha" "$H/qa-f14-link"
  printf 'x\n' > "$H/qa-f14-file.txt"
  control-agent-server api GET /api/file/home --expect 200 --check home eq "$H" \
    --check favorites len-eq 2 --check favorites.0.label eq qa-f14-alpha --check favorites.1.label eq qa-f14-Beta \
    --check favorites.0.path eq "$H/qa-f14-alpha" --check locations len-eq 1 --check locations.0.path eq / \
    --save F14.home/default
  control-agent-server api GET /api/file/home --query include_hidden=true --expect 200 \
    --check favorites contains .qa-f14-hidden --check favorites not-contains qa-f14-link \
    --check favorites not-contains qa-f14-file.txt --save F14.home/include-hidden
  ```
  `home` is the run's private home; `favorites` are `qa-f14-alpha` then
  `qa-f14-Beta` (case-insensitive order), without the symlink and the file;
  `locations` is `[{"label": "/", "path": "/"}]`. With `include_hidden=true`
  the hidden directories (`.qa-f14-hidden`, `.config`, ...) are added.
- **List subdirectories (`F14.subdirs`).** A tree with mixed case, a hidden
  directory, a file and a symlink.
  ```sh
  T="$F/subdirs"
  mkdir -p "$T/b" "$T/A" "$T/c" "$T/.hid"
  printf 'f\n' > "$T/f.txt"
  ln -s "$T/A" "$T/lnk"
  control-agent-server api GET /api/file/search_subdirs --query path="$T" --expect 200 --check items len-eq 3 \
    --check items.0.name eq A --check items.1.name eq b --check items.2.name eq c \
    --check items.0.path eq "$T/A" --check next_page_id missing --save F14.subdirs/default
  control-agent-server api GET /api/file/search_subdirs --query path="$T" --query include_hidden=true --expect 200 \
    --check items len-eq 4 --check items.0.name eq .hid
  ```
  Only `A`, `b`, `c` (case-insensitive order, absolute paths, no
  `next_page_id`); `.hid` appears first with `include_hidden=true`.
- **Pagination (`F14.subdirs-pagination`).** Two per page.
  ```sh
  NEXT=$(control-agent-server api GET /api/file/search_subdirs --query path="$T" --query limit=2 --expect 200 \
    --check items len-eq 2 --check items.1.name eq b --field next_page_id)
  test "$NEXT" = c
  control-agent-server api GET /api/file/search_subdirs --query path="$T" --query limit=2 --query page_id="$NEXT" \
    --expect 200 --check items len-eq 1 --check items.0.name eq c --check next_page_id missing \
    --save F14.subdirs-pagination/last-page
  control-agent-server api GET /api/file/search_subdirs --query path="$T" --query limit=0 --expect 422
  control-agent-server api GET /api/file/search_subdirs --query path="$T" --query limit=101 --expect 422
  ```
  Page one is `A`, `b` with `next_page_id` `c` (the lowercase name of the next
  entry); page two is `c` with no cursor; `limit` 0 and 101 are 422.
- **Listing errors (`F14.subdirs-errors`).** Missing, file, relative, no path.
  ```sh
  control-agent-server api GET /api/file/search_subdirs --query path="$T/missing" --expect 404 \
    --check detail eq 'Directory not found' --save F14.subdirs-errors/missing
  control-agent-server api GET /api/file/search_subdirs --query path="$T/f.txt" --expect 400 --check detail eq 'Path is not a directory'
  control-agent-server api GET /api/file/search_subdirs --query path=qa-f14 --expect 400 --check detail eq 'Path must be absolute'
  control-agent-server api GET /api/file/search_subdirs --expect 422
  ```
  The statuses are 404, 400, 400 and 422.
- **Paging past a case-only twin (`F14.subdirs-case-pagination`), known bug.**
  `Foo` and `foo` with one entry per page. Which twin sorts first depends on
  the directory's order on disk, so the recipe reads page one and expects the
  other twin on page two.
  ```sh
  CASE_DIR="$F/case"
  mkdir -p "$CASE_DIR/Foo" "$CASE_DIR/foo"
  CASE_FIRST=$(control-agent-server api GET /api/file/search_subdirs --query path="$CASE_DIR" --query limit=1 \
    --expect 200 --check items len-eq 1 --field items.0.name)
  CASE_NEXT=$(control-agent-server api GET /api/file/search_subdirs --query path="$CASE_DIR" --query limit=1 \
    --expect 200 --check next_page_id exists --field next_page_id)
  if test "$CASE_FIRST" = Foo; then CASE_OTHER=foo; else CASE_OTHER=Foo; fi
  test "$CASE_FIRST" = Foo -o "$CASE_FIRST" = foo
  control-agent-server api GET /api/file/search_subdirs --query path="$CASE_DIR" --query limit=1 --query page_id="$CASE_NEXT" \
    --expect 200 --check items len-eq 1 --check items.0.name eq "$CASE_OTHER" --check next_page_id missing \
    --save F14.subdirs-case-pagination/page-2  # bug
  ```
  Page one holds one twin and a cursor. Expected: page two holds exactly the
  other twin and no cursor. Actual: the cursor is `foo`, the lowercase name,
  and resuming picks the first entry whose lowercase name matches, which is
  the first page's entry again, with `next_page_id` `foo` again. A client
  that follows the cursor loops forever.
- **tar.gz archive (`F14.archive-tar`).** Archive the fixture repo, then a
  plain tree whose symlinks point inside and outside it.
  ```sh
  control-agent-server api GET /api/file/archive --query path="$REPO" --query format=tar.gz --expect 200 \
    --check-header content-disposition eq 'attachment; filename="qa-f14-repo.tar.gz"' \
    --check-header content-type eq application/gzip \
    --check-header x-archive-head-commit eq "$(git -C "$REPO" rev-parse HEAD)" --check-header x-archive-branch eq main \
    --check-header x-archive-base-commit missing --raw-out "$F/repo.tgz" --save F14.archive-tar/repo
  tar tzf "$F/repo.tgz" > "$F/repo.list"
  grep -qx 'qa-f14-repo/README.md' "$F/repo.list"
  grep -qx 'qa-f14-repo/notes.txt' "$F/repo.list"
  grep -qx 'qa-f14-repo/src/util.py' "$F/repo.list"
  grep -qx 'qa-f14-repo/archive_manifest.json' "$F/repo.list"
  test -z "$(grep -E '/\.git/|node_modules' "$F/repo.list")"
  tar xzOf "$F/repo.tgz" qa-f14-repo/archive_manifest.json | python3 -c 'import json,sys; m=json.load(sys.stdin); assert m["format"] == "tar.gz" and m["source"] == "qa-f14-repo" and m["file_count"] == 5 and "node_modules/" in m["excludes"], m'
  ln -s /etc "$T/qa-f14-etc-link"
  control-agent-server api GET /api/file/archive --query path="$T" --query format=tar.gz --expect 200 --raw-out "$F/tree.tgz"
  tar tzf "$F/tree.tgz" > "$F/tree.list"
  grep -qx 'subdirs/f.txt' "$F/tree.list"
  grep -qx 'subdirs/A/' "$F/tree.list"
  test -z "$(grep -E 'lnk|etc-link' "$F/tree.list")"
  for i in 1 2 3 4 5; do test -z "$(find "$AGENT_SERVER_VERIFY_RUN/fixtures" "$F" -maxdepth 1 -name 'tmp*')" && break; sleep 0.2; done
  test -z "$(find "$AGENT_SERVER_VERIFY_RUN/fixtures" "$F" -maxdepth 1 -name 'tmp*')"
  ```
  The repo archive is `qa-f14-repo.tar.gz` with the five working-tree files
  (tracked, modified, untracked and `qa.log`) and the manifest
  (`file_count` 5), but no `.git/` and no `node_modules/`; it carries the
  repository identity (`X-Archive-Head-Commit`, `X-Archive-Branch`) but no
  `X-Archive-Base-*`, which only describe a patch. The tree archive
  holds `f.txt` and the real directories (empty ones too) but neither symlink.
  The temp archive the server builds in the parent directory is gone (the
  short poll waits for the server's own after-response cleanup).
- **Exclude controls (`F14.archive-excludes`).** Full capture, then one extra
  glob.
  ```sh
  control-agent-server api GET /api/file/archive --query path="$REPO" --query format=tar.gz --query use_default_excludes=false \
    --expect 200 --raw-out "$F/full.tgz" --save F14.archive-excludes/full
  tar tzf "$F/full.tgz" > "$F/full.list"
  grep -qx 'qa-f14-repo/node_modules/pkg/j.js' "$F/full.list"
  grep -qx 'qa-f14-repo/.git/HEAD' "$F/full.list"
  test -f "$REPO/.git/config"
  test -d "$REPO/.git/logs"
  test -z "$(grep -E '\.git/(config|logs/)' "$F/full.list")"
  control-agent-server api GET /api/file/archive --query path="$REPO" --query format=tar.gz --query 'exclude=*.log' \
    --expect 200 --raw-out "$F/nolog.tgz" --save F14.archive-excludes/exclude-log
  tar tzf "$F/nolog.tgz" > "$F/nolog.list"
  grep -qx 'qa-f14-repo/notes.txt' "$F/nolog.list"
  test -z "$(grep 'qa.log' "$F/nolog.list")"
  tar xzOf "$F/nolog.tgz" qa-f14-repo/archive_manifest.json | python3 -c 'import json,sys; m=json.load(sys.stdin); assert m["excludes"][-1] == "*.log" and ".git/" in m["excludes"], m'
  control-agent-server api GET /api/file/archive --query path="$REPO" --query format=tar.gz --query 'exclude=*.log' \
    --query exclude=notes.txt --expect 200 --raw-out "$F/two-ex.tgz" --save F14.archive-excludes/exclude-two
  tar tzf "$F/two-ex.tgz" > "$F/two-ex.list"
  grep -qx 'qa-f14-repo/README.md' "$F/two-ex.list"
  test -z "$(grep -E 'qa\.log|notes\.txt' "$F/two-ex.list")"
  ```
  The full capture has `node_modules/pkg/j.js` and `.git/HEAD` but no
  `.git/config` and no `.git/logs/` although both exist on disk; `exclude=*.log`
  drops `qa.log`, keeps the defaults, and the manifest lists `*.log` last.
  `exclude` is repeatable: with `*.log` and `notes.txt` both files are gone.
- **git-delta patch (`F14.archive-git-delta`).** The default format against
  `HEAD`, then against the automatic base.
  ```sh
  HEAD_SHA=$(git -C "$REPO" rev-parse HEAD)
  control-agent-server api GET /api/file/archive --query path="$REPO" --query base_ref=HEAD --expect 200 \
    --check-header content-disposition eq 'attachment; filename="qa-f14-repo.patch"' \
    --check-header content-type matches '^text/x-patch' \
    --check-header x-archive-base-commit eq "$HEAD_SHA" --check-header x-archive-base-ref eq HEAD \
    --check-header x-archive-branch eq main --check-header x-archive-head-commit eq "$HEAD_SHA" \
    --check-header x-archive-repo-root eq "$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$REPO")" \
    --check-header x-archive-repo-remote missing --raw-out "$F/head.patch" --save F14.archive-git-delta/base-head
  grep -q '^diff --git a/README.md b/README.md' "$F/head.patch"
  grep -q '^diff --git a/notes.txt b/notes.txt' "$F/head.patch"
  grep -q '^diff --git a/qa.log b/qa.log' "$F/head.patch"
  test -z "$(grep -E '^diff --git a/(src/|node_modules/)' "$F/head.patch")"
  git clone -q "$REPO" "$F/clone"
  git -C "$F/clone" apply --check "$F/head.patch"
  git -C "$REPO" diff --cached --quiet
  control-agent-server api GET /api/file/archive --query path="$REPO" --expect 200 \
    --check-header x-archive-branch eq main --check-header x-archive-base-commit missing \
    --check-header x-archive-base-ref missing --raw-out "$F/auto.patch" --save F14.archive-git-delta/auto
  grep -q '^diff --git a/src/app.py b/src/app.py' "$F/auto.patch"
  control-agent-server api GET /api/file/archive --query path="$REPO/src" --query base_ref=HEAD~1 --expect 200 \
    --check-header content-disposition eq 'attachment; filename="src.patch"' \
    --check-header x-archive-base-commit eq "$(git -C "$REPO" rev-parse HEAD~1)" \
    --raw-out "$F/src.patch" --save F14.archive-git-delta/subdir
  test "$(grep -c '^diff --git' "$F/src.patch")" = 1
  grep -q '^diff --git a/src/util.py b/src/util.py' "$F/src.patch"
  ```
  With `base_ref=HEAD` the patch (`qa-f14-repo.patch`, `text/x-patch`) holds
  the modified `README.md` and the untracked `notes.txt` and `qa.log`, not the
  committed `src/` files or `node_modules`; it applies to a fresh clone, and
  the fixture's index has nothing staged afterwards. The headers name the base
  and head commit, `HEAD` and `main`, and `X-Archive-Repo-Root` is the
  percent-encoded repository path; the fixture has no remote, so there is no
  `X-Archive-Repo-Remote`. Without `base_ref` the base is therefore the empty
  tree: every file appears as new and there is no `X-Archive-Base-*` header
  (a clone with a remote is `F14.archive-auto-base`). A subdirectory of the
  repository (`src`, against `HEAD~1`) yields `src.patch` with only that
  subtree's change
  (`src/util.py`, paths still relative to the repository root), not the
  `README.md`, `notes.txt` and `qa.log` changes beside it.
- **Automatic base with a remote (`F14.archive-auto-base`).** The production
  path: archiving a cloned repository before its runtime is deleted. A clone
  of the fixture (its `origin` is the fixture path) gets one local commit and
  an uncommitted edit, then the same clone with a detached HEAD.
  ```sh
  AB="$F/auto-clone"
  git clone -q "$REPO" "$AB"
  ORIGIN_TIP=$(git -C "$AB" rev-parse origin/main)
  printf 'local\n' > "$AB/local.txt"
  git -C "$AB" add local.txt
  git -C "$AB" -c user.name=qa-f14 -c user.email=qa-f14@example.invalid commit -qm 'qa-f14 local commit'
  printf 'edit\n' >> "$AB/README.md"
  LOCAL_HEAD=$(git -C "$AB" rev-parse HEAD)
  test "$LOCAL_HEAD" != "$ORIGIN_TIP"
  control-agent-server api GET /api/file/archive --query path="$AB" --expect 200 \
    --check-header x-archive-base-ref eq auto --check-header x-archive-base-commit eq "$ORIGIN_TIP" \
    --check-header x-archive-head-commit eq "$LOCAL_HEAD" --check-header x-archive-branch eq main \
    --check-header x-archive-repo-remote eq "$(python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$REPO")" \
    --raw-out "$F/auto-clone.patch" --save F14.archive-auto-base/origin
  grep -q '^diff --git a/local.txt b/local.txt' "$F/auto-clone.patch"
  grep -q '^diff --git a/README.md b/README.md' "$F/auto-clone.patch"
  test -z "$(grep -E '^diff --git a/src/' "$F/auto-clone.patch")"
  git clone -q "$REPO" "$F/auto-replay"
  test "$(git -C "$F/auto-replay" rev-parse HEAD)" = "$ORIGIN_TIP"
  git -C "$F/auto-replay" apply --check "$F/auto-clone.patch"
  git -C "$AB" checkout -q --detach
  control-agent-server api GET /api/file/archive --query path="$AB" --expect 200 \
    --check-header x-archive-base-ref eq auto --check-header x-archive-base-commit eq "$LOCAL_HEAD" \
    --check-header x-archive-branch eq DETACHED --raw-out "$F/detached.patch" --save F14.archive-auto-base/detached
  test "$(grep -c '^diff --git' "$F/detached.patch")" = 1
  grep -q '^diff --git a/README.md b/README.md' "$F/detached.patch"
  ```
  On the branch, the omitted `base_ref` resolves to `origin/main`: the patch
  holds the committed-but-unpushed `local.txt` and the `README.md` edit, but
  none of the `src/` files both sides share, and it applies to a fresh clone
  of the origin tip. The headers say `X-Archive-Base-Ref: auto`, the origin
  tip as `X-Archive-Base-Commit`, the local commit as `X-Archive-Head-Commit`,
  and the percent-encoded origin URL as `X-Archive-Repo-Remote`. With HEAD
  detached on the local commit, the base is HEAD itself (not the origin tip):
  `X-Archive-Base-Commit` is the local commit, `X-Archive-Branch` is
  `DETACHED`, `X-Archive-Base-Ref` is still `auto`, and the patch holds only
  the uncommitted `README.md` edit.
- **Nested repository (`F14.archive-nested-repo`).** A non-repo parent with one
  copy of the repo two levels down, then a second copy.
  ```sh
  P="$F/parent"
  mkdir -p "$P/group"
  cp -r "$REPO" "$P/group/proj"
  printf 'top\n' > "$P/top.txt"
  control-agent-server api GET /api/file/archive --query path="$P" --query base_ref=HEAD --expect 200 \
    --check-header content-disposition eq 'attachment; filename="proj.patch"' \
    --raw-out "$F/parent.patch" --save F14.archive-nested-repo/one-repo
  grep -q '^diff --git a/notes.txt b/notes.txt' "$F/parent.patch"
  control-agent-server api GET /api/file/archive --query path="$P" --query format=tar.gz --expect 200 --raw-out "$F/parent.tgz"
  tar tzf "$F/parent.tgz" > "$F/parent.list"
  grep -qx 'proj/notes.txt' "$F/parent.list"
  test -z "$(grep 'top.txt' "$F/parent.list")"
  cp -r "$REPO" "$P/proj2"
  control-agent-server api GET /api/file/archive --query path="$P" --query base_ref=HEAD --expect 400 \
    --check detail contains 'Not a git repository' --save F14.archive-nested-repo/two-repos
  control-agent-server api GET /api/file/archive --query path="$P" --query format=tar.gz --expect 200 --raw-out "$F/parent2.tgz"
  tar tzf "$F/parent2.tgz" > "$F/parent2.list"
  grep -qx 'parent/top.txt' "$F/parent2.list"
  grep -qx 'parent/proj2/notes.txt' "$F/parent2.list"
  ```
  With one repository below it, both formats archive `group/proj` (named
  `proj.patch` / `proj.tar.gz`, rooted at `proj/`) and the parent's own
  `top.txt` is not in the tar. With two, the server refuses to guess:
  `git-delta` is 400 and `tar.gz` archives the parent as given.
- **What `format` promises (`F14.archive-format-doc`), known bug.** The
  OpenAPI description of the `format` parameter, against a `tar.gz` of a
  non-repository directory that holds one repository and a file of its own.
  ```sh
  control-agent-server api GET /openapi.json --expect 200 --quiet --raw-out "$F/openapi.json"
  DOC_FORMAT=$(python3 -c 'import json,sys; ps=json.load(open(sys.argv[1]))["paths"]["/api/file/archive"]["get"]["parameters"]; print(next(p["description"] for p in ps if p["name"] == "format"))' "$F/openapi.json")
  test -n "$DOC_FORMAT"
  DOCP="$F/doc-parent"
  mkdir -p "$DOCP"
  cp -r "$REPO" "$DOCP/proj"
  printf 'top\n' > "$DOCP/top.txt"
  control-agent-server api GET /api/file/archive --query path="$DOCP" --query format=tar.gz --expect 200 \
    --raw-out "$F/doc-parent.tgz" --save F14.archive-format-doc/tar
  tar tzf "$F/doc-parent.tgz" > "$F/doc-parent.list"
  grep -q 'proj/notes.txt$' "$F/doc-parent.list"
  if printf '%s\n' "$DOC_FORMAT" | grep -q 'entire directory' && test -z "$(grep 'top.txt' "$F/doc-parent.list")"; then false; fi  # bug
  ```
  Expected: the description and the archive agree, either by documenting
  that a directory holding exactly one repository archives only that
  repository, or by packing the whole directory. Actual: the description
  says `'tar.gz' for a full gzip tarball of the entire directory (works on
  non-git directories too)`, while the tar holds only `proj/` and not
  `top.txt` (the resolution to the nested repository is deliberate, see
  `archive_directory`, so the description is what drifted).
- **Archive validation (`F14.archive-errors`).**
  ```sh
  control-agent-server api GET /api/file/archive --query path=qa-f14/relative --expect 400 --check detail eq 'Path must be absolute'
  control-agent-server api GET /api/file/archive --query path="$F/missing-dir" --expect 404 \
    --check detail eq 'Directory not found' --save F14.archive-errors/missing
  control-agent-server api GET /api/file/archive --query path="$REPO/README.md" --expect 400 --check detail eq 'Path is not a directory'
  control-agent-server api GET /api/file/archive --query path="$REPO" --query base_ref=--output=/tmp/qa-f14-injected \
    --expect 400 --check detail eq "base_ref must not start with '-'"
  test ! -e /tmp/qa-f14-injected
  control-agent-server api GET /api/file/archive --query path="$REPO" --query base_ref=qa-f14-no-such-ref --expect 400 \
    --check detail contains 'could not be resolved'
  control-agent-server api GET /api/file/archive --query path="$REPO" --query format=zip --expect 422
  control-agent-server api GET /api/file/archive --query path="$T" --expect 400 --check detail contains 'Not a git repository'
  control-agent-server api GET /api/file/archive --expect 422
  ```
  400, 404, 400, 400 (an option-shaped `base_ref` never reaches git), 400,
  422 (`format` is `git-delta` or `tar.gz`), 400 and 422.
- **Trajectory zip (`F14.trajectory`).** Download the model conversation's
  trajectory and compare it with the event count and the file on disk.
  ```sh
  HEX=$(printf '%s' "$CID" | tr -d -)
  NEV=$(control-agent-server api GET "/api/conversations/$CID/events/count" --field .)
  control-agent-server api GET "/api/file/download-trajectory/$CID" --expect 200 \
    --check-header content-disposition eq "attachment; filename=\"$HEX.zip\"" --raw-out "$F/traj.zip" --save F14.trajectory/zip
  python3 -c 'import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); h=sys.argv[2]; n=z.namelist(); assert h+"/meta.json" in n and h+"/base_state.json" in n, n; ev=[m for m in n if m.startswith(h+"/events/event-")]; assert len(ev) == int(sys.argv[3]), (len(ev), sys.argv[3]); s=json.loads(z.read(h+"/base_state.json")); assert s["agent"]["llm"]["api_key"] == "**********", "api_key not redacted"; assert not any(b"gAAAAA" in z.read(m) for m in n), "Fernet ciphertext in the zip"' "$F/traj.zip" "$HEX" "$NEV"
  grep -q '"api_key": *"gAAAAA' "$AGENT_SERVER_VERIFY_RUN/server/workspace/conversations/$HEX/base_state.json"
  control-agent-server api GET "/api/file/download-trajectory/$HEX" --expect 200 --raw-out "$F/traj-hex.zip"
  python3 -m zipfile -t "$F/traj-hex.zip" > /dev/null
  for i in 1 2 3 4 5; do test -z "$(find "$AGENT_SERVER_VERIFY_RUN/server/workspace/conversations" -maxdepth 1 -name '*.zip')" && break; sleep 0.2; done
  test -z "$(find "$AGENT_SERVER_VERIFY_RUN/server/workspace/conversations" -maxdepth 1 -name '*.zip')"
  ```
  The zip is `<hex>.zip` with `<hex>/meta.json`, `<hex>/base_state.json` and
  one `events/event-*.json` per event (the count matches `events/count`). On
  disk the LLM key is Fernet ciphertext (`gAAAAA...`); in the zip it is
  `**********` and no ciphertext remains anywhere. The hex form of the id
  works too, and the temp zip under the conversations directory is removed.
- **Trajectory errors (`F14.trajectory-errors`).**
  ```sh
  control-agent-server api GET /api/file/download-trajectory/00000000-0000-4000-8000-00000000f14a --expect 404 \
    --check detail eq 'Conversation not found' --save F14.trajectory-errors/unknown
  control-agent-server api GET /api/file/download-trajectory/qa-f14-not-a-uuid --expect 422
  ```
  404 for an unknown id, 422 for a malformed one.
- **Concurrent trajectory downloads (`F14.trajectory-concurrent`), known bug.**
  Up to five rounds of six parallel downloads of the same conversation (two
  browser tabs or a retry are enough in practice). The burst stops after the
  first round with a broken download, so the bug reproduces almost surely
  and passing needs 30 clean concurrent downloads.
  ```sh
  CC="$F/concurrent"
  mkdir -p "$CC"
  control-agent-server api GET "/api/file/download-trajectory/$CID" --expect 200 --raw-out "$CC/single.zip"
  python3 -m zipfile -t "$CC/single.zip" > /dev/null
  cat > "$CC/burst.sh" <<'SH'
  for r in 1 2 3 4 5; do
    for i in 1 2 3 4 5 6; do
      curl -sS -o "$CC/$r-$i.zip" -w '%{http_code}' -H "X-Session-API-Key: $SESSION_API_KEY" \
        "$AGENT_SERVER_URL/api/file/download-trajectory/$CID" > "$CC/$r-$i.status" 2> "$CC/$r-$i.err" &
    done
    wait
    for i in 1 2 3 4 5 6; do
      if [ "$(cat "$CC/$r-$i.status")" != 200 ] || ! python3 -m zipfile -t "$CC/$r-$i.zip" > /dev/null 2>&1; then
        echo "round $r: download $i is broken"; exit 0
      fi
    done
    echo "round $r: six valid zips"
  done
  SH
  control-agent-server exec --env CID="$CID" --env CC="$CC" --save F14.trajectory-concurrent/burst -- sh "$CC/burst.sh"
  test -s "$CC/1-6.status"
  for s in "$CC"/[1-5]-[1-6].status; do test "$(cat "$s")" = 200; test -s "${s%.status}.zip"; python3 -m zipfile -t "${s%.status}.zip" > /dev/null; done  # bug
  ```
  A single download is a valid zip. Expected: every concurrent download is
  200 with a valid zip, in all five rounds. Actual: every handler writes the
  same temp file `<conversations_path>/<hex>.zip` and unlinks it after
  sending, so some requests get 500 (`File at path ... does not exist`, or
  the unlink's `FileNotFoundError`) and some 200 responses are truncated,
  sometimes to no body at all, so curl writes no file (`curl: (18) transfer
  closed with ... bytes remaining`, `Response content longer than
  Content-Length` in the log). Usually the first round already has a broken
  download; the burst's output names it, and each `<round>-<n>.err` holds
  curl's error.
- **Conversation-scoped transfer (`F14.scoped-transfer`).** Read the file the
  agent wrote, then upload, download and create a directory inside the
  workspace.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/file/download" --query path="$WS/qa-f14-agent.txt" --expect 200 \
    --raw-out "$F/agent.txt" --save F14.scoped-transfer/agent-file
  grep -qx hi "$F/agent.txt"
  control-agent-server api POST "/api/conversations/$CID/file/upload" --query path="$WS/qa-f14-up/blob.bin" --file file="$F/blob.bin" \
    --expect 200 --check success eq true --save F14.scoped-transfer/upload
  control-agent-server api GET "/api/conversations/$CID/file/download" --query path="$WS/qa-f14-up/blob.bin" --expect 200 --raw-out "$F/scoped.bin"
  cmp "$F/blob.bin" "$F/scoped.bin"
  control-agent-server api GET /api/file/download --query path="$WS/qa-f14-up/blob.bin" --expect 200 --raw-out "$F/scoped-global.bin"
  cmp "$F/blob.bin" "$F/scoped-global.bin"
  control-agent-server api POST "/api/conversations/$CID/file/create_directory" --query path="$WS/qa-f14-dir/sub" --expect 200 --check success eq true
  test -d "$WS/qa-f14-dir/sub"
  control-agent-server api GET "/api/conversations/$CID/file/download" --query path="$WS/qa-f14-missing.txt" --expect 404 --check detail eq 'File not found'
  control-agent-server api GET "/api/conversations/$CID/file/download" --query path="$WS" --expect 400 --check detail eq 'Path is not a file'
  ```
  The scoped download returns the agent's `hi`; the scoped upload lands in the
  workspace (the global route reads the same bytes); the scoped
  `create_directory` creates the nested directory; inside the workspace the
  handler's own 404 and 400 apply.
- **Workspace path guard (`F14.scoped-path-guard`).** Address `CID`'s
  workspace through `CID2`'s scope in every way that should escape.
  ```sh
  control-agent-server api GET "/api/conversations/$CID2/file/download" --query path="$WS/qa-f14-agent.txt" --expect 422 \
    --check detail eq 'path must be inside the conversation workspace' --save F14.scoped-path-guard/other-workspace
  control-agent-server api GET "/api/conversations/$CID2/file/download" --query path="$WS2/../$(basename "$WS")/qa-f14-agent.txt" --expect 422
  ln -s "$WS" "$WS2/qa-f14-escape"
  control-agent-server api GET "/api/conversations/$CID2/file/download" --query path="$WS2/qa-f14-escape/qa-f14-agent.txt" --expect 422
  control-agent-server api GET "/api/conversations/$CID2/file/download" --query path="${WS2}-sibling/x.txt" --expect 422
  control-agent-server api GET "/api/conversations/$CID2/file/download" --query path=qa-f14-agent.txt --expect 422
  control-agent-server api GET "/api/conversations/$CID/file/download" --query path="$WS/qa-f14-up/../qa-f14-agent.txt" --expect 200
  control-agent-server api POST "/api/conversations/$CID2/file/upload" --query path="$WS/qa-f14-intruder.txt" --file file="$F/v2.txt" --expect 422
  control-agent-server api POST "/api/conversations/$CID2/file/create_directory" --query path="$WS/qa-f14-intruder-dir" --expect 422
  test ! -e "$WS/qa-f14-intruder.txt"
  test ! -e "$WS/qa-f14-intruder-dir"
  control-agent-server api GET /api/file/download --query path="$WS2/qa-f14-escape/qa-f14-agent.txt" --expect 200 \
    --save F14.scoped-path-guard/global-same-path
  ```
  Another workspace, `..` traversal, a symlink inside `WS2` that points at
  `WS`, a sibling directory sharing `WS2`'s prefix, and a relative path are all
  `422 path must be inside the conversation workspace` (the dependency runs
  before the handler, so a relative path is 422 here, not 400); the refused
  upload and directory never appear. The guard resolves the path rather than
  refusing every `..`: `$WS/qa-f14-up/../qa-f14-agent.txt` through `CID`'s
  own scope is 200. The global route serves the same symlinked path with 200.
- **Unknown or malformed conversation (`F14.scoped-conversation-errors`).**
  ```sh
  control-agent-server api GET /api/conversations/00000000-0000-4000-8000-00000000f14b/file/download --query path="$WS/qa-f14-agent.txt" \
    --expect 404 --check detail contains 'Conversation not found' --save F14.scoped-conversation-errors/unknown
  control-agent-server api POST /api/conversations/00000000-0000-4000-8000-00000000f14b/file/create_directory --query path="$F/unknown-scope" --expect 404
  test ! -e "$F/unknown-scope"
  control-agent-server api GET /api/conversations/qa-f14-not-a-uuid/file/download --query path="$WS/qa-f14-agent.txt" --expect 422
  control-agent-server api GET "/api/conversations/$CID/file/download" --expect 422
  ```
  An unknown runtime conversation is 404 and creates nothing; a malformed id
  and a missing `path` are 422.
- **Scoped trajectory and archive (`F14.scoped-trajectory-archive`).**
  ```sh
  control-agent-server api GET "/api/conversations/$CID/file/download-trajectory/$CID" --expect 200 \
    --raw-out "$F/scoped-traj.zip" --save F14.scoped-trajectory-archive/trajectory
  python3 -m zipfile -t "$F/scoped-traj.zip" > /dev/null
  control-agent-server api GET "/api/conversations/$CID/file/download-trajectory/$CID2" --expect 422 \
    --check detail eq 'Trajectory must belong to the selected conversation'
  control-agent-server api GET "/api/conversations/$CID/file/download-trajectory/qa-f14-not-a-uuid" --expect 422 \
    --check detail eq 'Invalid trajectory conversation id'
  control-agent-server api GET "/api/conversations/$CID/file/archive" --query path="$WS" --expect 200 \
    --raw-out "$F/ws.patch" --save F14.scoped-trajectory-archive/git-delta
  grep -q '^diff --git a/qa-f14-agent.txt b/qa-f14-agent.txt' "$F/ws.patch"
  control-agent-server api GET "/api/conversations/$CID/file/archive" --query path="$WS" --query format=tar.gz --expect 200 --raw-out "$F/ws.tgz"
  tar tzf "$F/ws.tgz" > "$F/ws.list"
  grep -qx "$(basename "$WS")/qa-f14-agent.txt" "$F/ws.list"
  grep -qx "$(basename "$WS")/qa-f14-up/blob.bin" "$F/ws.list"
  control-agent-server api GET "/api/conversations/$CID/file/archive" --query path="$REPO" --expect 422 \
    --check detail eq 'path must be inside the conversation workspace'
  control-agent-server api GET "/api/conversations/$CID2/file/archive" --query path="$WS" --query format=tar.gz --expect 422
  ```
  The conversation's own trajectory downloads as a valid zip; `CID2`'s id
  through `CID`'s scope and a malformed id are 422. The server `git init`s
  every conversation workspace without a commit, so the scoped `git-delta`
  is against the empty tree and lists the agent's `qa-f14-agent.txt` as a new
  file; the `tar.gz` holds it and the uploaded blob. Archiving the fixture
  repo or another workspace through this scope is 422.
- **SDK workspace (`F14.sdk-workspace`).** `RemoteWorkspace.file_upload` and
  `file_download`, first on the host (`/api/file/...`), then scoped to `CID`
  (`runtime_conversation_id`, `/api/conversations/{id}/file/...`).
  ```sh
  cat > "$F/sdk_files.py" <<'PY'
  import os
  import sys
  from pathlib import Path
  from uuid import UUID

  from openhands.sdk.workspace.remote.base import RemoteWorkspace

  fixtures, ws, cid = Path(sys.argv[1]), Path(sys.argv[2]), UUID(sys.argv[3])
  host, key = os.environ["AGENT_SERVER_URL"], os.environ["SESSION_API_KEY"]
  blob = (fixtures / "blob.bin").read_bytes()
  plain = RemoteWorkspace(host=host, api_key=key, working_dir=str(fixtures))
  scoped = RemoteWorkspace(host=host, api_key=key, working_dir=str(ws), runtime_conversation_id=cid)
  for name, w, remote in (
      ("plain", plain, fixtures / "sdk" / "blob.bin"),
      ("scoped", scoped, ws / "qa-f14-sdk" / "blob.bin"),
  ):
      up = w.file_upload(fixtures / "blob.bin", remote)
      assert up.success and up.error is None, (name, up)
      assert remote.read_bytes() == blob, name
      local = fixtures / f"sdk-{name}.out"
      down = w.file_download(remote, local)
      assert down.success and down.file_size == len(blob), (name, down)
      assert local.read_bytes() == blob, name
  refused = scoped.file_upload(fixtures / "v2.txt", fixtures / "sdk-outside.txt")
  assert not refused.success and "422" in (refused.error or ""), refused
  assert not (fixtures / "sdk-outside.txt").exists()
  print("SDK_FILES_OK")
  PY
  control-agent-server exec --expect-output SDK_FILES_OK --save F14.sdk-workspace/program -- .venv/bin/python "$F/sdk_files.py" "$F" "$WS" "$CID"
  ```
  Both workspaces upload the blob (the file on disk matches) and download it
  back byte for byte; the scoped workspace's upload outside `WS` returns
  `success=False` with the 422 in `error` (the SDK swallows HTTP errors into
  the result) and writes nothing.
- **Restart (`F14.restart`).** Restart the server and read everything back.
  ```sh
  control-agent-server restart
  control-agent-server api GET /api/file/download --query path="$F/xfer/a/b/blob.bin" --expect 200 --raw-out "$F/after-restart.out"
  cmp "$F/v2.txt" "$F/after-restart.out"
  control-agent-server api GET "/api/conversations/$CID/file/download" --query path="$WS/qa-f14-up/blob.bin" --expect 200 \
    --raw-out "$F/after-restart-scoped.bin" --save F14.restart/scoped-download
  cmp "$F/blob.bin" "$F/after-restart-scoped.bin"
  control-agent-server api GET "/api/conversations/$CID/file/download-trajectory/$CID" --expect 200 \
    --raw-out "$F/after-restart.zip" --save F14.restart/trajectory
  python3 -c 'import sys,zipfile; n=zipfile.ZipFile(sys.argv[1]).namelist(); ev=[m for m in n if m.startswith(sys.argv[2]+"/events/event-")]; assert len(ev) >= int(sys.argv[3]), (len(ev), sys.argv[3])' "$F/after-restart.zip" "$HEX" "$NEV"
  control-agent-server api GET "/api/conversations/$CID2/file/download" --query path="$WS/qa-f14-agent.txt" --expect 422
  control-agent-server doctor
  ```
  The overwritten global file, the scoped upload and the trajectory (at least
  as many events as before) are all served after the restart, the restored
  conversation still binds its scope (the guard still refuses `WS` through
  `CID2`), and `doctor` is ok.
- **Init-time conversations path (`F14.trajectory-init-path`), known bug.**
  A second server starts dormant;
  `POST /api/init` (`--auth init` sends that run's secret key in
  `X-Init-API-Key`) moves its conversations to a fixture directory. The trap
  stops the second server even when an assertion fails; its evidence stays in
  its own run directory.
  ```sh
  B=$(control-agent-server launch --new --name f14-init --deferred-init --print-run)
  trap 'control-agent-server stop --run "$B" > /dev/null' EXIT
  BP="$B/fixtures/qa-f14-conversations"
  mkdir -p "$BP"
  control-agent-server api POST /api/init --run "$B" --auth init \
    --json "{\"conversations_path\": \"$BP\"}" --expect 200 --check state eq ready
  control-agent-server llm preset deepseek --run "$B"
  BCID=$(control-agent-server conversation start --run "$B" --tools file_editor --no-autotitle --print-id)
  test -d "$BP/$(printf '%s' "$BCID" | tr -d -)"
  control-agent-server api GET "/api/conversations/$BCID" --run "$B" --expect 200 --check id eq "$BCID"
  control-agent-server api GET "/api/file/download-trajectory/$BCID" --run "$B" --expect 200 --save F14.trajectory-init-path/global  # bug
  control-agent-server api GET "/api/conversations/$BCID/file/download-trajectory/$BCID" --run "$B" --expect 200 \
    --save F14.trajectory-init-path/scoped  # bug
  ```
  The conversation is persisted under the init-time path and `GET` finds it.
  Expected: both trajectory routes return the zip (each alone encodes the
  bug). Actual: both answer `404 Conversation not found`:
  `download_trajectory` reads `get_default_config().conversations_path` (the
  launch-time default) instead of the config `POST /api/init` installed on
  `app.state`.

## Gotchas

- The upload destination is the `path` query parameter, not a form field, and
  the multipart field must be named `file`. The response is only
  `{"success": true}`; the SDK and TypeScript consumers read a `file_size`
  that is never sent (`F14.upload-size`).
- Upload truncates the target before streaming, so a failed upload can leave
  a partial file; it is not atomic (`F14.upload-atomic`). That recipe lowers
  the second server's `RLIMIT_FSIZE` with `prlimit` (util-linux, Linux only);
  Python ignores `SIGXFSZ`, so the write fails with `EFBIG` instead of
  killing the server. A body under 1 MB stays in Starlette's in-memory
  spool, so the limit hits the handler's write, not the multipart parser.
- The global routes accept any absolute path the server user can reach
  (including `/etc`); only the scoped routes confine paths. Keep fixtures under
  the run directory or `/tmp/qa-f14-*`.
- Scoped path checks happen in a router dependency: relative and outside paths
  are 422 there, while the same relative path on a global route is 400.
  `home` and `search_subdirs` have no scoped variant.
- `search_subdirs`' cursor is the lowercase name of the next entry; an unknown
  `page_id` silently restarts at the first page, and case-only twins loop
  (`F14.subdirs-case-pagination`). `home` lists at most 50 favorites, with no
  pagination. 403 (`Permission denied`) cannot be reproduced as root.
- `F14.home` counts favorites exactly: it relies on the launcher's private
  `HOME` holding only hidden entries (`.config`, `.gitconfig`, `.openhands`).
- `archive` resolves the repository to archive: if `path` is not a repository
  but holds exactly one up to three levels down (skipping hidden and vendored
  directories), both formats archive only that repository, so files beside it
  are not in the `tar.gz` even though the `format` description says "the
  entire directory" (`F14.archive-format-doc`). Callers that need everything
  must pass a path with zero or several repositories, or the repository
  itself.
- `git-delta` honors the repo's `.gitignore` (`git add -A` on a scratch index);
  `tar.gz` does not and only applies the exclude globs. The automatic base is
  `origin/<branch>`, then `origin/<default branch>` or the merge base with it,
  then the empty tree, so a remote-less repository gives a full-repository
  patch unless `base_ref=HEAD`; a detached HEAD uses `HEAD`
  (`F14.archive-auto-base`). Either way `X-Archive-Base-Ref` is `auto`.
- The repository identity headers (`X-Archive-Repo-Root`, `-Branch`,
  `-Head-Commit`, `-Repo-Remote`) are sent only when the archived directory
  itself holds `.git`; archiving a subdirectory of a repository returns the
  `X-Archive-Base-*` headers but no identity headers.
- Header values such as `X-Archive-Repo-Root` are percent-encoded.
  `api --check-header` asserts on any response header (the saved exchange
  keeps only a few unless `--all-headers` is given), and repeated `--query`
  flags keep every value, so `exclude=a&exclude=b` is two `--query exclude=`
  flags.
- git discovery walks up from `path`. If any ancestor of the runs home is a
  repository (a conversation whose workspace was `/tmp` leaves `/tmp/.git`,
  because the server `git init`s workspaces that are not inside a repo), a
  plain directory is "inside a repo": `git-delta` of it answers 200 with a
  patch relative to that ancestor, and the server skips `git init` for new
  workspaces, so scoped patches carry long ancestor-relative paths. The
  preconditions therefore restart the run with
  `GIT_CEILING_DIRECTORIES=<run>` and check that `CID`'s workspace got its own
  `.git`; drop that line only on a machine with no repository above the runs
  home.
- The archive's temp file is created in `path`'s parent directory (the system
  temp directory when the parent is not writable) and removed after the
  response; for a scoped archive of the workspace root it lives outside the
  workspace.
- Every conversation workspace is `git init`ed by the server on start (no
  commit), so scoped `git-delta` archives of a workspace are always against
  the empty tree.
- `download-trajectory` only checks that `<conversations_path>/<hex>/` exists,
  not that the conversation is loaded; it includes lease files and every
  event file, skips a top-level `acp/` directory, and redacts JSON/JSONL
  credential fields plus any Fernet token. It writes a fixed temp name
  (`F14.trajectory-concurrent`) and ignores an init-time `conversations_path`
  (`F14.trajectory-init-path`).
- No file route emits conversation events or WebSocket frames; their second
  views are downloads, listings, archives and the disk.
- On a deferred-init server every route here is 503 until `POST /api/init`
  (the deferred-init family owns that gate).
- The agent gets only `file_editor` (`conversation start --tools file_editor`),
  so the family needs no `tmux`; the model's one job is to write
  `qa-f14-agent.txt`, which the scoped download and archives then read.
