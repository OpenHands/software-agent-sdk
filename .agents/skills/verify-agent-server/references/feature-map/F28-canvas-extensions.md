# Canvas apps: install, catalog and backend lifecycle

Agent Canvas apps ("canvas extensions") are UI bundles the server installs and
serves: a directory with a `canvas-extension.json` manifest (schema 1), an
entrypoint bundle, an optional SVG icon and an optional backend. A consumer
installs one from a local directory, a git URL (`file://` included) or the
`github:owner/repo` shorthand; it always lands disabled, keyed by the
manifest's `name`, and is listed with its manifest re-validated from disk on
every read. The bundle and icon are served byte for byte. An app that
declares a `backend` gets an explicit, revision-pinned lifecycle: `GET .../backend`
reports the state and the revision, `prepare` verifies the artifact's sha256
and extracts it read-only (the approval), `start` runs the prepared revision in
its own process group with only `HOME` (plus the allow-listed variables the
manifest asks to inherit) in its environment and waits for its health check, `stop` kills the group, `logs` returns bounded stdout/stderr and
`DELETE .../backend/data` wipes the app's mutable data. Installs and approvals
persist across restarts; running backends do not.

Source: `openhands-agent-server/openhands/agent_server/canvas_extensions_router.py`, `openhands-agent-server/openhands/agent_server/canvas_extensions/`, `openhands-sdk/openhands/sdk/extensions/`

Needs: `git`, `node`

Launch: `--env TZ=UTC`

Routes: `POST /api/canvas-extensions/install`, `GET /api/canvas-extensions/installed`,
`GET /api/canvas-extensions/installed/{extension_name}`,
`PATCH /api/canvas-extensions/installed/{extension_name}`,
`DELETE /api/canvas-extensions/installed/{extension_name}`,
`GET /api/canvas-extensions/installed/{extension_name}/backend`,
`POST /api/canvas-extensions/installed/{extension_name}/backend/prepare`,
`POST /api/canvas-extensions/installed/{extension_name}/backend/start`,
`POST /api/canvas-extensions/installed/{extension_name}/backend/stop`,
`GET /api/canvas-extensions/installed/{extension_name}/backend/logs`,
`DELETE /api/canvas-extensions/installed/{extension_name}/backend/data`,
`GET /api/canvas-extensions/installed/{extension_name}/bundle`,
`GET /api/canvas-extensions/installed/{extension_name}/icon`

## Sub-features

