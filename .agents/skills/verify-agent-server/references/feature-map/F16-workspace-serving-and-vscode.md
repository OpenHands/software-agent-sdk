# Workspace file serving and VS Code endpoints

How a browser or an agent consumer looks at what the agent produced. Every
conversation's workspace directory is a plain static site under
`/api/conversations/{conversation_id}/workspace/`: a file comes back inline
with a `Content-Type` guessed from its extension (plus `ETag`, `Last-Modified`
and byte ranges), a directory serves its `index.html` or 404, and anything that
resolves outside the workspace (encoded `..`, absolute paths, symlinks) is 400.
These two routes sit in their own auth group: they accept the
`X-Session-API-Key` header or the `oh_workspace_session_key` cookie, so Agent
Canvas can embed agent-made HTML, images and PDFs in `<iframe>` and `<img>`.
The VS Code routes report whether the bundled OpenVSCode Server is enabled and
running, and build its URL (`tkn` is the first session key, `folder` the
workspace; the conversation-scoped variant defaults the folder to the
conversation's workspace and refuses folders outside it). They are header-only.

Source: `openhands-agent-server/openhands/agent_server/workspace_router.py`, `openhands-agent-server/openhands/agent_server/vscode_router.py`, `openhands-agent-server/openhands/agent_server/vscode_service.py`, `openhands-agent-server/openhands/agent_server/runtime_router.py`, `openhands-agent-server/openhands/agent_server/dependencies.py`, `openhands-agent-server/openhands/agent_server/docker_runtime/routers.py`, `clients/typescript/src/client/vscode-client.ts`, `clients/typescript/src/workspace/remote-workspace.ts`

Needs: `llm`, `git`

Routes: `GET /api/conversations/{conversation_id}/workspace`,
`GET /api/conversations/{conversation_id}/workspace/{file_path:path}`,
`GET /api/conversations/{runtime_conversation_id}/vscode/url`,
`GET /api/conversations/{runtime_conversation_id}/vscode/status`,
`GET /api/vscode/url`, `GET /api/vscode/status`

## Sub-features

- `F16.root-index`: `GET .../workspace` and `.../workspace/` serve the workspace root's `index.html` as `text/html; charset=utf-8`, inline (no `Content-Disposition`).
- `F16.file-bytes`: a nested file comes back byte for byte with `Content-Length`, `ETag`, `Last-Modified` and `Accept-Ranges: bytes`; percent-encoded names (spaces, non-ASCII) resolve.
- `F16.content-types`: the `Content-Type` follows the extension (`.css`, `.json`, `.svg`, `.png`, `.txt`), and an unknown extension is `application/octet-stream`.
- `F16.dir-index`: a subdirectory serves its own `index.html` with or without the trailing slash; a directory without one is 404 `No index.html in directory` (the workspace root too), and a missing file is 404 `File not found`.
- `F16.range`: a `Range: bytes=0-5` request answers 206 with `Content-Range` and only those bytes.
- `F16.live-content`: a file changed on disk is served fresh at once, with a new `ETag` (no server-side cache).
- `F16.traversal-rejected`: encoded `..` segments and absolute paths (`//etc/hostname`, `%2Fetc%2Fhostname`) answer 400 `Path is outside the workspace`; a `..` that stays inside is served.
- `F16.symlink-escape`: a symlinked file or directory that resolves outside the workspace is 400; a symlink to a file inside it is served.
- `F16.unknown-conversation`: an unknown conversation id is 404 `Conversation not found: <id>` on both routes, a non-UUID id is 422, and the dashless hex form of a real id works.
- `F16.workspace-dir-missing`: a conversation whose workspace directory was removed answers 404 `Workspace directory does not exist`, and serves again once the directory is back.
- `F16.deleted-conversation`: after `DELETE /api/conversations/{id}` its workspace is no longer served (404 `Conversation not found`), although the files stay on disk.
- `F16.worktree-served`: a `worktree: true` conversation is served from its own git worktree: files written there and the committed content, not the source checkout's uncommitted edit or untracked file.
- `F16.auth`: without a key or with a wrong one both routes are 401 (also for an unknown conversation); the header works, and so does the workspace cookie alone, but not a cookie with a wrong value.
- `F16.agent-artifact`: an HTML file the agent writes with the file editor is served at the conversation's workspace URL, and the events socket shows the observation that created it.
- `F16.restart`: after a server restart the same conversation's workspace is served again, with the header and with the cookie minted before the restart.
- `F16.vscode-disabled`: with VS Code off (the launcher's default), both status routes answer `{"running": false, "enabled": false, "message": "VSCode is disabled in configuration"}` and both URL routes 503.
- `F16.vscode-scoped-guards`: the conversation-scoped VS Code routes answer 404 for an unknown conversation, 422 for a non-UUID id, and 422 `workspace_dir must be inside the conversation workspace` for a relative or outside folder, before looking at VS Code.
- `F16.vscode-auth`: all four VS Code routes need the header (401 otherwise), and the workspace cookie, whose path covers the scoped routes, does not open them.
- `F16.vscode-enabled-status`: with `enable_vscode` on and VS Code unable to start (its port taken, or no binary), both status routes answer `{"running": false, "enabled": true}` and the server keeps serving.
- `F16.vscode-url-shape`: the URL is `http://localhost:<vscode_port>/?tkn=<first session key>&folder=workspace`; `base_url` replaces the origin, `workspace_dir` sets the folder, the scoped route defaults the folder to the conversation's `workspace.working_dir`, and `vscode_base_path` is inserted after the origin.
- `F16.vscode-url-not-running`: a VS Code that is not running must not be advertised: `url` is null while `running` is false (known bug: a keyed server returns a URL carrying the session key).
- `F16.vscode-url-no-key`: a server without session keys, with VS Code enabled but not running (no binary), answers `{"url": null}` on both URL routes.
- `F16.vscode-url-after-init`: a deferred-init server that receives its session key through `POST /api/init`, with VS Code enabled but not running (no binary), answers `{"url": null}`.
- `F16.vscode-folder-encoding`: the URL's `folder` parameter decodes back to the workspace path even when the path contains `&` or `#` (known bug: the value is not URL-encoded).
- `F16.docker-root-cookie`: in Docker runtime mode the slashless workspace root accepts the workspace cookie like the slashed form does (known bug: it falls into the header-only proxy and answers 401).
- `F16.vscode-running`: with the OpenVSCode Server binary installed and its port free, status reports `running: true` and the URL opens the editor (blocked on this machine: no binary).

## How to get to it (agent POV)

- REST: `GET /api/conversations/{conversation_id}/workspace` (the root's
  `index.html`) and `GET /api/conversations/{conversation_id}/workspace/{file_path}`
  (any file, or a directory's `index.html`). Auth: `X-Session-API-Key`, or the
  `oh_workspace_session_key` cookie minted by `POST /api/auth/workspace-session`
  (that route and the cookie's attributes belong to the auth family, F02; here
  the cookie is only the credential). `api --jar` plays the browser.
- REST: `GET /api/vscode/status` and `GET /api/vscode/url?base_url=&workspace_dir=`
  (global; `workspace_dir` defaults to the relative `workspace` and is not
  checked), and the conversation-scoped
  `GET /api/conversations/{runtime_conversation_id}/vscode/status` and
  `.../vscode/url` (the folder defaults to the conversation's
  `workspace.working_dir`; an explicit `workspace_dir` must be absolute and
  inside it). Header only.
- TypeScript client: `RemoteWorkspace.startWorkspaceSession(conversationId)`
  mints the cookie and returns `${host}/api/conversations/{id}/workspace/`
  (always with the trailing slash) for iframes;
  `deleteWorkspaceSession()` clears it. `VSCodeClient.getUrl({baseUrl, workspaceDir})`
  and `getStatus()` (also `OpenHandsClient.vscode`) call the global routes, or
  the conversation-scoped ones when constructed with `conversationId` and the
  server advertises `conversation_runtime_routes_v1`.
- SDK (Python): no wrapper for these routes; `RemoteWorkspace` reads files
  through the file routes (F14).
- Configuration: `enable_vscode` (`OH_ENABLE_VSCODE`, default true in the
  server, off in `launch` unless `--vscode`), `vscode_port`
  (`OH_VSCODE_PORT`, default 8001; `launch` picks a free one),
  `vscode_base_path` (`OH_VSCODE_BASE_PATH`), and the session keys (the first
  is the VS Code token; on a deferred-init server they arrive with
  `POST /api/init`, F03). `OH_CONVERSATION_RUNTIME=docker` swaps the workspace
  file route for a proxy into the conversation's container. A conversation
  started with `worktree: true` (F04) is served from its worktree.
- Agent Canvas embeds workspace files in iframes after minting the cookie, and
  opens VS Code from the URL route (context only).
- Recipes drive every route with `api`, use `api --jar` for the cookie,
  `restart --config-json` to turn VS Code on, an `http-sink` fixture to hold the
  VS Code port, a model conversation plus `ws start`/`ws stop` for the agent's
  artifact, a `git-repo` fixture for the worktree, and second runs
  (`--no-auth --vscode`, `--no-auth --deferred-init --vscode`, Docker runtime)
  for the other modes.

## Driving it with control-agent-server

Preconditions:

- A run is live and exported (`launch --new`, no extra flags), `doctor` is ok,
  `$DEEPSEEK_API_KEY` is set, and `git`, `jq` and `python3` are on `PATH`.
- The block below activates `deepseek-flash` (only `F16.agent-artifact` runs
  the model) and builds the run-owned workspace `$F/ws`: an `index.html`, a
  subdirectory with its own `index.html`, a directory without one, files of
  several types, a name with a space and a non-ASCII letter, and symlinks to a
  file inside, a file outside and a directory outside the workspace. `CID` is a
  conversation on `$F/ws` that never runs (no tools, so no `tmux`), `WS` its
  `workspace.working_dir` as the server reports it, and `BARE` a second one on
  a workspace without `index.html`.
  ```sh
  control-agent-server llm preset deepseek
  F="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f16"
  mkdir -p "$F/ws/sub/docs" "$F/ws/empty-dir" "$F/bare" "$F/outside-dir"
  printf '<h1>qa-f16-root</h1>\n' > "$F/ws/index.html"
  printf '<p>qa-f16-sub</p>\n' > "$F/ws/sub/index.html"
  printf 'qa-f16 notes v1\n' > "$F/ws/sub/docs/notes.txt"
  printf '{"qa": "f16"}\n' > "$F/ws/data.json"
  printf '<svg xmlns="http://www.w3.org/2000/svg"/>\n' > "$F/ws/pic.svg"
  printf 'body{color:red}\n' > "$F/ws/style.css"
  head -c 4096 /dev/urandom > "$F/ws/img.png"
  printf 'qa-f16-blob' > "$F/ws/blob.qaunknown"
  printf 'qa-f16 spaced\n' > "$F/ws/my file é.txt"
  printf 'qa-f16-outside\n' > "$F/outside.txt"
  printf 'qa-f16-outside-dir\n' > "$F/outside-dir/file.txt"
  ln -sfn data.json "$F/ws/inside-link.json"
  ln -sfn ../outside.txt "$F/ws/escape-link.txt"
  ln -sfn ../outside-dir "$F/ws/dir-link"
  printf 'qa-f16-bare\n' > "$F/bare/readme.txt"
  CID=$(control-agent-server conversation start --no-run --tools none --no-autotitle --workspace "$F/ws" --print-id)
  WS=$(control-agent-server api GET "/api/conversations/$CID" --field workspace.working_dir)
  BARE=$(control-agent-server conversation start --no-run --tools none --no-autotitle --workspace "$F/bare" --print-id)
  test -n "$WS"
  ```

- **Root index (`F16.root-index`).** Open the workspace root with and
  without the trailing slash.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace" --expect 200 --check . contains qa-f16-root \
    --check-header content-type eq 'text/html; charset=utf-8' --check-header content-disposition missing --save F16.root-index/root
  control-agent-server api GET "/api/conversations/$CID/workspace/" --expect 200 --check . contains qa-f16-root
  ```
  Both return `<h1>qa-f16-root</h1>` as `text/html; charset=utf-8` with no
  `Content-Disposition`, so a browser renders it in an iframe.
- **File bytes and headers (`F16.file-bytes`).** Download a binary file and
  compare it with the file on disk, then a percent-encoded name.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace/img.png" --expect 200 --raw-out "$F/img.out" \
    --check-header content-length eq 4096 --check-header etag exists --check-header last-modified exists \
    --check-header accept-ranges eq bytes --save F16.file-bytes/png
  cmp "$F/img.out" "$F/ws/img.png"
  control-agent-server api GET "/api/conversations/$CID/workspace/sub/docs/notes.txt" --expect 200 --check . contains 'qa-f16 notes v1' --check-header content-length eq 16
  control-agent-server api GET "/api/conversations/$CID/workspace/my%20file%20%C3%A9.txt" --expect 200 --check . contains 'qa-f16 spaced' --save F16.file-bytes/encoded-name
  ```
  The 4096 bytes are identical to the file; the nested text file and the
  encoded name `my file é.txt` both resolve.
- **Content types (`F16.content-types`).** One request per extension.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace/style.css" --expect 200 --check-header content-type eq 'text/css; charset=utf-8' --save F16.content-types/css
  control-agent-server api GET "/api/conversations/$CID/workspace/data.json" --expect 200 --check-header content-type eq application/json --check qa eq f16
  control-agent-server api GET "/api/conversations/$CID/workspace/pic.svg" --expect 200 --check-header content-type eq image/svg+xml --save F16.content-types/svg
  control-agent-server api GET "/api/conversations/$CID/workspace/img.png" --expect 200 --check-header content-type eq image/png \
    --check-header content-disposition missing
  control-agent-server api GET "/api/conversations/$CID/workspace/sub/docs/notes.txt" --expect 200 --check-header content-type eq 'text/plain; charset=utf-8'
  control-agent-server api GET "/api/conversations/$CID/workspace/blob.qaunknown" --expect 200 --check-header content-type eq application/octet-stream \
    --check-header content-disposition missing --save F16.content-types/unknown
  ```
  Each type matches its extension; the unknown extension falls back to
  `application/octet-stream`. Even the image and the unknown type carry no
  `Content-Disposition`, so nothing is forced into a download.
- **Directory index and missing files (`F16.dir-index`).** A directory with
  an `index.html`, one without, a workspace root without one, and a missing
  file.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace/sub" --expect 200 --check . contains qa-f16-sub --save F16.dir-index/sub
  control-agent-server api GET "/api/conversations/$CID/workspace/sub/" --expect 200 --check . contains qa-f16-sub
  control-agent-server api GET "/api/conversations/$CID/workspace/empty-dir" --expect 404 --check detail eq 'No index.html in directory' --save F16.dir-index/no-index
  control-agent-server api GET "/api/conversations/$BARE/workspace" --expect 404 --check detail eq 'No index.html in directory'
  control-agent-server api GET "/api/conversations/$BARE/workspace/readme.txt" --expect 200 --check . contains qa-f16-bare
  control-agent-server api GET "/api/conversations/$CID/workspace/qa-f16-missing.txt" --expect 404 --check detail eq 'File not found' --save F16.dir-index/missing
  ```
  `sub` serves `<p>qa-f16-sub</p>` either way; `empty-dir` and the bare
  workspace root are 404 `No index.html in directory` (the bare workspace's
  files are still served), and a missing file is 404 `File not found`.
- **Byte ranges (`F16.range`).** Ask for the first six bytes.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace/sub/docs/notes.txt" --header 'Range: bytes=0-5' --expect 206 \
    --check . eq qa-f16 --check-header content-range eq 'bytes 0-5/16' --save F16.range/first-six
  ```
  The answer is 206 `Partial Content` with `Content-Range: bytes 0-5/16` and
  the body `qa-f16`, which is what PDF and video viewers rely on.
- **Live content (`F16.live-content`).** Serve a file, change it on disk,
  serve it again.
  ```sh
  printf 'qa-f16-live v1\n' > "$F/ws/live.txt"
  E1=$(control-agent-server api GET "/api/conversations/$CID/workspace/live.txt" --expect 200 --check . contains 'qa-f16-live v1' --print-header etag)
  printf 'qa-f16-live v2 (longer)\n' > "$F/ws/live.txt"
  control-agent-server api GET "/api/conversations/$CID/workspace/live.txt" --expect 200 --check . contains 'qa-f16-live v2' \
    --check-header etag ne "$E1" --save F16.live-content/after-change
  ```
  The second request returns the new content and a different `ETag`.
- **Traversal (`F16.traversal-rejected`).** Encoded `..` segments (the CLI
  sends them as written; a literal `../` would be normalized away by the
  client) and absolute paths, then a `..` that stays inside.
  ```sh
  test -f "$F/outside.txt"
  control-agent-server api GET "/api/conversations/$CID/workspace/%2e%2e/outside.txt" --expect 400 --check detail eq 'Path is outside the workspace' --save F16.traversal-rejected/dotdot
  control-agent-server api GET "/api/conversations/$CID/workspace/..%2Foutside.txt" --expect 400 --check detail eq 'Path is outside the workspace'
  control-agent-server api GET "/api/conversations/$CID/workspace/sub/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/hostname" --expect 400
  control-agent-server api GET "/api/conversations/$CID/workspace//etc/hostname" --expect 400 --check detail eq 'Path is outside the workspace' --save F16.traversal-rejected/absolute
  control-agent-server api GET "/api/conversations/$CID/workspace/%2Fetc%2Fhostname" --expect 400 --check detail eq 'Path is outside the workspace'
  control-agent-server api GET "/api/conversations/$CID/workspace/sub/%2e%2e/data.json" --expect 200 --check qa eq f16 --save F16.traversal-rejected/inside-dotdot
  ```
  Every escape is 400 `Path is outside the workspace` and no file content
  comes back; `sub/../data.json` resolves inside and is served.
- **Symlinks (`F16.symlink-escape`).** A file symlink that points outside, a
  directory symlink that points outside, and one that stays inside.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace/escape-link.txt" --expect 400 --check detail eq 'Path is outside the workspace' --save F16.symlink-escape/file-outside
  control-agent-server api GET "/api/conversations/$CID/workspace/dir-link/file.txt" --expect 400 --check detail eq 'Path is outside the workspace' --save F16.symlink-escape/dir-outside
  control-agent-server api GET "/api/conversations/$CID/workspace/inside-link.json" --expect 200 --check qa eq f16 --save F16.symlink-escape/inside
  ```
  Both escaping links are 400 (the path is resolved before the check); the
  link to `data.json` is served.
- **Unknown and malformed ids (`F16.unknown-conversation`).** A random UUID, a
  non-UUID, and the dashless form of `CID`.
  ```sh
  Z=00000000-0000-4000-8000-0000000000f6
  control-agent-server api GET "/api/conversations/$Z/workspace" --expect 404 --check detail eq "Conversation not found: $Z" --save F16.unknown-conversation/root
  control-agent-server api GET "/api/conversations/$Z/workspace/index.html" --expect 404 --check detail eq "Conversation not found: $Z"
  control-agent-server api GET /api/conversations/not-a-uuid/workspace --expect 422 --check detail.0.loc.1 eq conversation_id --save F16.unknown-conversation/not-uuid
  control-agent-server api GET /api/conversations/not-a-uuid/workspace/index.html --expect 422
  control-agent-server api GET "/api/conversations/${CID//-/}/workspace/data.json" --expect 200 --check qa eq f16
  ```
  Unknown is 404 with the id in the detail, a non-UUID is 422 on the
  `conversation_id` path parameter, and the 32-character hex id serves the
  same workspace.
- **Workspace directory gone (`F16.workspace-dir-missing`).** Remove a
  conversation's workspace directory, then put it back.
  ```sh
  mkdir -p "$F/gone"
  printf '<p>qa-f16-gone</p>\n' > "$F/gone/index.html"
  GID=$(control-agent-server conversation start --no-run --tools none --no-autotitle --workspace "$F/gone" --print-id)
  control-agent-server api GET "/api/conversations/$GID/workspace" --expect 200 --check . contains qa-f16-gone
  rm -rf "$F/gone"
  control-agent-server api GET "/api/conversations/$GID/workspace" --expect 404 --check detail eq 'Workspace directory does not exist' --save F16.workspace-dir-missing/gone
  control-agent-server api GET "/api/conversations/$GID/workspace/index.html" --expect 404 --check detail eq 'Workspace directory does not exist'
  mkdir -p "$F/gone"
  printf '<p>qa-f16-back</p>\n' > "$F/gone/index.html"
  control-agent-server api GET "/api/conversations/$GID/workspace" --expect 200 --check . contains qa-f16-back --save F16.workspace-dir-missing/back
  ```
  While the directory is missing both routes are 404
  `Workspace directory does not exist`; the conversation survives, and the
  recreated directory is served at once.
- **Deleted conversation (`F16.deleted-conversation`).** Delete the
  conversation from the previous bullet and request its workspace.
  ```sh
  control-agent-server api DELETE "/api/conversations/$GID" --expect 200
  control-agent-server api GET "/api/conversations/$GID" --expect 404
  control-agent-server api GET "/api/conversations/$GID/workspace" --expect 404 --check detail eq "Conversation not found: $GID" --save F16.deleted-conversation/root
  control-agent-server api GET "/api/conversations/$GID/workspace/index.html" --expect 404
  test -f "$F/gone/index.html"
  ```
  The workspace URL stops serving with the conversation (404), while the
  user-provided directory and its file stay on disk.
- **Worktree conversation (`F16.worktree-served`).** Start a `worktree: true`
  conversation on a git fixture whose checkout has an uncommitted edit to
  `README.md` and an untracked `notes.txt`, then write a file into the
  worktree.
  ```sh
  REPO=$(control-agent-server fixture git-repo --name qa-f16-repo --print-path)
  WT=$(control-agent-server conversation start --no-run --tools none --no-autotitle --workspace "$REPO" --body-json '{"worktree": true}' --print-id)
  WTD=$(control-agent-server api GET "/api/conversations/$WT" --field workspace.working_dir)
  test "$WTD" != "$REPO" && test -f "$WTD/README.md" && test -f "$REPO/notes.txt"
  printf '<p>qa-f16-wt</p>\n' > "$WTD/qa-f16-wt.html"
  control-agent-server api GET "/api/conversations/$WT/workspace/qa-f16-wt.html" --expect 200 --check . contains qa-f16-wt --save F16.worktree-served/worktree-file
  git -C "$REPO" show HEAD:README.md > "$F/readme-head.md"
  if cmp -s "$F/readme-head.md" "$REPO/README.md"; then false; fi
  control-agent-server api GET "/api/conversations/$WT/workspace/README.md" --expect 200 --raw-out "$F/readme-served.md" --save F16.worktree-served/committed
  cmp "$F/readme-served.md" "$F/readme-head.md"
  control-agent-server api GET "/api/conversations/$WT/workspace/notes.txt" --expect 404 --check detail eq 'File not found' --save F16.worktree-served/untracked
  ```
  The working directory is the worktree, not the fixture; the file written
  there is served; `README.md` comes back as committed (the checkout's edit
  differs and is not served), and the untracked `notes.txt` is 404 although
  it exists in the source checkout.
- **Header or cookie (`F16.auth`).** No key, a wrong key, the header, then
  the cookie alone in a browser-like jar, and a cookie with a wrong value.
  ```sh
  control-agent-server api GET "/api/conversations/$CID/workspace" --auth none --expect 401 --check detail eq Unauthorized --save F16.auth/no-key
  control-agent-server api GET "/api/conversations/$CID/workspace/data.json" --auth bad --expect 401 --check detail eq Unauthorized
  control-agent-server api GET /api/conversations/00000000-0000-4000-8000-0000000000f6/workspace/data.json --auth none --expect 401
  control-agent-server api GET "/api/conversations/$CID/workspace/data.json" --expect 200 --check qa eq f16
  control-agent-server api POST /api/auth/workspace-session --jar qa-f16 --expect 204 --check-header set-cookie contains oh_workspace_session_key=
  control-agent-server api GET "/api/conversations/$CID/workspace" --auth none --jar qa-f16 --expect 200 --check . contains qa-f16-root --save F16.auth/cookie-root
  control-agent-server api GET "/api/conversations/$CID/workspace/data.json" --auth none --jar qa-f16 --expect 200 --check qa eq f16 --save F16.auth/cookie-file
  control-agent-server api GET "/api/conversations/$CID/workspace/data.json" --auth none --cookie 'oh_workspace_session_key=qa-f16-wrong' --expect 401 --save F16.auth/wrong-cookie
  ```
  Missing and wrong keys are 401 `Unauthorized` before the conversation is
  looked up; the header and the cookie alone (no header) each open both
  routes, and a cookie whose value is not a session key is 401.
- **The agent's own artifact (`F16.agent-artifact`).** A tiny model run
  writes an HTML file; the events socket is captured live and the workspace
  route serves the file.
  ```sh
  mkdir -p "$F/agent"
  AID=$(control-agent-server conversation start --no-run --tools file_editor --no-autotitle --workspace "$F/agent" --print-id)
  control-agent-server ws start "/sockets/events/$AID" --name qa-f16-agent --duration 300
  control-agent-server conversation send "$AID" --text 'Use the file_editor tool to create the file qa-f16-agent.html in the current working directory containing exactly: <p>qa-f16-agent</p>' \
    --wait --until finished --timeout 300
  control-agent-server ws stop qa-f16-agent --kinds ObservationEvent --contains qa-f16-agent.html --expect-min 1 --wait 20 --save F16.agent-artifact/events
  control-agent-server api GET "/api/conversations/$AID/workspace/qa-f16-agent.html" --expect 200 --check . contains qa-f16-agent \
    --check-header content-type eq 'text/html; charset=utf-8' --save F16.agent-artifact/served
  test -f "$F/agent/qa-f16-agent.html"
  ```
  The socket carries the file editor's `ObservationEvent` for
  `qa-f16-agent.html`, and the workspace URL serves it as HTML; the file is
  on disk in the conversation's workspace.
- **Across a restart (`F16.restart`).** Restart the server and serve the
  same conversation's files.
  ```sh
  control-agent-server restart
  control-agent-server api GET "/api/conversations/$CID/workspace/data.json" --expect 200 --check qa eq f16 --save F16.restart/header
  control-agent-server api GET "/api/conversations/$CID/workspace" --auth none --jar qa-f16 --expect 200 --check . contains qa-f16-root --save F16.restart/cookie
  control-agent-server doctor
  ```
  The conversation is restored from persistence and its workspace is
  served; the cookie minted before the restart still works (its value is the
  unchanged session key).
- **VS Code disabled (`F16.vscode-disabled`).** The launcher's default.
  ```sh
  control-agent-server config | jq -e '.config_file.enable_vscode == false'
  control-agent-server api GET /api/vscode/status --expect 200 --check running eq false --check enabled eq false \
    --check message eq 'VSCode is disabled in configuration' --save F16.vscode-disabled/status
  control-agent-server api GET "/api/conversations/$CID/vscode/status" --expect 200 --check running eq false --check enabled eq false \
    --check message eq 'VSCode is disabled in configuration'
  control-agent-server api GET /api/vscode/url --expect 503 --check detail eq 'Internal Server Error' \
    --check exception contains 'VSCode is disabled in configuration' --save F16.vscode-disabled/url
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --expect 503 --check exception contains 'Set enable_vscode=true to enable'
  ```
  Status says disabled with a message; the URL routes are 503, and as for
  every 5xx the body is `{"detail": "Internal Server Error", "exception":
  "503: VSCode is disabled in configuration. Set enable_vscode=true to enable."}`.
- **Scoped guards (`F16.vscode-scoped-guards`).** Bad conversations and bad
  folders, while VS Code is still disabled.
  ```sh
  Z=00000000-0000-4000-8000-0000000000f6
  control-agent-server api GET "/api/conversations/$Z/vscode/status" --expect 404 --check detail eq "Conversation not found: $Z" --save F16.vscode-scoped-guards/unknown
  control-agent-server api GET "/api/conversations/$Z/vscode/url" --expect 404
  control-agent-server api GET /api/conversations/not-a-uuid/vscode/status --expect 422 --check detail.0.loc.1 eq runtime_conversation_id
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query workspace_dir=/etc --expect 422 \
    --check detail eq 'workspace_dir must be inside the conversation workspace' --save F16.vscode-scoped-guards/outside
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query workspace_dir=sub --expect 422 --check detail eq 'workspace_dir must be inside the conversation workspace'
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query "workspace_dir=$WS/sub/../../outside-dir" --expect 422
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query "workspace_dir=$WS/dir-link" --expect 422
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query "workspace_dir=$WS/sub" --expect 503
  ```
  Unknown is 404 and a non-UUID 422 even for `status`; a relative folder, an
  outside one, one that climbs out with `..` and a symlink out of the
  workspace are 422. A folder inside the workspace passes the guard and
  reaches the disabled check (503).
- **VS Code routes are header-only (`F16.vscode-auth`).** No key on all four
  routes, then the workspace cookie from `F16.auth` on the scoped ones.
  ```sh
  control-agent-server api GET /api/vscode/status --auth none --expect 401 --save F16.vscode-auth/status-no-key
  control-agent-server api GET /api/vscode/url --auth bad --expect 401
  control-agent-server api GET "/api/conversations/$CID/vscode/status" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --auth none --expect 401
  control-agent-server api GET "/api/conversations/$CID/workspace" --auth none --jar qa-f16 --expect 200
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --auth none --jar qa-f16 --expect 401 --check detail eq Unauthorized --save F16.vscode-auth/cookie-url
  control-agent-server api GET "/api/conversations/$CID/vscode/status" --auth none --jar qa-f16 --expect 401
  ```
  Every VS Code route is 401 without the header. The jar's cookie (path
  `/api/conversations`, so the browser sends it to the scoped routes) still
  opens the workspace but not the URL that carries the session key.
- **Enabled but not running (`F16.vscode-enabled-status`).** Hold a port with
  an HTTP sink, then restart with VS Code enabled on that port, so it cannot
  start whether or not the binary is installed.
  ```sh
  SINK=$(control-agent-server fixture http-sink --name qa-f16-vscode-port | jq -r .url)
  VPORT=${SINK##*:}
  control-agent-server restart --config-json '{"enable_vscode": true, "vscode_port": '"$VPORT"'}'
  control-agent-server logs --grep 'VSCode service failed to start' --expect-min 1
  control-agent-server api GET /api/vscode/status --expect 200 --check . eq '{"running": false, "enabled": true}' --save F16.vscode-enabled-status/status
  control-agent-server api GET "/api/conversations/$CID/vscode/status" --expect 200 --check . eq '{"running": false, "enabled": true}'
  control-agent-server doctor
  ```
  The log says `VSCode service failed to start, continuing without VSCode`;
  both status routes answer exactly `{"running": false, "enabled": true}`
  (no `message`) and the server is otherwise healthy.
- **URL shape (`F16.vscode-url-shape`).** Global and scoped URLs, with and
  without overrides, then with a base path.
  ```sh
  KEY=$(cat "$AGENT_SERVER_VERIFY_RUN/private/session_api_key")
  control-agent-server api GET /api/vscode/url --expect 200 --check url eq "http://localhost:$VPORT/?tkn=$KEY&folder=workspace" --save F16.vscode-url-shape/global
  control-agent-server api GET /api/vscode/url --query base_url=https://qa-f16.example:9443/ --query workspace_dir=/qa/f16 --expect 200 \
    --check url eq "https://qa-f16.example:9443/?tkn=$KEY&folder=/qa/f16"
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --expect 200 --check url eq "http://localhost:$VPORT/?tkn=$KEY&folder=$WS" --save F16.vscode-url-shape/scoped
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query "workspace_dir=$WS/sub" --expect 200 --check url eq "http://localhost:$VPORT/?tkn=$KEY&folder=$WS/sub"
  control-agent-server restart --config-json '{"vscode_base_path": "/qa-f16/vscode/"}'
  control-agent-server api GET /api/vscode/url --expect 200 --check url eq "http://localhost:$VPORT/qa-f16/vscode/?tkn=$KEY&folder=workspace" --save F16.vscode-url-shape/base-path
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --query base_url=https://qa-f16.example --expect 200 \
    --check url eq "https://qa-f16.example/qa-f16/vscode/?tkn=$KEY&folder=$WS"
  unset KEY
  ```
  The token is the run's session key (redacted in output and evidence), the
  port is `vscode_port`, `base_url` loses its trailing slash, the scoped
  default folder is the conversation's working directory, and the base path
  sits between the origin and `/?tkn=`.
- **Not running, still advertised (`F16.vscode-url-not-running`), known bug.**
  Read status and URL on the keyed run, where VS Code is not running.
  ```sh
  control-agent-server api GET /api/vscode/status --expect 200 --check enabled eq true --check running eq false
  control-agent-server api GET /api/vscode/url --expect 200 --check . eq '{"url": null}' --save F16.vscode-url-not-running/global  # bug
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --expect 200 --check . eq '{"url": null}'  # bug
  ```
  `running` is false, so the body should be exactly `{"url": null}` on both
  routes (each `# bug` line alone shows the defect), as the service intends
  (`VSCodeService.set_connection_token`: a server that is not running keeps
  its token "so `get_vscode_url` doesn't advertise a server that isn't there
  (for example, in images without VSCode)"), as a keyless server answers
  (`F16.vscode-url-no-key`), and as a server that receives the very same key
  through `POST /api/init` answers (`F16.vscode-url-after-init`). A server
  booted with the key instead returns
  `http://localhost:<port>/?tkn=<session key>&folder=...`, because
  `get_vscode_service()` seeds the token from `session_api_keys[0]` before
  `start()` fails. Consumers that show an "open VS Code" link get a dead one.
  Low severity: the URL only reaches callers that already hold the key.
- **Folder with `&` and `#` (`F16.vscode-folder-encoding`), known bug.**
  Parse the scoped URL as a browser would, first for the normal workspace,
  then for a workspace named `qa f16&x=1#frag`.
  ```sh
  ROUNDTRIP='import sys, urllib.parse as u; p = u.urlsplit(sys.stdin.read().strip()); q = u.parse_qs(p.query); sys.exit(0 if q.get("folder") == [sys.argv[1]] and not p.fragment else 1)'
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --expect 200 --field url | python3 -c "$ROUNDTRIP" "$WS"
  mkdir -p "$F/qa f16&x=1#frag"
  ODD=$(control-agent-server conversation start --no-run --tools none --no-autotitle --workspace "$F/qa f16&x=1#frag" --print-id)
  OWS=$(control-agent-server api GET "/api/conversations/$ODD" --field workspace.working_dir)
  control-agent-server api GET "/api/conversations/$ODD/vscode/url" --expect 200 --save F16.vscode-folder-encoding/odd-folder
  control-agent-server api GET "/api/conversations/$ODD/vscode/url" --expect 200 --field url | python3 -c "$ROUNDTRIP" "$OWS"  # bug
  ```
  The normal workspace round-trips. For the odd one the `folder` parameter
  should decode back to `.../qa f16&x=1#frag`; today the value is pasted
  unencoded, so a parser reads `folder=.../qa f16`, an extra `x=1` parameter
  and a `#frag` fragment, and VS Code would open the wrong folder. Without
  the OpenVSCode binary the URL is only readable through the defect of
  `F16.vscode-url-not-running` (see Gotchas).
- **No key, no URL (`F16.vscode-url-no-key`).** A second server without
  session keys, VS Code enabled on the port the sink still holds.
  ```sh
  NK=$(control-agent-server launch --new --no-auth --vscode --name f16-nokey --config-json '{"vscode_port": '"$VPORT"'}' --print-run)
  trap 'control-agent-server stop --run "$NK" >/dev/null' EXIT
  mkdir -p "$NK/fixtures/qa-f16-ws"
  NC=$(control-agent-server conversation start --run "$NK" --no-run --tools none --no-autotitle --workspace "$NK/fixtures/qa-f16-ws" --print-id)
  control-agent-server api GET /api/vscode/status --run "$NK" --auth none --expect 200 --check enabled eq true --check running eq false
  control-agent-server api GET /api/vscode/url --run "$NK" --auth none --expect 200 --check . eq '{"url": null}' --save F16.vscode-url-no-key/global
  control-agent-server api GET "/api/conversations/$NC/vscode/url" --run "$NK" --auth none --expect 200 --check . eq '{"url": null}'
  control-agent-server stop --run "$NK"
  ```
  With no key and no binary there is no token, so both URL routes answer
  `{"url": null}` while `running` is false (see Gotchas for a machine with
  the binary). The second run's evidence lands under its own run directory.
- **Key delivered by init, no URL (`F16.vscode-url-after-init`).** A
  deferred-init server booted without keys, VS Code enabled on the held port,
  then initialized with a session key, as a warm pool does.
  ```sh
  DI=$(control-agent-server launch --new --no-auth --deferred-init --vscode --name f16-deferred --config-json '{"vscode_port": '"$VPORT"'}' --print-run)
  trap 'control-agent-server stop --run "$DI" >/dev/null' EXIT
  control-agent-server api POST /api/init --run "$DI" --auth none --header "X-Init-API-Key: $(cat "$DI/private/secret_key")" \
    --json '{"session_api_keys": ["qa-f16-init-key"]}' --expect 200 --check state eq ready
  control-agent-server api GET /api/vscode/url --run "$DI" --auth none --expect 401
  control-agent-server api GET /api/vscode/status --run "$DI" --auth none --header 'X-Session-API-Key: qa-f16-init-key' --expect 200 \
    --check enabled eq true --check running eq false --save F16.vscode-url-after-init/status
  control-agent-server api GET /api/vscode/url --run "$DI" --auth none --header 'X-Session-API-Key: qa-f16-init-key' --expect 200 \
    --check . eq '{"url": null}' --save F16.vscode-url-after-init/url
  control-agent-server stop --run "$DI"
  ```
  The delivered key is enforced (401 without it) and status is
  `{"running": false, "enabled": true}`. The URL is null: `/api/init` hands
  the key to VS Code only when it is running (`set_connection_token`), so this
  server and the keyed boot of `F16.vscode-url-not-running` answer
  differently for the same configuration.
- **Docker-mode root and the cookie (`F16.docker-root-cookie`), known bug.**
  A second server with `OH_CONVERSATION_RUNTIME=docker`. Translated: no
  Docker daemon is needed, because the route table and its auth groups are
  fixed at startup (which only warns that it cannot list containers), and an
  unknown conversation shows which group answered.
  ```sh
  DK=$(control-agent-server launch --new --name f16-docker --env OH_CONVERSATION_RUNTIME=docker --print-run)
  trap 'control-agent-server stop --run "$DK" >/dev/null' EXIT
  Z=00000000-0000-4000-8000-0000000000f6
  control-agent-server api GET "/api/conversations/$Z/workspace/" --run "$DK" --expect 404 --check detail eq 'Conversation not found'
  control-agent-server api GET "/api/conversations/$Z/workspace" --run "$DK" --expect 404 --check detail eq 'Conversation not found'
  control-agent-server api GET "/api/conversations/$Z/workspace/" --run "$DK" --auth none --expect 401
  control-agent-server api POST /api/auth/workspace-session --run "$DK" --jar qa-f16-docker --expect 204
  control-agent-server api GET "/api/conversations/$Z/workspace/" --run "$DK" --auth none --jar qa-f16-docker --expect 404 --save F16.docker-root-cookie/slash
  control-agent-server api GET "/api/conversations/$Z/workspace" --run "$DK" --auth none --jar qa-f16-docker --expect 404 --save F16.docker-root-cookie/slashless  # bug
  control-agent-server stop --run "$DK"
  ```
  With the header both forms reach the Docker routes (404); with nothing,
  `workspace/` is 401. With only the
  cookie, `workspace/` passes the workspace auth group (404), but the
  slashless `workspace` answers 401: `docker_workspace_router` only defines
  `/{conversation_id}/workspace/{file_path:path}`, so the root falls into the
  header-only conversation proxy. In local mode the slashless root accepts
  the cookie (`F16.auth`). Clients that use the trailing slash (the
  TypeScript client does) are not affected.
- **VS Code actually running (`F16.vscode-running`), blocked.**
  Needs the OpenVSCode Server binary at
  `/openhands/.openvscode-server/bin/openvscode-server` (the path is hardcoded;
  `control-agent-server capabilities` reports `vscode`).
  ```sh
  control-agent-server restart --reset-config --config-json '{"enable_vscode": true}'
  control-agent-server api GET /api/vscode/status --expect 200 --check enabled eq true --check running eq true
  control-agent-server api GET "/api/conversations/$CID/vscode/url" --expect 200 --check url contains "folder=$WS"
  control-agent-server api GET /api/vscode/url --field url | xargs curl -sS --noproxy '*' -o /dev/null -w '%{http_code}\n' | grep -qx 200
  ```
  With the binary and a free `vscode_port`, status reports `running: true`
  and the advertised URL answers 200 with the editor.

## Gotchas

- 5xx answers are rewritten by the server's exception handler:
  `detail` is always `Internal Server Error` and the real reason is in
  `exception` (`"503: VSCode is disabled ..."`). Check `exception`, not
  `detail`.
- The `tkn` in a VS Code URL is the first session key. The CLI redacts it in
  output and evidence, but `--field url` prints it raw for shell chaining:
  pipe it straight into the consumer. `map run` saves every variable a
  bullet assigns into the run's `map-run/*.state.sh`, so a URL or key kept in
  a variable lands on disk (`F16.vscode-url-shape` unsets `KEY`).
- VS Code only starts if `/openhands/.openvscode-server/bin/openvscode-server`
  exists and `vscode_port` is free, and only at startup; it never retries.
  The log line `VSCode server binary not found, VSCode will be disabled` does
  not mean `enabled: false`: status still says `enabled: true`. The service is
  a process singleton built from the config file and environment, so toggle
  it with `restart --config-json` (overrides accumulate across restarts until
  `--reset-config`).
- `F16.vscode-url-shape`, and the arrange steps of
  `F16.vscode-folder-encoding`, read the URL the server advertises while VS
  Code is not running (see `F16.vscode-url-not-running`). When that bug is
  fixed, the shape and the folder encoding are only observable with the
  binary installed; move those checks into `F16.vscode-running` at the same
  time (until then `vscode-url-shape` fails and `vscode-folder-encoding`
  fails before its `# bug` line, which is the signal to move them).
- A null URL is asserted on the whole body (`--check . eq '{"url": null}'`):
  `--check url missing` and `--check url eq null` also pass when the key is
  absent, which would break the SDK and TypeScript models that expect it.
- `F16.vscode-url-no-key` and `F16.vscode-url-after-init` hold for a machine
  without the OpenVSCode binary (like this one). With the binary, `start()`
  mints a random token before its port check
  (`tests/agent_server/test_vscode_service.py::test_start_port_unavailable`),
  so a keyless or deferred server whose VS Code failed on the held port also
  advertises a URL for a server that is not running; on such a machine those
  two bullets fail on the same defect as `F16.vscode-url-not-running`.
- The scoped VS Code routes run the conversation lookup and the
  `workspace_dir` guard before the enabled check, so their 404 and 422 answers
  do not depend on configuration. The global `/api/vscode/url` does not check
  `workspace_dir` at all.
- `api` sends literal `..` segments already normalized by the HTTP client;
  traversal probes must percent-encode them (`%2e%2e`, `..%2F`) or use a
  double slash for an absolute path.
- Workspace files are served from the live conversation's
  `workspace.working_dir` (the worktree for worktree conversations); dotfiles
  such as `.git/config` or `.env` are served like any other file.
- There is no conditional-request support: `If-None-Match` with the current
  `ETag` still returns 200 with the body, and `HEAD` is 405 (FastAPI GET
  routes do not add HEAD).
- The workspace routes are mounted outside the dormant gate's router; in
  deferred-init mode they answer 503 through the missing conversation service
  (see F03). In Docker runtime mode `workspace/{file_path}` is proxied into the
  conversation's container and the `Conversation workspace is not local`
  branch of the local handler is unreachable (the local registry only accepts
  `LocalWorkspace`).
- CORS on the workspace routes echoes any http(s) origin with credentials
  (F02.cors-workspace-routes), so whatever these routes serve is readable by
  any page that holds the partitioned cookie.