- `F28.install-local`: installing from a local directory answers 200 with the app disabled (even when the body says `"enabled": true`), `resolved_ref` null and the validated manifest, copies it under `canvas-extensions/installed/<name>/` and records it in `.installed.json`, and the list and GET views agree.
- `F28.install-git`: a `file://` git URL installs as a git source: `resolved_ref` is the repository's HEAD commit and the clone is cached under `cache/extensions/`.
- `F28.install-ref`: `ref` pins a git install to that commit (`version` and `resolved_ref` are the pinned commit's, `.installed.json` records `requested_ref`), a forced install without `ref` moves to the branch head, and an unknown ref on a source that was never fetched is 400 and installs nothing.
- `F28.install-repo-path`: `repo_path` installs a subdirectory of the source and is echoed back; a missing subdirectory or one that escapes the source is 400, and a source root without a manifest is 422.
- `F28.install-errors`: an unreadable source is 400 (missing path, unparseable string, bad `github:` shorthand, `file://` URL of a non-repository), a missing or empty `source` or an invalid manifest (bad JSON, bad name, escaping or missing entrypoint, wrong `schema_version`) is 422, no key is 401, and nothing is installed.
- `F28.bundle`: `GET .../bundle` serves the installed entrypoint byte for byte with `Cache-Control: no-cache`, an `ETag` and a JavaScript content type, whether or not the app is enabled.
- `F28.icon`: `GET .../icon` serves the declared SVG byte for byte as `image/svg+xml` with a sandboxing CSP and `nosniff`; an app without an icon is 404.
- `F28.enable-disable`: `PATCH .../installed/{name}` toggles `enabled`, and both GET and `.installed.json` show the new value; a non-boolean body is 422.
- `F28.unknown-name`: an unknown name is 404 on GET, PATCH, DELETE, bundle and icon, a non-kebab-case name is 422 on every route, and the routes need the session key.
- `F28.list-discovery`: a valid app directory copied into `installed/` by hand is invisible to GET by name until a list call registers it (disabled, `source` `local`), and a list call prunes an entry whose directory was deleted.
- `F28.broken-install`: an installed app whose manifest stops validating, or whose entrypoint becomes a symlink out of the package, stays listed with `manifest` null; its bundle and icon are 404 and its backend reports `missing`, while PATCH and DELETE still work. An icon that becomes a symlink out of the package is 404 while the manifest and bundle stay valid.
- `F28.backend-status`: `GET .../backend` reports `missing` for an unknown name, `unsupported` for a browser-only app and `stopped` with a revision for a backend app (`sha256:<hex>` of the manifest for a local install, the commit SHA for a git install), never 404.
- `F28.backend-approval-gate`: starting an unprepared revision, preparing a wrong revision, preparing a tampered archive, preparing an archive with an escaping member and preparing a remote (`url`) artifact that cannot be downloaded are all 409, and none of them writes an approval or extracts or keeps a file.
- `F28.backend-prepare`: preparing the current revision verifies the checksum, extracts the artifact into a read-only `artifacts/<name>/<sha256>/` directory, records the approval (shown as `prepared_revision`), and is idempotent.
- `F28.backend-start`: starting the prepared revision returns `ready` with a pid and port; the backend answers on `127.0.0.1:<port>`, runs in its own process group from the installed package directory with only `HOME=<data>/home` in its environment; a repeated start keeps the same pid, a different revision is 409, and a forced install or a data deletion is 409 while it runs.
- `F28.backend-stop`: `POST .../backend/stop` kills the process group (a child the backend forked dies too) and reports `stopped`; the port closes, and a second stop is a harmless 200.
- `F28.force-reinstall`: installing an already installed app without `force` is 409 and leaves the record untouched; `force: true` replaces the files (new bundle bytes and ETag, new `installed_at`), keeps the previous `enabled` value, and deletes the backend approval so the revision must be prepared again.
- `F28.backend-logs`: while a backend is `starting` its stdout and stderr lines are readable with `[stdout] `/`[stderr] ` prefixes; `stop` abandons the pending start, the logs stay readable after it, `limit_bytes` bounds the tail and sets `truncated`, values outside 1..262144 are 422, and an unknown app has empty logs.
- `F28.ts-client`: the TypeScript `CanvasExtensionsClient` takes a backend app through `getBackendStatus`, `prepareBackend`, `startBackend`, `getBackendLogs`, `stopBackend` and `deleteBackendData` with the REST results, rejects refusals as `HttpError` with the server's status and detail, and a client timeout shorter than the backend's health wait rejects `startBackend` while the server keeps the backend `starting`.
- `F28.backend-start-fails`: a backend whose health check never passes ends `unhealthy` with `backend health check timed out` after `health.timeout_seconds`, one that exits ends `unhealthy` with its exit code, and neither leaves a process behind.
- `F28.backend-inherit-environment`: a manifest whose backend asks to inherit a variable outside the allow-list (`OH_SECRET_KEY`) or names one twice is refused at install with 422, and a backend that asks for `PATH`, `TZ` and `LANG` runs with exactly `HOME` plus the server's values of those it has, none of the server's secrets.
- `F28.disable-stops-backend`: disabling an app with a running backend stops its process group, and the backend reports `stopped`.
- `F28.restart-persistence`: after a graceful restart the installs, their `enabled` values and the backend approval are still there, the backend that was running is gone, and `start` works without a new `prepare`.
- `F28.uninstall`: `DELETE .../installed/{name}` stops the backend and removes the package and its record, keeps the backend data, approval and artifact, reports the backend `missing`, and a second DELETE is 404; `DELETE .../backend/data` then removes the data directory.
- `F28.prepare-corrupt-archive`: an artifact that is not a gzip archive, with a matching sha256, is refused with 409 like any other bad artifact.
- `F28.install-ref-cached`: an unknown `ref` is 400 and leaves the installed app alone even when the source's clone is already cached.
- `F28.status-probe-latch`: a backend that missed one status health probe (it answered slower than the 1 s probe timeout) is reported `ready` again once it answers its health check.
- `F28.status-exit-hang`: when a backend's leader process exits while a child it forked still holds its stdout, `GET .../backend` answers promptly with `unhealthy` and `stop` reaps the child.
- `F28.hard-restart-orphan`: a backend that was running when the server was SIGKILLed does not survive the server's restart.

## How to get to it (agent POV)

- REST (catalog): `POST /api/canvas-extensions/install` with
  `{"source", "ref", "repo_path", "force"}`, `GET /api/canvas-extensions/installed`,
  `GET|PATCH|DELETE /api/canvas-extensions/installed/{extension_name}`
  (`PATCH` body `{"enabled": bool}`), `GET .../bundle` and `GET .../icon`.
  Every recipe here drives these routes directly.
- REST (backend lifecycle): `GET .../backend`, `POST .../backend/prepare` and
  `POST .../backend/start` (both `{"revision": "<GET .../backend .revision>"}`),
  `POST .../backend/stop`, `GET .../backend/logs?limit_bytes=N` and
  `DELETE .../backend/data`. Driven directly as well.
- TypeScript client: `OpenHandsClient.canvasExtensions` (`CanvasExtensionsClient`
  in `clients/typescript/src/client/canvas-extensions-client.ts`) has
  `getBackendStatus`, `prepareBackend`, `startBackend`, `stopBackend`,
  `getBackendLogs` and `deleteBackendData`; it has no install, list, enable,
  uninstall, bundle or icon methods. Its default 60 s timeout can be shorter
  than a manifest's `health.timeout_seconds` (up to 300 s): `startBackend`
  then rejects on the client while the server keeps starting. Driven by
  `F28.ts-client` from the built `clients/typescript/dist`. Its
  `createAppBackendSession`/`revokeAppBackendSession` belong to the bridge
  family (F29).
- Python SDK: no consumer; the routes wrap
  `openhands.agent_server.canvas_extensions.installed` and the SDK's generic
  `InstallationManager` (shared with plugins and skills).
- Agent Canvas (context only): the Canvas frontend installs and toggles apps,
  loads `bundle` and `icon`, and runs the prepare and start flow before it
  routes the browser to a backend through the `/app-backends` bridge (another
  family).
- Config: everything lives under `OH_PERSISTENCE_DIR`
  (`canvas-extensions/installed`, `canvas-extensions/backends/{approvals,artifacts,data,downloads}`,
  `cache/extensions`), which `launch` makes private to the run
  (`home/.openhands`). Backends run only on Linux amd64/arm64.

## Driving it with control-agent-server

Preconditions:

- A baseline run is live and exported (`launch --new --env TZ=UTC`, the
  family's `Launch:` flags), `doctor` is ok. No
  LLM profile is needed; `git` and `python3` are on `PATH`.
- Fixtures, all owned by the run: `fixture canvas-app --name qa-canvas`
  (browser-only, with an icon; a git repository), `--name qa-backend --backend ok`
  (an aiohttp backend artifact with `/health` and an echo route),
  `--name qa-unhealthy --backend unhealthy` (health answers 503) and
  `--name qa-exit --backend exit` (exits with code 3). The CLI has no fixture
  for a backend that prints and hangs, for an archive with an escaping member
  or for an artifact that is not gzip, so the block below writes small
  apps under `fixtures/f28/` with a Python heredoc: `qa-f28-logs` (prints one
  line to each stream, then sleeps without serving HTTP), `qa-f28-unsafe` (a
  `../qa-f28-escape` member), `qa-f28-notgz` (plain bytes, correct sha256) and
  `qa-f28-flaky` (a stdlib HTTP backend whose `/slow` and `/fast` routes make
  its health check take 2 s or answer at once, whose `/fork` route starts a
  `/bin/sleep 300` child sharing its stdout and prints the child's pid, and
  whose `/exit` route kills the leader without answering), `qa-f28-env` (the
  same server, asking to inherit `PATH`, `TZ` and `LANG`), `qa-f28-env-secret`
  and `qa-f28-env-dup` (asking for `OH_SECRET_KEY`, and for `TZ` twice) and
  `qa-f28-remote` (a remote `url` artifact on a closed loopback HTTPS port).
  The family launches with `TZ=UTC` (`Launch:`), so the server has a `TZ` to
  pass on. `node` runs the TypeScript client from `clients/typescript/dist`
  (a checkout without it builds it with `npm ci && npm run build`, which
  needs the npm registry). It also commits a
  two-commit git app `qa-f28-git` (manifest `1.0.0`, then `2.0.0`) for the
  `ref` bullets, with `GV1` and `GV2` naming the two commits.
  ```sh
  FX=$(control-agent-server fixture canvas-app --name qa-canvas --print-path)
  BX=$(control-agent-server fixture canvas-app --name qa-backend --backend ok --print-path)
  UX=$(control-agent-server fixture canvas-app --name qa-unhealthy --backend unhealthy --print-path)
  EX=$(control-agent-server fixture canvas-app --name qa-exit --backend exit --print-path)
  F28FX="$AGENT_SERVER_VERIFY_RUN/fixtures/f28"
  python3 - "$F28FX" <<'PY'
  import hashlib, io, json, pathlib, sys, tarfile
  root = pathlib.Path(sys.argv[1])
  def targz(*members):
      buffer = io.BytesIO()
      with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
          for name, data in members:
              info = tarfile.TarInfo(name)
              info.size, info.mode = len(data), 0o755
              tar.addfile(info, io.BytesIO(data))
      return buffer.getvalue()
  def app(name, archive, argv=("{artifact_dir}/run.sh", "{port}"), inherit=None):
      pkg = root / name
      (pkg / "dist").mkdir(parents=True, exist_ok=True)
      (pkg / "backend").mkdir(exist_ok=True)
      (pkg / "dist" / "index.js").write_text(f"export const app = {name!r};\n")
      (pkg / "backend" / "linux.tar.gz").write_bytes(archive)
      artifact = {"path": "backend/linux.tar.gz", "sha256": hashlib.sha256(archive).hexdigest()}
      manifest = {"schema_version": 1, "name": name, "display_name": name, "version": "0.1.0",
                  "entrypoint": "dist/index.js",
                  "backend": {"schema_version": 1,
                              "artifacts": {"linux-amd64": artifact, "linux-arm64": artifact},
                              "argv": list(argv),
                              "health": {"path": "/health", "timeout_seconds": 30, "interval_seconds": 0.2}}}
      if inherit is not None:
          manifest["backend"]["inherit_environment"] = list(inherit)
      (pkg / "canvas-extension.json").write_text(json.dumps(manifest, indent=2))
  app("qa-f28-logs", targz(("run.sh", b"#!/bin/sh\necho qa-f28-out\necho qa-f28-err >&2\nexec /bin/sleep 120\n")))
  app("qa-f28-unsafe", targz(("run.sh", b"#!/bin/sh\n"), ("../qa-f28-escape", b"x\n")))
  app("qa-f28-notgz", b"qa-f28 plain bytes, not a gzip archive\n")
  FLAKY = '''
  import http.server, os, subprocess, sys, time
  slow = False
  class Handler(http.server.BaseHTTPRequestHandler):
      def do_GET(self):
          global slow
          if self.path == "/exit":
              os._exit(0)
          slow = {"/slow": True, "/fast": False}.get(self.path, slow)
          body = b"ok"
          if self.path == "/fork":
              body = str(subprocess.Popen(["/bin/sleep", "300"]).pid).encode()
          if self.path == "/health" and slow:
              time.sleep(2)
          self.send_response(200)
          self.end_headers()
          self.wfile.write(body)
      def log_message(self, *args):
          pass
  http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
  '''
  app("qa-f28-flaky", targz(("run.py", f"#!{sys.executable}\n{FLAKY}".encode())), argv=("{artifact_dir}/run.py", "{port}"))
  for env_app, names in (("qa-f28-env", ["PATH", "TZ", "LANG"]), ("qa-f28-env-secret", ["TZ", "OH_SECRET_KEY"]),
                         ("qa-f28-env-dup", ["TZ", "TZ"])):
      app(env_app, targz(("run.py", f"#!{sys.executable}\n{FLAKY}".encode())), argv=("{artifact_dir}/run.py", "{port}"),
          inherit=names)
  remote = root / "qa-f28-remote"
  (remote / "dist").mkdir(parents=True, exist_ok=True)
  (remote / "dist" / "index.js").write_text("export const app = 'qa-f28-remote';\n")
  unreachable = {"url": "https://127.0.0.1:9/qa-f28.tar.gz", "sha256": "0" * 64}
  (remote / "canvas-extension.json").write_text(json.dumps({
      "schema_version": 1, "name": "qa-f28-remote", "display_name": "qa-f28-remote", "version": "0.1.0",
      "entrypoint": "dist/index.js",
      "backend": {"schema_version": 1, "argv": ["{artifact_dir}/run.sh"],
                  "artifacts": {"linux-amd64": unreachable, "linux-arm64": unreachable}}}))
  PY
  P="$AGENT_SERVER_VERIFY_RUN/home/.openhands"
  INST="$P/canvas-extensions/installed"
  BK="$P/canvas-extensions/backends"
  BHEAD=$(git -C "$BX" rev-parse HEAD)
  BSHA=$(sha256sum "$BX/backend/linux.tar.gz" | cut -d' ' -f1)
  test -f "$F28FX/qa-f28-notgz/canvas-extension.json" && test -f "$F28FX/qa-f28-flaky/canvas-extension.json"
  GX="$F28FX/qa-f28-git"
  if [ ! -d "$GX/.git" ]; then
    mkdir -p "$GX/dist" && printf 'export const v = 1;\n' > "$GX/dist/index.js"
    printf '{"schema_version": 1, "name": "qa-f28-git", "display_name": "QA git", "version": "1.0.0", "entrypoint": "dist/index.js"}' > "$GX/canvas-extension.json"
    git -C "$GX" init -q -b main && git -C "$GX" add -A
    git -C "$GX" -c user.name=QA -c user.email=qa@example.invalid -c commit.gpgsign=false commit -qm v1
    sed -i 's/"1.0.0"/"2.0.0"/' "$GX/canvas-extension.json"
    git -C "$GX" -c user.name=QA -c user.email=qa@example.invalid -c commit.gpgsign=false commit -qam v2
  fi
  GV1=$(git -C "$GX" rev-parse HEAD~1)
  GV2=$(git -C "$GX" rev-parse HEAD)
  ```

- **Install from a local directory (`F28.install-local`).** Install the
  browser-only app with a body that asks for `enabled: true`.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$FX\", \"enabled\": true}" \
    --expect 200 --check name eq qa-canvas --check enabled eq false --check resolved_ref eq null \
    --check source eq "$FX" --check install_path eq "$INST/qa-canvas" \
    --check manifest.contributes.pages.0.path eq /qa-canvas --check manifest.icon eq assets/icon.svg \
    --save F28.install-local/install
  AT0=$(control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --field installed_at)
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --expect 200 \
    --check enabled eq false --check manifest.display_name eq 'QA qa-canvas' --save F28.install-local/get
  control-agent-server api GET /api/canvas-extensions/installed --expect 200 --check canvas_extensions len-eq 1 \
    --check canvas_extensions.0.name eq qa-canvas --check canvas_extensions.0.enabled eq false --save F28.install-local/list
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json \
    --check extensions.qa-canvas.enabled eq false --check extensions.qa-canvas.installed_at eq "$AT0"
  cmp "$INST/qa-canvas/dist/index.js" "$FX/dist/index.js"
  ```
  The install answers 200 with `enabled: false` although the body asked for
  `true` (the field is ignored), the GET, the list and `.installed.json`
  agree, and the package is copied under `installed/qa-canvas/`.
- **Install from a git URL (`F28.install-git`).** Install the backend app
  from its `file://` URL.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$BX\"}" \
    --expect 200 --check name eq qa-backend --check resolved_ref eq "$BHEAD" --check enabled eq false \
    --check source eq "file://$BX" --save F28.install-git/install
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend --check resolved_ref eq "$BHEAD"
  control-agent-server state ls 'home/.openhands/cache/extensions/qa-backend-*/canvas-extension.json' --expect-count 1
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json \
    --check extensions.qa-backend.resolved_ref eq "$BHEAD"
  ```
  `resolved_ref` is the fixture's HEAD commit in the response, the GET and
  `.installed.json`, and a clone sits in `cache/extensions/qa-backend-<hash>/`.
- **Pin a git ref (`F28.install-ref`).** Ask `qa-f28-git` for a ref that does
  not exist, pin its first commit, then reinstall it without a ref.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$GX\", \"ref\": \"qa-no-such-ref\"}" \
    --expect 400 --check detail contains 'Could not read canvas extension source' --save F28.install-ref/unknown-ref
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-git --expect 404
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$GX\", \"ref\": \"$GV1\"}" \
    --expect 200 --check name eq qa-f28-git --check version eq 1.0.0 --check resolved_ref eq "$GV1" \
    --check manifest.version eq 1.0.0 --save F28.install-ref/pinned
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-git --check version eq 1.0.0 --check resolved_ref eq "$GV1"
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json \
    --check extensions.qa-f28-git.requested_ref eq "$GV1" --check extensions.qa-f28-git.resolved_ref eq "$GV1"
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$GX\", \"force\": true}" \
    --expect 200 --check version eq 2.0.0 --check resolved_ref eq "$GV2" --save F28.install-ref/latest
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-git --check version eq 2.0.0 --check resolved_ref eq "$GV2"
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-f28-git --expect 200
  ```
  The unknown ref on a source that was never fetched is 400
  (`Could not read canvas extension source: Failed to fetch extension from file://...`)
  and installs nothing. `ref` set to the first commit installs `1.0.0` with
  that commit as `resolved_ref` and `requested_ref`; a forced install without
  a ref moves to the branch head (`2.0.0`). The app is uninstalled again, so
  the catalog counts below are unchanged.
- **Install a subdirectory (`F28.install-repo-path`).** `fixtures/f28/` holds
  several apps and no manifest of its own.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX\", \"repo_path\": \"qa-f28-logs\"}" \
    --expect 200 --check name eq qa-f28-logs --check repo_path eq qa-f28-logs --save F28.install-repo-path/install
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs --check repo_path eq qa-f28-logs
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX\", \"repo_path\": \"qa-f28-nope\"}" \
    --expect 400 --check detail contains 'not found' --save F28.install-repo-path/missing
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX\", \"repo_path\": \"../..\"}" \
    --expect 400 --check detail contains escapes --save F28.install-repo-path/escape
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX\"}" \
    --expect 422 --check detail contains canvas-extension.json --save F28.install-repo-path/root
  ```
  The subdirectory installs as `qa-f28-logs` with `repo_path` recorded; the
  three bad variants are 400 `... not found ...`, 400 `... escapes ...` and
  422 `No canvas-extension.json found ...`.
- **Install errors (`F28.install-errors`).** Unreadable sources, bad bodies and
  bad manifests; nothing may be installed.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json '{"source": "/nonexistent/qa-f28"}' \
    --expect 400 --check detail contains 'does not exist' --save F28.install-errors/missing-path
  control-agent-server api POST /api/canvas-extensions/install --json '{"source": "qa-not-a-source"}' \
    --expect 400 --check detail contains 'Unable to parse'
  control-agent-server api POST /api/canvas-extensions/install --json '{"source": "github:onlyowner"}' \
    --expect 400 --check detail contains 'Invalid GitHub shorthand'
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$F28FX\"}" --expect 400
  control-agent-server api POST /api/canvas-extensions/install --json '{"source": ""}' --expect 422
  control-agent-server api POST /api/canvas-extensions/install --json '{}' --expect 422 --check detail.0.loc.1 eq source
  BAD="$F28FX/qa-f28-bad"
  mkdir -p "$BAD/dist" && printf 'x\n' > "$BAD/dist/index.js"
  printf '{bad' > "$BAD/canvas-extension.json"
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$BAD\"}" \
    --expect 422 --check detail contains 'Invalid canvas extension' --save F28.install-errors/bad-json
  for MANIFEST in \
    '{"schema_version": 1, "name": "Bad_Name", "display_name": "x", "version": "1", "entrypoint": "dist/index.js"}' \
    '{"schema_version": 1, "name": "qa-f28-bad", "display_name": "x", "version": "1", "entrypoint": "../outside.js"}' \
    '{"schema_version": 1, "name": "qa-f28-bad", "display_name": "x", "version": "1", "entrypoint": "dist/missing.js"}' \
    '{"schema_version": 2, "name": "qa-f28-bad", "display_name": "x", "version": "1", "entrypoint": "dist/index.js"}'; do
    printf '%s' "$MANIFEST" > "$BAD/canvas-extension.json"
    control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$BAD\"}" \
      --expect 422 --check detail contains 'Invalid canvas extension' --quiet
  done
  control-agent-server api POST /api/canvas-extensions/install --auth none --json "{\"source\": \"$BAD\"}" --expect 401
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-bad --expect 404
  control-agent-server api GET /api/canvas-extensions/installed --check canvas_extensions len-eq 3
  test ! -e "$INST/qa-f28-bad"
  ```
  The 400s carry `Could not read canvas extension source: <reason>`, the
  pydantic 422s name `source`, every bad manifest is 422
  `Invalid canvas extension. Ensure the manifest is well-formed.`, and the
  catalog still holds only the three earlier apps.
- **Bundle (`F28.bundle`).** Download the entrypoint of the (disabled) app.
  ```sh
  BUNDLE="$AGENT_SERVER_VERIFY_RUN/fixtures/f28/bundle-v1.js"
  ETAG1=$(control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/bundle --expect 200 \
    --check-header cache-control eq no-cache --check-header content-type contains text/javascript \
    --check-header etag exists --raw-out "$BUNDLE" --print-header etag)
  cmp "$BUNDLE" "$FX/dist/index.js"
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/bundle --check-header etag eq "$ETAG1" \
    --quiet --save F28.bundle/bundle
  ```
  The body is byte-identical to `dist/index.js`, with `Cache-Control: no-cache`,
  a `text/javascript` content type and a stable ETag; the app is still disabled.
- **Icon (`F28.icon`).** Download the declared SVG; an app without an icon has none.
  ```sh
  ICON="$AGENT_SERVER_VERIFY_RUN/fixtures/f28/icon.svg"
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/icon --expect 200 \
    --check-header content-type eq image/svg+xml --check-header content-security-policy contains sandbox \
    --check-header content-security-policy contains "default-src 'none'" \
    --check-header x-content-type-options eq nosniff --check-header cache-control eq no-cache \
    --raw-out "$ICON" --save F28.icon/icon
  cmp "$ICON" "$FX/assets/icon.svg"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/icon --expect 404 \
    --check detail contains 'icon not found' --save F28.icon/no-icon
  ```
  The SVG matches the fixture and carries
  `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`
  and `X-Content-Type-Options: nosniff`; `qa-f28-logs` declares no icon and
  gets 404.
- **Enable and disable (`F28.enable-disable`).** Toggle the browser-only app.
  ```sh
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-canvas --json '{"enabled": true}' \
    --expect 200 --check name eq qa-canvas --check enabled eq true --save F28.enable-disable/enable
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check enabled eq true
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json --check extensions.qa-canvas.enabled eq true
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-canvas --json '{"enabled": false}' \
    --expect 200 --check enabled eq false --save F28.enable-disable/disable
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check enabled eq false
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json --check extensions.qa-canvas.enabled eq false
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-canvas --json '{"enabled": "maybe"}' --expect 422
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-canvas --json '{}' --expect 422
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check enabled eq false
  ```
  Each PATCH answers `{"name": "qa-canvas", "enabled": <value>}` and both
  second views follow it; the bad bodies are 422 and change nothing. The app
  ends disabled.
- **Unknown and invalid names (`F28.unknown-name`).** `qa-ghost` was never
  installed; `Bad_Name` is not kebab-case.
  ```sh
  control-agent-server api GET /api/canvas-extensions/installed/qa-ghost --expect 404 \
    --check detail eq "Canvas extension 'qa-ghost' is not installed" --save F28.unknown-name/get
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-ghost --json '{"enabled": true}' --expect 404
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-ghost --json '{"enabled": false}' --expect 404
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-ghost --expect 404
  control-agent-server api GET /api/canvas-extensions/installed/qa-ghost/bundle --expect 404 --check detail contains 'bundle not found'
  control-agent-server api GET /api/canvas-extensions/installed/qa-ghost/icon --expect 404
  control-agent-server api GET /api/canvas-extensions/installed/Bad_Name --expect 422
  control-agent-server api PATCH /api/canvas-extensions/installed/Bad_Name --json '{"enabled": true}' --expect 422
  control-agent-server api DELETE /api/canvas-extensions/installed/Bad_Name --expect 422
  control-agent-server api GET /api/canvas-extensions/installed/Bad_Name/bundle --expect 422
  control-agent-server api GET /api/canvas-extensions/installed/Bad_Name/icon --expect 422
  control-agent-server api GET /api/canvas-extensions/installed/Bad_Name/backend --expect 422
  control-agent-server api POST /api/canvas-extensions/installed/Bad_Name/backend/prepare --json '{"revision": "qa-x"}' \
    --expect 422 --check detail.0.loc.1 eq extension_name
  control-agent-server api POST /api/canvas-extensions/installed/Bad_Name/backend/start --json '{"revision": "qa-x"}' \
    --expect 422 --check detail.0.loc.1 eq extension_name
  control-agent-server api POST /api/canvas-extensions/installed/Bad_Name/backend/stop --expect 422 --check detail.0.loc.1 eq extension_name
  control-agent-server api DELETE /api/canvas-extensions/installed/Bad_Name/backend/data --expect 422 --check detail.0.loc.1 eq extension_name
  control-agent-server api GET /api/canvas-extensions/installed/qa--x/backend/logs --expect 422
  control-agent-server api GET /api/canvas-extensions/installed --auth none --expect 401
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/bundle --auth none --expect 401
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/stop --auth none --expect 401
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/bundle --expect 200 --quiet
  ```
  404 `Canvas extension 'qa-ghost' is not installed` (`... bundle not found`,
  `... icon not found`), 422 for the bad names on all eleven routes that take a name
  (the backend ones blame `extension_name`, not the body), and 401 without the key while
  the same bundle request with the key is 200.
- **Discovery by listing (`F28.list-discovery`).** Copy a valid app into
  `installed/` by hand, as an image build might, then delete it by hand.
  ```sh
  cp -r "$FX" "$INST/qa-f28-hand"
  sed -i 's/"name": "qa-canvas"/"name": "qa-f28-hand"/' "$INST/qa-f28-hand/canvas-extension.json"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-hand --expect 404
  control-agent-server api GET /api/canvas-extensions/installed --expect 200 --check canvas_extensions len-eq 4 \
    --check canvas_extensions.3.name eq qa-f28-hand --check canvas_extensions.3.source eq local \
    --check canvas_extensions.3.enabled eq false --save F28.list-discovery/discovered
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-hand --expect 200 --check source eq local
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json --check extensions.qa-f28-hand.enabled eq false
  rm -rf "$INST/qa-f28-hand"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-hand --expect 404
  control-agent-server api GET /api/canvas-extensions/installed --check canvas_extensions len-eq 3 \
    --check canvas_extensions not-contains qa-f28-hand --save F28.list-discovery/pruned
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json --check extensions.qa-f28-hand missing
  ```
  Before a list call the copy is 404; the list registers it disabled with
  `source: "local"`, after which GET finds it. Once the directory is gone, GET
  is 404 and the next list removes it from `.installed.json`.
- **Broken installs (`F28.broken-install`).** Corrupt an installed app's
  manifest, restore it, then swap its entrypoint for a symlink out of the
  package.
  ```sh
  KX=$(control-agent-server fixture canvas-app --name qa-broken --print-path)
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$KX\"}" --expect 200 --quiet
  printf '{bad' > "$INST/qa-broken/canvas-extension.json"
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken --expect 200 --check manifest missing \
    --check version eq 0.1.0 --save F28.broken-install/bad-manifest
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/bundle --expect 404
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/icon --expect 404
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/backend --check state eq missing
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-broken --json '{"enabled": true}' --expect 200
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken --check enabled eq true --check manifest missing
  cp "$KX/canvas-extension.json" "$INST/qa-broken/canvas-extension.json"
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/bundle --expect 200 --quiet
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/icon --expect 200 --quiet
  printf '<svg xmlns="http://www.w3.org/2000/svg"/>\n' > "$F28FX/outside.svg"
  rm "$INST/qa-broken/assets/icon.svg" && ln -s "$F28FX/outside.svg" "$INST/qa-broken/assets/icon.svg"
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/icon --expect 404 \
    --check detail contains 'icon not found' --save F28.broken-install/symlink-icon
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken --check manifest.icon eq assets/icon.svg
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/bundle --expect 200 --quiet
  printf 'outside\n' > "$F28FX/outside.js"
  rm "$INST/qa-broken/dist/index.js" && ln -s "$F28FX/outside.js" "$INST/qa-broken/dist/index.js"
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken/bundle --expect 404 --save F28.broken-install/symlink-bundle
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken --check manifest missing
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-broken --expect 200 \
    --check message eq "Canvas extension 'qa-broken' uninstalled"
  control-agent-server api GET /api/canvas-extensions/installed/qa-broken --expect 404
  test ! -e "$INST/qa-broken"
  ```
  With an unparseable manifest the record is still served (version from the
  install record, `manifest: null`), bundle and icon are 404 and the backend
  is `missing`, but PATCH works. A restored manifest serves the bundle and
  icon again; an icon symlinked outside the package is 404 while the manifest
  (icon field included) and the bundle stay valid; an entrypoint symlinked
  outside the package is 404 at serve time and the manifest reads as null.
  DELETE still uninstalls it.
- **Backend status (`F28.backend-status`).** Read the lifecycle state of a
  missing, a browser-only and a backend app.
  ```sh
  control-agent-server api GET /api/canvas-extensions/installed/qa-ghost/backend --expect 200 --check state eq missing \
    --check revision eq null --check detail contains 'not installed' --save F28.backend-status/missing
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/backend --expect 200 --check state eq unsupported \
    --check revision matches '^sha256:[0-9a-f]{64}$' --check detail contains 'does not declare a backend' \
    --save F28.backend-status/unsupported
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --expect 200 --check state eq stopped \
    --check revision eq "$BHEAD" --check prepared_revision eq null --check pid eq null --check port eq null \
    --save F28.backend-status/stopped
  ```
  `missing` (with `revision` null) for the unknown name, `unsupported` with a
  `sha256:` revision for the local browser-only install, and `stopped` with the
  git commit as the revision for the `file://` install, nothing prepared.
- **Approval gate (`F28.backend-approval-gate`).** Try every shortcut around
  `prepare`.
  ```sh
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 409 --check detail eq 'backend revision must be prepared before start' --save F28.backend-approval-gate/unprepared
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json '{"revision": "qa-wrong"}' \
    --expect 409 --check detail contains 'does not match installed revision'
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json '{}' --expect 422
  cp "$INST/qa-backend/backend/linux.tar.gz" "$F28FX/qa-backend.tar.gz.bak"
  printf x >> "$INST/qa-backend/backend/linux.tar.gz"
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json "{\"revision\": \"$BHEAD\"}" \
    --expect 409 --check detail eq 'backend artifact checksum does not match manifest' --save F28.backend-approval-gate/tampered
  cp "$F28FX/qa-backend.tar.gz.bak" "$INST/qa-backend/backend/linux.tar.gz"
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-unsafe\"}" --expect 200 --quiet
  UREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-unsafe/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-unsafe/backend/prepare --json "{\"revision\": \"$UREV\"}" \
    --expect 409 --check detail eq 'backend artifact contains an unsafe archive entry' --save F28.backend-approval-gate/unsafe
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-remote\"}" --expect 200 --quiet
  RREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-remote/backend --check state eq stopped --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-remote/backend/prepare --json "{\"revision\": \"$RREV\"}" \
    --expect 409 --check detail eq 'backend artifact download failed' --save F28.backend-approval-gate/download-failed
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check prepared_revision eq null
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-unsafe/backend --check prepared_revision eq null
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-remote/backend --check prepared_revision eq null
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/approvals/*' --expect-count 0
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/artifacts/**' --expect-count 0
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/downloads/*' --expect-count 0
  test -z "$(find "$P" -name qa-f28-escape)"
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-f28-remote --expect 200 --quiet
  ```
  Each attempt is 409 with its reason, the bodiless prepare is 422, no
  approval file exists, no artifact file was extracted or downloaded and the
  escaping member was never written. `qa-f28-remote` declares a remote
  (`url`) artifact on a closed loopback HTTPS port, so its prepare takes the
  download path and fails there. The tampered archive is restored and the
  remote app uninstalled afterwards, so the catalog counts below are
  unchanged.
- **Prepare (`F28.backend-prepare`).** Approve the installed revision.
  ```sh
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq stopped --check prepared_revision eq "$BHEAD" --save F28.backend-prepare/prepare
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check prepared_revision eq "$BHEAD"
  control-agent-server state cat home/.openhands/canvas-extensions/backends/approvals/qa-backend.json \
    --check revision eq "$BHEAD" --check artifact_sha256 eq "$BSHA" --check platform matches '^linux-(amd64|arm64)$' \
    --check artifact_dir eq "$BK/artifacts/qa-backend/$BSHA"
  control-agent-server state cat "home/.openhands/canvas-extensions/backends/artifacts/qa-backend/$BSHA/server.py" \
    --mode 555 --contains 'web.run_app' --max-chars 80
  test "$(stat -c %a "$BK/artifacts/qa-backend/$BSHA")" = 555
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check prepared_revision eq "$BHEAD" --expect-max-ms 3000
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/artifacts/**' --expect-count 1
  ```
  `prepared_revision` equals the revision in the response, the GET and
  `approvals/qa-backend.json` (with the manifest's sha256); `server.py` is
  extracted as `r-xr-xr-x` into a `dr-xr-xr-x` directory named after the
  sha256, and a second prepare answers at once without a second extraction.
- **Start (`F28.backend-start`).** Start the prepared revision of the
  (still disabled) app and look at the process it runs.
  ```sh
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq ready --check pid exists --check port exists --check detail eq null \
    --save F28.backend-start/start
  BPID=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check state eq ready --field pid)
  BPORT=$(control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --field port)
  test "$(curl -fsS "http://127.0.0.1:$BPORT/health")" = ok
  curl -fsS "http://127.0.0.1:$BPORT/qa-echo" | grep -F "\"data_dir\": \"$BK/data/qa-backend\""
  test "$(tr '\0' '\n' < "/proc/$BPID/environ")" = "HOME=$BK/data/qa-backend/home"
  test "$(readlink "/proc/$BPID/cwd")" = "$(cd "$INST/qa-backend" && pwd -P)"
  test "$(ps -o pgid= -p "$BPID" | tr -d ' ')" = "$BPID"
  test "$(pgrep -f "$BK/artifacts/qa-backend/")" = "$BPID"
  test -d "$BK/data/qa-backend/home"
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq ready --check pid eq "$BPID"
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json '{"revision": "qa-other"}' \
    --expect 409 --check detail eq 'a different backend revision is already running'
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$FX\", \"force\": true}" \
    --expect 409 --check detail contains 'Stop running Canvas App backends' --save F28.backend-start/force-blocked
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-backend/backend/data \
    --expect 409 --check detail eq 'stop the backend before deleting its data'
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check installed_at eq "$AT0"
  kill -0 "$BPID"
  ```
  `ready` with a pid and port; the backend answers `/health` and reports its
  `{data_dir}`; its environment is exactly `HOME=<data>/qa-backend/home` (no
  session or secret keys), its cwd is the installed package and it leads its
  own process group. It is the only process whose command line holds the
  run's `artifacts/qa-backend/` path, which is what the later `pgrep` checks
  for leftover processes rely on. A repeated start returns the same pid, another revision
  is 409, and a forced install of any app and a data deletion are 409 while
  it runs. Starting does not require the app to be enabled.
- **Stop (`F28.backend-stop`).** Stop the running backend twice.
  ```sh
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/stop --expect 200 \
    --check state eq stopped --check pid eq null --check port eq null --expect-max-ms 7000 --save F28.backend-stop/stop
  if kill -0 "$BPID" 2>/dev/null; then false; fi
  if curl -s -m 2 "http://127.0.0.1:$BPORT/health"; then false; fi
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check state eq stopped \
    --check prepared_revision eq "$BHEAD"
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/stop --expect 200 --check state eq stopped
  control-agent-server api POST /api/canvas-extensions/installed/qa-ghost/backend/stop --expect 200 --check state eq missing
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-flaky\"}" --expect 200 --quiet
  KREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/prepare --json "{\"revision\": \"$KREV\"}" --expect 200 --quiet
  KPORT=$(control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/start --json "{\"revision\": \"$KREV\"}" \
    --expect 200 --check state eq ready --field port)
  KCHILD=$(curl -fsS "http://127.0.0.1:$KPORT/fork")
  kill -0 "$KCHILD"
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/stop --expect 200 --check state eq stopped \
    --expect-max-ms 7000 --save F28.backend-stop/group
  if kill -0 "$KCHILD" 2>/dev/null; then false; fi
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-f28-flaky --expect 200 --quiet
  ```
  The process is gone and its port refuses connections; the approval stays,
  and stopping again (or stopping an unknown app) is a 200 no-op. A
  `/bin/sleep` child that `qa-f28-flaky` forked while its leader keeps
  running dies with the group on `stop`; that app is uninstalled again so
  the catalog counts below are unchanged.
- **Reinstall (`F28.force-reinstall`).** Enable the browser-only app,
  install it again without `force`, then change its bundle at the source and
  reinstall both apps with `force`.
  ```sh
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-canvas --json '{"enabled": true}' --expect 200
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$FX\"}" \
    --expect 409 --check detail eq 'Canvas extension already installed. Use force=true to overwrite.' \
    --save F28.force-reinstall/conflict
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check installed_at eq "$AT0" --check enabled eq true
  printf 'export const qaCanvasApp = "qa-f28-v2";\n' > "$FX/dist/index.js"
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$FX\", \"force\": true}" \
    --expect 200 --check enabled eq true --check installed_at ne "$AT0" --save F28.force-reinstall/force
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check enabled eq true --check installed_at ne "$AT0"
  BUNDLE2="$AGENT_SERVER_VERIFY_RUN/fixtures/f28/bundle-v2.js"
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas/bundle --expect 200 \
    --check-header etag ne "$ETAG1" --raw-out "$BUNDLE2" --quiet
  cmp "$BUNDLE2" "$FX/dist/index.js"
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$BX\", \"force\": true}" \
    --expect 200 --check enabled eq false --check resolved_ref eq "$BHEAD" --save F28.force-reinstall/force-backend
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check state eq stopped \
    --check prepared_revision eq null --save F28.force-reinstall/approval-gone
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/approvals/qa-backend.json' --expect-count 0
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 409 --check detail contains 'must be prepared'
  ```
  Without `force` the install is 409 and the record keeps its `installed_at`.
  The forced install of the enabled app comes back enabled with a new
  `installed_at`, and the bundle serves the new bytes under a new ETag. The
  forced install of the backend app keeps it disabled, deletes its approval
  (`prepared_revision` null, no approval file) and so refuses `start` until
  the next `prepare`.
- **Logs while starting (`F28.backend-logs`).** `qa-f28-logs` prints and
  never answers its health check (30 s timeout); start it in the background,
  read its logs while it is `starting`, then stop it.
  ```sh
  LREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-logs/backend/prepare --json "{\"revision\": \"$LREV\"}" \
    --expect 200 --check prepared_revision eq "$LREV" --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-logs/backend/start --json "{\"revision\": \"$LREV\"}" \
    --expect 200 --check state eq stopped --save F28.backend-logs/abandoned-start > /dev/null &
  START_JOB=$!
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend --until-ok 15 \
    --check state eq starting --check pid exists --save F28.backend-logs/starting
  LPID=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend --field pid)
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend/logs --until-ok 15 \
    --check logs contains '[stdout] qa-f28-out' --check logs contains '[stderr] qa-f28-err' --check truncated eq false \
    --save F28.backend-logs/logs
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-logs/backend/stop --expect 200 \
    --check state eq stopped --expect-max-ms 7000
  wait "$START_JOB"
  if kill -0 "$LPID" 2>/dev/null; then false; fi
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend/logs --expect 200 \
    --check logs contains qa-f28-out --check logs contains qa-f28-err
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend/logs --query limit_bytes=8 \
    --expect 200 --check logs len-eq 8 --check truncated eq true --save F28.backend-logs/limited
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend/logs --query limit_bytes=0 --expect 422
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend/logs --query limit_bytes=262145 --expect 422
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend/logs --query limit_bytes=262144 --expect 200
  control-agent-server api GET /api/canvas-extensions/installed/qa-ghost/backend/logs --expect 200 \
    --check logs eq '' --check truncated eq false
  ```
  The status reads `starting` with a pid while the health wait runs, and the
  logs hold `[stdout] qa-f28-out` and `[stderr] qa-f28-err`. `stop` returns in
  well under the 30 s health timeout, the pending start then answers 200
  `stopped`, the process is gone and the logs are still readable;
  `limit_bytes=8` returns the last 8 bytes with `truncated: true`, and 0 and
  262145 are 422.
- **TypeScript client (`F28.ts-client`).** Every backend method of the built
  `CanvasExtensionsClient`, on a backend app of its own, then a `startBackend`
  that outlasts the client's timeout on `qa-f28-logs` (prepared above; it
  never passes its 30 s health check).
  ```sh
  TX=$(control-agent-server fixture canvas-app --name qa-f28-ts --backend ok --print-path)
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$TX\"}" --expect 200 --quiet
  cat > "$F28FX/ts_canvas.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { CanvasExtensionsClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const options = { host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY };
  const client = new CanvasExtensionsClient(options);
  const httpError = (status, detail) => (err) =>
    err.name === 'HttpError' && err.status === status && (detail === undefined || err.detail === detail);
  const app = 'qa-f28-ts';
  let status = await client.getBackendStatus(app);
  assert.equal(status.state, 'stopped');
  assert.equal(status.prepared_revision, null);
  const revision = status.revision;
  await assert.rejects(client.startBackend(app, revision), httpError(409, 'backend revision must be prepared before start'));
  await assert.rejects(client.prepareBackend(app, 'qa-wrong'),
    httpError(409, 'approval revision does not match installed revision'));
  assert.equal((await client.prepareBackend(app, revision)).prepared_revision, revision);
  status = await client.startBackend(app, revision);
  assert.equal(status.state, 'ready');
  assert.ok(status.pid > 0 && status.port > 0);
  const pid = status.pid;
  assert.equal((await client.getBackendStatus(app)).pid, pid);
  const logs = await client.getBackendLogs(app);
  assert.equal(logs.name, app);
  assert.equal(typeof logs.logs, 'string');
  await assert.rejects(client.deleteBackendData(app), httpError(409, 'stop the backend before deleting its data'));
  status = await client.stopBackend(app);
  assert.equal(status.state, 'stopped');
  assert.equal(status.pid, null);
  assert.equal((await client.deleteBackendData(app)).state, 'stopped');
  await assert.rejects(client.getBackendStatus('Bad_Name'), httpError(422));
  assert.equal((await client.getBackendStatus('qa-ghost')).state, 'missing');

  const slow = 'qa-f28-logs';
  const slowRevision = (await client.getBackendStatus(slow)).revision;
  assert.equal((await client.prepareBackend(slow, slowRevision)).prepared_revision, slowRevision);
  const impatient = new CanvasExtensionsClient({ ...options, timeout: 2000 });
  const started = Date.now();
  await assert.rejects(impatient.startBackend(slow, slowRevision),
    (err) => err.name !== 'HttpError' && err.message === 'Request timeout after 2000ms');
  assert.ok(Date.now() - started < 10000);
  status = await client.getBackendStatus(slow);
  assert.equal(status.state, 'starting');
  assert.ok(status.pid > 0);
  let slowLogs;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    slowLogs = await client.getBackendLogs(slow);
    if (slowLogs.logs.includes('[stderr] qa-f28-err') && slowLogs.logs.includes('[stdout] qa-f28-out')) break;
    await new Promise((done) => setTimeout(done, 200));
  }
  assert.ok(slowLogs.logs.includes('[stdout] qa-f28-out') && slowLogs.logs.includes('[stderr] qa-f28-err'));
  const tail = await client.getBackendLogs(slow, 8);
  assert.equal(tail.logs.length, 8);
  assert.equal(tail.truncated, true);
  assert.equal((await client.getBackendStatus(slow)).state, 'starting');
  assert.equal((await client.stopBackend(slow)).state, 'stopped');
  client.close();
  impatient.close();
  console.log(`QA_F28_TS_OK ready_pid=${pid} starting_pid=${status.pid}`);
  JS
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  control-agent-server exec --timeout 120 --expect-output QA_F28_TS_OK --save F28.ts-client/program -- node "$F28FX/ts_canvas.mjs"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-logs/backend --check state eq stopped --check pid eq null
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-ts/backend --check state eq stopped \
    --check prepared_revision exists --check pid eq null
  if pgrep -f "$BK/artifacts/qa-f28-logs/"; then false; fi
  if pgrep -f "$BK/artifacts/qa-f28-ts/"; then false; fi
  test ! -e "$BK/data/qa-f28-ts"
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-f28-ts --expect 200 --quiet
  ```
  The program prints `QA_F28_TS_OK`: `getBackendStatus`, `prepareBackend`,
  `startBackend`, `getBackendLogs`, `stopBackend` and `deleteBackendData`
  return the states the REST bullets assert (`stopped`, prepared, `ready`
  with the same pid on a second read, `stopped` without a pid), and the
  refusals reject with an `HttpError` carrying the server's status and
  detail (409 unprepared start, wrong revision and data deletion while
  running; 422 for a bad name). A client built with `timeout: 2000` rejects
  `startBackend` after 2 s with `Request timeout after 2000ms` (a plain
  `Error`, not an `HttpError`), while the server keeps the backend
  `starting` with its logs readable until `stopBackend`: the same happens to
  the default 60 s timeout against a manifest whose `health.timeout_seconds`
  is longer (up to 300). The server-side reads confirm both backends are
  gone and the data directory deleted; the app is uninstalled, so the
  catalog counts below are unchanged.
- **Failed starts (`F28.backend-start-fails`).** `qa-unhealthy` answers its
  health check with 503 (15 s timeout); `qa-exit` exits with code 3.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$UX\"}" --expect 200 --quiet
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$EX\"}" --expect 200 --quiet
  HREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-unhealthy/backend --field revision)
  XREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-exit/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-unhealthy/backend/prepare --json "{\"revision\": \"$HREV\"}" --expect 200 --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-exit/backend/prepare --json "{\"revision\": \"$XREV\"}" --expect 200 --quiet
  T0=$SECONDS
  control-agent-server api POST /api/canvas-extensions/installed/qa-unhealthy/backend/start --json "{\"revision\": \"$HREV\"}" \
    --expect 200 --check state eq unhealthy --check detail eq 'backend health check timed out' --check pid eq null \
    --expect-max-ms 25000 --save F28.backend-start-fails/timeout
  test $((SECONDS - T0)) -ge 14
  control-agent-server api GET /api/canvas-extensions/installed/qa-unhealthy/backend --check state eq unhealthy --check pid eq null
  if pgrep -f "$BK/artifacts/qa-unhealthy/"; then false; fi
  control-agent-server api POST /api/canvas-extensions/installed/qa-exit/backend/start --json "{\"revision\": \"$XREV\"}" \
    --expect 200 --check state eq unhealthy --check detail eq 'backend exited with code 3' --check pid eq null \
    --save F28.backend-start-fails/exit
  control-agent-server api GET /api/canvas-extensions/installed/qa-exit/backend --check state eq unhealthy \
    --check detail eq 'backend exited with code 3'
  if pgrep -f "$BK/artifacts/qa-exit/"; then false; fi
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-unhealthy/backend/data --expect 200 --check state eq unhealthy
  test ! -e "$BK/data/qa-unhealthy"
  ```
  The unhealthy backend's start blocks for about the 15 s health timeout and
  ends `unhealthy` / `backend health check timed out`; the exiting one ends
  `unhealthy` / `backend exited with code 3`. No process of either remains,
  and the failed backend's data can be deleted.
- **Inherited environment (`F28.backend-inherit-environment`).** Install the
  two apps whose `inherit_environment` the manifest schema refuses, then
  start `qa-f28-env`, which asks for `PATH`, `TZ` and `LANG`, and compare its
  environment with the server process's own.
  ```sh
  for BADENV in secret dup; do
    control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-env-$BADENV\"}" \
      --expect 422 --check detail contains 'Invalid canvas extension' --save "F28.backend-inherit-environment/refused-$BADENV"
    control-agent-server api GET "/api/canvas-extensions/installed/qa-f28-env-$BADENV" --expect 404
    test ! -e "$INST/qa-f28-env-$BADENV"
  done
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-env\"}" --expect 200 \
    --check manifest.backend.inherit_environment len-eq 3 --save F28.backend-inherit-environment/install
  VREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-env/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-env/backend/prepare --json "{\"revision\": \"$VREV\"}" --expect 200 --quiet
  VPID=$(control-agent-server api POST /api/canvas-extensions/installed/qa-f28-env/backend/start --json "{\"revision\": \"$VREV\"}" \
    --expect 200 --check state eq ready --field pid)
  SPID=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["pid"])' "$AGENT_SERVER_VERIFY_RUN/run.json")
  test "$(tr '\0' '\n' < "/proc/$SPID/cmdline" | grep -cx openhands.agent_server)" = 1
  test "$(tr '\0' '\n' < "/proc/$SPID/environ" | grep -c '^OH_SECRET_KEY=')" = 1
  test "$(tr '\0' '\n' < "/proc/$SPID/environ" | grep -c '^PATH=')" = 1
  EXPECTED_ENV=$( (echo "HOME=$BK/data/qa-f28-env/home"; tr '\0' '\n' < "/proc/$SPID/environ" | grep -E '^(PATH|TZ|LANG)=') | sort)
  tr '\0' '\n' < "/proc/$VPID/environ" | cut -d= -f1 | sort | paste -sd ' ' -
  test "$(tr '\0' '\n' < "/proc/$VPID/environ" | sort)" = "$EXPECTED_ENV"
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-env/backend/stop --expect 200 --check state eq stopped --quiet
  sed -i 's/"LANG"/"OH_SECRET_KEY"/' "$INST/qa-f28-env/canvas-extension.json"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-env --check manifest missing
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-env/backend/start --json "{\"revision\": \"$VREV\"}" \
    --expect 200 --check state eq missing --check pid eq null --save F28.backend-inherit-environment/edited-on-disk
  if pgrep -f "$BK/artifacts/qa-f28-env/"; then false; fi
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-f28-env --expect 200 --quiet
  ```
  Both bad manifests are 422 `Invalid canvas extension ...` and leave no
  install: `OH_SECRET_KEY` is outside the allow-list (`LANG`, `LC_ALL`,
  `LC_CTYPE`, `PATH`, `TMPDIR`, `TZ`) even next to an allowed name, and a
  name listed twice is refused too. The started backend's environment is
  exactly `HOME=<data>/qa-f28-env/home` plus the server's own values of the
  requested names it has (`PATH` always, `TZ=UTC` from the family's
  `Launch:` line, `LANG` only when the server has one), while the server
  process holds `OH_SECRET_KEY` (checked without printing it); the printed
  line lists the backend's variable names only. The app is stopped and
  uninstalled, so the catalog counts below are unchanged. Before that, its
  installed manifest is edited on disk to ask for `OH_SECRET_KEY`: it stops
  validating (`manifest: null`), so `start` answers `missing` and runs
  nothing; the start-time check in `_environment` is never reached through
  the API.
- **Disable stops the backend (`F28.disable-stops-backend`).** Prepare again
  (the forced install revoked the approval), enable, start, then disable.
  ```sh
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/prepare --json "{\"revision\": \"$BHEAD\"}" --expect 200 --quiet
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --json '{"enabled": true}' --expect 200
  DPID=$(control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq ready --field pid)
  kill -0 "$DPID"
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --json '{"enabled": false}' \
    --expect 200 --check enabled eq false --expect-max-ms 7000 --save F28.disable-stops-backend/disable
  if kill -0 "$DPID" 2>/dev/null; then false; fi
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check state eq stopped --check pid eq null \
    --save F28.disable-stops-backend/status
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend --check enabled eq false
  ```
  The disable answers 200 only after the backend's group is gone; the status
  is `stopped`.
- **Persistence across a restart (`F28.restart-persistence`).** Enable and
  start the backend app, then restart the server gracefully.
  ```sh
  control-agent-server api PATCH /api/canvas-extensions/installed/qa-backend --json '{"enabled": true}' --expect 200
  RPID=$(control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq ready --field pid)
  control-agent-server restart
  if kill -0 "$RPID" 2>/dev/null; then false; fi
  control-agent-server api GET /api/canvas-extensions/installed --check canvas_extensions len-eq 6 --quiet
  control-agent-server api GET /api/canvas-extensions/installed/qa-canvas --check enabled eq true --save F28.restart-persistence/canvas
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend --check enabled eq true --check resolved_ref eq "$BHEAD"
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check state eq stopped \
    --check prepared_revision eq "$BHEAD" --check pid eq null --save F28.restart-persistence/backend
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq ready --check pid ne "$RPID" --save F28.restart-persistence/start-again
  control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/stop --expect 200 --check state eq stopped
  ```
  The graceful shutdown stopped the backend; the six installs, both
  `enabled: true` values and the approval come back, the backend reads
  `stopped`, and `start` succeeds with a new pid without another `prepare`.
- **Uninstall (`F28.uninstall`).** Start the backend, uninstall the app, then
  delete its data.
  ```sh
  QPID=$(control-agent-server api POST /api/canvas-extensions/installed/qa-backend/backend/start --json "{\"revision\": \"$BHEAD\"}" \
    --expect 200 --check state eq ready --field pid)
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-backend --expect 200 \
    --check message eq "Canvas extension 'qa-backend' uninstalled" --save F28.uninstall/delete
  if kill -0 "$QPID" 2>/dev/null; then false; fi
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend --expect 404
  control-agent-server api GET /api/canvas-extensions/installed --check canvas_extensions len-eq 5 \
    --check canvas_extensions not-contains qa-backend --quiet
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json --check extensions.qa-backend missing --max-chars 100
  test ! -e "$INST/qa-backend"
  control-agent-server api GET /api/canvas-extensions/installed/qa-backend/backend --check state eq missing \
    --check prepared_revision eq "$BHEAD" --save F28.uninstall/backend-missing
  test -d "$BK/data/qa-backend/home"
  test -d "$BK/artifacts/qa-backend/$BSHA"
  control-agent-server state cat home/.openhands/canvas-extensions/backends/approvals/qa-backend.json --check revision eq "$BHEAD"
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-backend --expect 404
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-backend/backend/data --expect 200 \
    --check state eq missing --save F28.uninstall/delete-data
  test ! -e "$BK/data/qa-backend"
  ```
  The uninstall stops the running backend and removes the package and its
  record; the backend reads `missing` while the data directory, the extracted
  artifact and the approval survive, a second DELETE is 404, and the data
  route then removes `backends/data/qa-backend`.
- **Non-gzip artifact (`F28.prepare-corrupt-archive`), known bug.** An
  artifact whose bytes match the manifest's sha256 but are not a gzip archive
  should be refused like every other bad artifact (409). Today
  `tarfile.ReadError` escapes the router's `ValueError` mapping and the
  request fails with 500 `not a gzip file`; nothing is written either way.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-notgz\"}" --expect 200 --quiet
  NREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-notgz/backend --field revision)
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-notgz/backend --check state eq stopped
  test "$(sha256sum "$INST/qa-f28-notgz/backend/linux.tar.gz" | cut -d' ' -f1)" = \
    "$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["backend"]["artifacts"]["linux-amd64"]["sha256"])' "$INST/qa-f28-notgz/canvas-extension.json")"
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-notgz/backend/prepare --json "{\"revision\": \"$NREV\"}" \
    --expect 409 --save F28.prepare-corrupt-archive/prepare  # bug
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/approvals/qa-f28-notgz.json' --expect-count 0
  control-agent-server state ls 'home/.openhands/canvas-extensions/backends/artifacts/qa-f28-notgz/**' --expect-count 0
  ```
  The installed artifact's sha256 matches the manifest, so the checksum gate
  passes and only the archive format is wrong. Expected 409 with a reason and
  no approval; actual 500
  `{"detail": "Internal Server Error", "exception": "not a gzip file"}`
  (`tarfile.ReadError` from `_extract_archive` is not a `ValueError`, the only
  exception the prepare route maps to 409). The `# bug` line is the prepare.
- **Unknown ref on a cached source (`F28.install-ref-cached`), known bug.**
  Pin `qa-f28-git` (which caches its clone), then force a ref that does not
  exist.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$GX\", \"ref\": \"$GV1\", \"force\": true}" \
    --expect 200 --check resolved_ref eq "$GV1" --quiet
  control-agent-server state ls 'home/.openhands/cache/extensions/qa-f28-git-*/canvas-extension.json' --expect-count 1
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"file://$GX\", \"ref\": \"qa-no-such-ref\", \"force\": true}" \
    --expect 400 --check detail contains 'Could not read canvas extension source' --save F28.install-ref-cached/unknown-ref  # bug
  control-agent-server state cat home/.openhands/canvas-extensions/installed/.installed.json \
    --check extensions.qa-f28-git.requested_ref eq "$GV1" --check extensions.qa-f28-git.resolved_ref eq "$GV1"  # bug
  control-agent-server api DELETE /api/canvas-extensions/installed/qa-f28-git --expect 200 --quiet
  ```
  The pinned install leaves exactly one cached clone of the source. Expected:
  400 as on a first fetch (`F28.install-ref`), with the pinned install
  untouched. Actual: 200 at the first `# bug` line; the SDK's cached clone
  logs `Failed to checkout qa-no-such-ref ... Using cached version`
  (`openhands/sdk/git/cached_repo.py` `_update_repository`), the forced
  install replaces the app with whatever commit the cache had checked out,
  and `.installed.json` records `requested_ref: "qa-no-such-ref"` next to
  that commit (the second `# bug` line, for a fix that answers 400 but still
  writes the record). Once fixed, the bullet uninstalls the app; while the
  bug lasts the app stays installed, which no later bullet counts.
- **Health probe latch (`F28.status-probe-latch`), known bug.** Make the
  running backend answer one status probe slower than the 1 s probe timeout,
  then fast again.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-flaky\"}" --expect 200,409 --quiet
  FREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/prepare --json "{\"revision\": \"$FREV\"}" --expect 200 --quiet
  FPORT=$(control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/start --json "{\"revision\": \"$FREV\"}" \
    --expect 200 --check state eq ready --field port)
  curl -fsS "http://127.0.0.1:$FPORT/slow"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --expect 200 --check pid exists \
    --save F28.status-probe-latch/slow-probe
  curl -fsS "http://127.0.0.1:$FPORT/fast"
  test "$(curl -fsS -m 3 "http://127.0.0.1:$FPORT/health")" = ok
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --until-ok 5 --check state eq ready \
    --save F28.status-probe-latch/after-recovery  # bug
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/stop --expect 200 --check state eq stopped --quiet
  ```
  Expected: once `/health` answers in time again (the direct `curl` shows it
  does), the status returns to `ready` (whether or not the slow probe was
  reported). The slow-probe status is not asserted, so a fix that tolerates
  one slow probe passes too. Actual: the slow probe reads `unhealthy`
  ("Backend health probe failed", saved as `slow-probe`) and that latches
  while the process keeps serving, so the `# bug` line keeps reading
  `unhealthy` for its 5 s; later status calls never re-probe, `start` with
  the same revision returns the same `unhealthy` pid without restarting it, a
  forced install stays refused with 409, and only `stop` followed by `start`
  recovers. While the bug lasts the backend is left running; the next bullet
  stops it first.
- **Status hangs after the leader exits (`F28.status-exit-hang`), known bug.**
  The backend forks a child that inherits its stdout, then its leader exits.
  ```sh
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$F28FX/qa-f28-flaky\"}" --expect 200,409 --quiet
  FREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/prepare --json "{\"revision\": \"$FREV\"}" --expect 200 --quiet
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/stop --expect 200 --quiet
  FPORT=$(control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/start --json "{\"revision\": \"$FREV\"}" \
    --expect 200 --check state eq ready --field port)
  FPID=$(control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --check state eq ready --field pid)
  CHILD=$(curl -fsS "http://127.0.0.1:$FPORT/fork")
  trap 'kill "$CHILD" 2>/dev/null || true' EXIT
  kill -0 "$CHILD"
  curl -s -m 3 "http://127.0.0.1:$FPORT/exit" || true
  for _ in $(seq 50); do kill -0 "$FPID" 2>/dev/null || break; sleep 0.1; done
  if kill -0 "$FPID" 2>/dev/null; then false; fi
  kill -0 "$CHILD"
  control-agent-server api GET /api/canvas-extensions/installed/qa-f28-flaky/backend --until-ok 10 --timeout 10 \
    --check state eq unhealthy --check detail eq 'Backend exited with code 0' --save F28.status-exit-hang/status  # bug
  control-agent-server api POST /api/canvas-extensions/installed/qa-f28-flaky/backend/stop --timeout 10 --expect 200 --check state eq stopped
  if kill -0 "$CHILD" 2>/dev/null; then false; fi  # bug
  ```
  The leader is gone (the server reaped it) while the forked `/bin/sleep`
  child still runs. Expected: the status reports the exit (`unhealthy`,
  `Backend exited with code 0`) at once and `stop` kills the leftover child.
  `--until-ok` covers a status call that lands before the server noticed the
  exit (it would read `Backend health probe failed` once). An `api` call that
  times out ends the retries, so the hang fails the `# bug` line with
  `timed out`. Actual: the status call waits for the child to close the
  pipes (here up to 300 s) while it holds the app's lock, so `stop`, `logs`,
  `prepare`, `start` and the data route for that app all time out behind it
  and the child keeps running. The last line is marked too: a fix that only
  stops the status call from waiting but leaves the child alive after `stop`
  is still this bug. An exit trap kills the bullet's own child, which
  releases the lock.
- **Backend outlives a crash (`F28.hard-restart-orphan`), known bug.** Start a
  backend, SIGKILL the server and restart it on the same state.
  ```sh
  OX=$(control-agent-server fixture canvas-app --name qa-orphan --backend ok --print-path)
  control-agent-server api POST /api/canvas-extensions/install --json "{\"source\": \"$OX\"}" --expect 200 --quiet
  OREV=$(control-agent-server api GET /api/canvas-extensions/installed/qa-orphan/backend --field revision)
  control-agent-server api POST /api/canvas-extensions/installed/qa-orphan/backend/prepare --json "{\"revision\": \"$OREV\"}" --expect 200 --quiet
  OPID=$(control-agent-server api POST /api/canvas-extensions/installed/qa-orphan/backend/start --json "{\"revision\": \"$OREV\"}" \
    --expect 200 --check state eq ready --field pid)
  test "$(pgrep -f "$BK/artifacts/qa-orphan/")" = "$OPID"
  control-agent-server restart --hard
  control-agent-server api GET /api/canvas-extensions/installed/qa-orphan/backend --check state eq stopped \
    --check pid eq null --save F28.hard-restart-orphan/status-after-crash
  if pgrep -f "$BK/artifacts/qa-orphan/"; then false; fi  # bug
  ```
  Expected: no backend process of the dead server remains once the server is
  back (reaped at startup, or tied to the server's lifetime). Actual: the
  backend runs in its own session (`start_new_session=True`), so it survives
  the SIGKILL with parent pid 1; the new server reports `stopped`, a new
  `start` launches a second process on the same data directory, and
  `DELETE .../backend/data` succeeds under the live orphan.
  `control-agent-server stop` reaps it (`orphans_reaped`). The leftover check
  (the `# bug` line, which prints the survivor's pid) matches the run-private
  artifact path rather than the pid, so a recycled pid cannot fake a
  survivor; the `pgrep` before the crash shows the pattern finds the live
  backend. This is the last bullet because its orphan stays up until `stop`.

## Gotchas

- `file://` is a git URL, not a local path: `file:///plain/dir` that is not a
  repository is 400. Use a bare absolute path for a local install. Relative
  and `~` sources resolve against the server's cwd and private `HOME`, not the
  caller's.
- A fresh install is always disabled and the body has no `enabled` field
  (extra fields are ignored). `force` keeps the previous `enabled` value but
  deletes the backend approval, and is refused with 409 while any app's
  backend is starting or running, not only this app's.
- `GET /installed` writes: it registers valid hand-copied directories
  (disabled, `source: "local"`) and prunes records whose directory is gone.
  GET by name, bundle, icon and the backend routes do not discover; they 404
  (or report `missing`) until a list call ran.
- `ref` goes through the SDK's cached git clone (`cache/extensions/<name>-<hash>`),
  which falls back to the cached checkout when the ref cannot be checked out:
  an unknown ref is 400 only on the first fetch of a source
  (`F28.install-ref-cached`). Every git fetch failure reads
  `Could not read canvas extension source: Failed to fetch extension from <url>`
  without naming the ref, although the router avoids "failed to fetch" wording
  on purpose (the Canvas client treats it as a network outage).
- Every read re-validates the installed manifest and the entrypoint's
  containment; `manifest: null` is the signal that an install is broken on
  disk. A non-`.svg` icon is silently dropped at parse time and the app stays
  valid.
- The backend routes never 404: an unknown or broken app reports `missing`,
  a browser-only app `unsupported`, and prepare/start/stop/logs/data answer
  200 with that state. Only a non-kebab-case name is 422.
- The revision is the git commit for git installs and
  `sha256:<hash of the manifest JSON>` for local ones, so editing an installed
  manifest of a local install changes the revision (and needs a new prepare),
  while a git install's revision does not change.
- `prepare` and `start` do not consult `enabled`: a disabled app can be
  prepared and started (the README makes `prepare` with the exact revision the
  explicit gate; disabling and uninstalling stop the backend). `prepare` also
  does not check whether a backend is running.
- `start` blocks until the health check passes or `health.timeout_seconds`
  (up to 300) runs out, so give `api` a long enough `--timeout` (default 60 s)
  for slow fixtures. `stop` abandons a pending start, which then answers
  `stopped`.
- Backends get no `PATH` unless they ask: the environment is only
  `HOME=<data>/<name>/home` plus the server's values of the names
  `inherit_environment` lists from its allow-list (`LANG`, `LC_ALL`,
  `LC_CTYPE`, `PATH`, `TMPDIR`, `TZ`; anything else is a 422 at install,
  `F28.backend-inherit-environment`), so scripts need absolute shebangs and
  binaries.
  Python's stdout to a pipe is block-buffered: flush, or `logs` stays empty.
  The CLI's aiohttp fixture prints nothing, which is why the logs bullet uses
  its own shell backend.
- Logs are kept after a stop or a failed start and cleared by the next
  `start`; each read chunk (not each line) is prefixed with `[stdout] ` or
  `[stderr] `.
- Extracted artifacts are read-only (`0555`) and never garbage-collected, not
  even on uninstall; `DELETE .../backend/data` removes only `backends/data/<name>`.
  A non-root `rm -rf` of the run's state needs `chmod -R u+w` on
  `canvas-extensions/backends/artifacts` first.
- Backends run in their own session, so a SIGKILLed server leaves them
  running (`F28.hard-restart-orphan`); a graceful stop or `restart` stops them.
  `control-agent-server stop` reaps leftovers whose `HOME` is inside the run.
- Every `GET .../backend` of a `ready` backend probes its health path with a
  1 s timeout. One slow answer latches `unhealthy` until `stop` and `start`
  (`F28.status-probe-latch`), so fixture backends must answer `/health` from
  a threaded server, never behind a slow request. A backend whose children
  keep its stdout open after the leader dies wedges that app's lifecycle
  routes (`F28.status-exit-hang`); `exec` into the real server or redirect
  children's output.
- A pending `start` that another command must observe runs as a
  backgrounded `api` call (`... &`, then `START_JOB=$!`) and is collected
  with `wait "$START_JOB"`, so its own `--expect`/`--check` still fail the
  bullet (`F28.backend-logs`).
- Probing a backend directly (`curl 127.0.0.1:<port>`, `/proc/<pid>`) needs a
  Linux host where the CLI and the server share a network and pid namespace;
  an attached remote server cannot be checked this way. The browser route to
  a backend (`/app-backends/...`) belongs to the bridge family.
