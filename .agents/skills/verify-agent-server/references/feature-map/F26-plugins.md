# Plugins catalog, install and marketplace

Plugins bundle skills, slash commands (exposed as `<plugin>:<command>`
keyword skills), sub-agents, hooks and MCP servers. A consumer lists the
plugins a conversation would load on its own (user directories, the project's
`.agents/plugins` and `.openhands/plugins`, and enabled installed plugins),
installs plugins from a local path or a git source into the server's store,
enables, disables, refreshes and uninstalls them, and reads the plugins-only
catalog of the public extensions marketplace. Enabled installed plugins are
merged into every new conversation on its first message: their hooks run and
their commands activate.

Source: `openhands-agent-server/openhands/agent_server/plugins_router.py`, `openhands-agent-server/openhands/agent_server/plugins_service.py`, `openhands-sdk/openhands/sdk/plugin/`, `openhands-sdk/openhands/sdk/extensions/`, `openhands-sdk/openhands/sdk/marketplace/`, `clients/typescript/src/client/plugins-client.ts`

Needs: `llm`, `git`, `node`, `network`

Routes: `POST /api/plugins`, `POST /api/plugins/install`,
`GET /api/plugins/installed`, `GET /api/plugins/installed/{plugin_name}`,
`PATCH /api/plugins/installed/{plugin_name}`,
`DELETE /api/plugins/installed/{plugin_name}`,
`POST /api/plugins/installed/{plugin_name}/refresh`,
`GET /api/plugins/marketplace`

## Sub-features

- `F26.auth`: every plugins route answers 401 without a valid session key and installs nothing.
- `F26.catalog-project`: `POST /api/plugins` with a `project_dir` lists the plugins under its `.agents/plugins` and `.openhands/plugins` (also from the enclosing git root) with their skills, command skills and files; `load_project: false` or no `project_dir` lists none, and a request without a JSON body is 422.
- `F26.catalog-user-precedence`: `load_user` lists the plugins under `~/.agents/plugins` and `$OH_PERSISTENCE_DIR/plugins`; a project plugin wins over a user plugin of the same name, and a user-directory plugin wins over an enabled installed plugin of the same name.
- `F26.install-local`: installing a local Claude Code layout plugin answers 200 with its metadata, skills and files, copies it into the store, records it in `.installed.json`, and the ambient catalog lists it (never the store itself).
- `F26.install-agent-plugins`: a root `plugin.json` with the Agent Plugins `$schema` installs with its `skills/` and `dev.openhands/commands`; the same manifest without `$schema` is 422.
- `F26.install-git`: installing from a `file://` git source with `repo_path` records the source, `repo_path` and the resolved commit, and copies only that subdirectory.
- `F26.install-repo-root-name`: a git source installed without `repo_path` and without a manifest is named after the repository, not after the server's cache directory.
- `F26.install-conflict`: a second install of the same plugin is 409 and changes nothing; `force: true` reinstalls and keeps the enabled flag.
- `F26.install-errors`: unfetchable sources are 400, invalid plugins 422, malformed bodies 422, and none of them leaves anything in the store.
- `F26.install-github`: the GitHub shorthand `github:owner/repo` with a `repo_path` installs that subdirectory of the public repository with its resolved commit.
- `F26.install-documented-source`: the GitHub source form that the OpenAPI description of `InstallPluginRequest.source` advertises installs the plugin.
- `F26.enable-disable`: `PATCH` with `enabled` toggles the flag, which the single view, the list and `.installed.json` show; a disabled plugin leaves the ambient catalog and comes back when re-enabled.
- `F26.name-errors`: an unknown plugin is 404 and a name outside `^[a-z0-9]+(-[a-z0-9]+)*$` is 422 on get, enable/disable, uninstall and refresh.
- `F26.refresh`: refresh re-fetches from the recorded source (a new commit, an edited local manifest), drops a pinned ref, and keeps the enabled flag.
- `F26.self-heal`: a directory copied into the store is unknown to the single view until a listing adopts it with source `local`; listing also ignores a directory whose manifest name differs and drops a record whose directory vanished.
- `F26.refresh-unfetchable`: refreshing a plugin whose source vanished, whose source now holds an invalid manifest, or one adopted with source `local`, is a client error and keeps the installation.
- `F26.uninstall`: uninstall removes the directory and the record; the single view, the list and the ambient catalog no longer show it, and a second uninstall is 404.
- `F26.conversation-ambient`: a new conversation loads an enabled installed plugin on its first message: its `UserPromptSubmit` hook runs (a `HookExecutionEvent`, also on the events socket), its command skill activates, and its skills show in the conversation.
- `F26.conversation-disabled`: a conversation started after the plugin was disabled loads neither its hook nor its skills, while the user-directory plugins still load.
- `F26.conversation-resume-disabled`: after a restart, an existing conversation drops a since-disabled plugin entirely, its command as well as its hook.
- `F26.restart-persist`: installed plugins, their records and their enabled flags survive a restart.
- `F26.marketplace-local`: against a local stand-in for the extensions repository, `GET /api/plugins/marketplace` lists only `./plugins/` entries (local contents for local entries, attach coordinates for git entries) with a fresh `installed` flag; a repository without a manifest gives an empty catalog that is not cached.
- `F26.marketplace-cache`: a non-empty catalog is served from a per-process cache (a new upstream entry appears only after a restart; the 5-minute TTL itself is not waited out), while `installed` stays fresh.
- `F26.ts-client`: every `PluginsClient` method of the TypeScript client works against the server, and errors surface as `HttpError` with the status and detail.
- `F26.marketplace-public`: against the real `OpenHands/extensions` repository, the catalog lists exactly its `./plugins/` entries, with their contents.

## How to get to it (agent POV)

- REST, ambient catalog: `POST /api/plugins` with `PluginsRequest`
  (`load_user` and `load_project` default to `true`, `project_dir` defaults
  to none; a JSON body is required, `{}` is enough) returns
  `{"plugins": [{name, version, description, path, skills, files}]}`, the set
  a conversation in `project_dir` would auto-load. Driven by the
  `F26.catalog-*` bullets and as the second view of every mutation.
- REST, installed plugins: `POST /api/plugins/install` (`source`, `ref`,
  `repo_path`, `force`), `GET /api/plugins/installed`,
  `GET|PATCH|DELETE /api/plugins/installed/{plugin_name}` (`PATCH` takes
  `{"enabled": bool}`) and `POST /api/plugins/installed/{plugin_name}/refresh`.
  Responses carry `skills` (including `<plugin>:<command>` skills) and `files`
  of the installed copy. The store is `$OH_PERSISTENCE_DIR/plugins/installed/<name>/`
  with metadata in `.installed.json`; git sources are cached under
  `$OH_PERSISTENCE_DIR/cache/extensions/`.
- REST, marketplace: `GET /api/plugins/marketplace` reads the public
  extensions clone at `$OH_PERSISTENCE_DIR/cache/skills/public-skills`
  (shared with the skills catalog, F25) and returns the `./plugins/` entries
  with attach coordinates (`source`, `ref`, `repo_path`), `installed`, and
  `path`/`skills`/`files` when the entry is a directory of the clone.
- Conversations: `LocalConversation._ensure_plugins_loaded` merges enabled
  installed plugins, user-directory plugins and the workspace's project
  plugins into the agent on the first message or run. Second views:
  `GET /api/conversations/{id}?include_skills=true` (`agent.agent_context.skills`;
  without the query the skills are trimmed), the user `MessageEvent`'s
  `activated_skills` and `HookExecutionEvent`s on
  `/sockets/events/{conversation_id}` (both owned by the conversation
  families). Explicit attach (`plugins` on `POST /api/conversations`) and
  `POST /api/conversations/{id}/load_plugin` belong to F04 and F09.
- TypeScript client: `PluginsClient.getPlugins`, `getPluginsMarketplace`,
  `installPlugin`, `listInstalledPlugins`, `getInstalledPlugin`,
  `setPluginEnabled`, `uninstallPlugin`, `refreshPlugin` (also as
  `OpenHandsClient.plugins`); driven by `F26.ts-client`.
- Python SDK: no remote client for these routes; `openhands.sdk.plugin`
  (`install_plugin`, `list_installed_plugins`, `load_available_plugins`, ...)
  is the same code run in-process.
- Configuration: `OH_PERSISTENCE_DIR` (store and caches) and `HOME`
  (`~/.agents/plugins`) are read at import, so set them before launch (the
  launcher makes both private to the run); `EXTENSIONS_REF` pins the public
  repository ref.
- Agent Canvas (context only): the plugins marketplace page installs and
  attaches plugins from the catalog.

## Driving it with control-agent-server

Preconditions:

- A run is live and exported, `doctor` is ok, `$DEEPSEEK_API_KEY` is set and
  `git` and `node` are installed. No launch flags are needed. Outbound HTTPS
  to github.com (`network`) is needed only by `F26.install-github`,
  `F26.install-documented-source` and `F26.marketplace-public`; every other
  bullet runs offline (apart from the DeepSeek calls). The `known bug`
  bullets arrange their own state and positive controls before the command
  marked `# bug`, so each also runs alone with `map run --only`, and an
  arrange failure (no network, no model) reports `fail`, not `xfail`.
- The block below activates `deepseek-flash` and creates the run-owned
  fixtures: the `qa-f26-inst` plugin (its hook echoes `QA_F26_INST_HOOK`),
  the `qa-f26-src` git source, the `qa-f26-proj` project with two project
  plugins, three user-directory plugins (one a copy of a project plugin with
  another description; `qa-f26-user`'s hook echoes `QA_F26_USER_HOOK`), an
  Agent Plugins layout plugin, broken plugin directories for the error cases,
  and a local git repository `extensions` without a marketplace manifest that
  is cloned into the run's public-skills cache, where it stands in for the
  public repository until `F26.marketplace-public` removes it.
  ```sh
  control-agent-server llm preset deepseek
  export GIT_CONFIG_GLOBAL="$AGENT_SERVER_VERIFY_RUN/home/.gitconfig"
  F="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f26"
  I="$AGENT_SERVER_VERIFY_RUN/home/.openhands/plugins/installed"
  U="$AGENT_SERVER_VERIFY_RUN/home/.agents/plugins"
  O="$AGENT_SERVER_VERIFY_RUN/home/.openhands/plugins"
  PUB="$AGENT_SERVER_VERIFY_RUN/home/.openhands/cache/skills/public-skills"
  E="$F/extensions"
  PL=$(control-agent-server fixture plugin --name qa-f26-inst --print-path)
  G=$(control-agent-server fixture git-source --name qa-f26-src --print-path)
  H1=$(git -C "$G" rev-parse HEAD)
  P=$(control-agent-server fixture project --name qa-f26-proj --print-path)
  mkdir -p "$U" "$O" "$P/.agents/plugins" "$P/.openhands/plugins" "$P/sub" "$E/plugins" "$E/skills/qa-f26-pub-standalone" \
    "$F/qa-f26-ap/skills/qa-f26-ap-skill" "$F/qa-f26-ap/dev.openhands/commands" "$F/qa-f26-ap-noschema" \
    "$F/qa-f26-badjson/.plugin" "$F/Qa_Plugin/skills/qa-f26-x" "$F/qa-f26-badname/.plugin" "$F/qa-f26-noname/.plugin"
  cp -r "$(control-agent-server fixture plugin --name qa-f26-proj-plugin --print-path)" "$P/.agents/plugins/"
  cp -r "$(control-agent-server fixture plugin --name qa-f26-legacy-plugin --print-path)" "$P/.openhands/plugins/"
  cp -r "$(control-agent-server fixture plugin --name qa-f26-user --print-path)" "$U/"
  cp -r "$(control-agent-server fixture plugin --name qa-f26-oh --print-path)" "$O/"
  cp -r "$P/.agents/plugins/qa-f26-proj-plugin" "$U/"
  sed -i 's/"description": ".*"/"description": "QA user copy"/' "$U/qa-f26-proj-plugin/.plugin/plugin.json"
  sed -i 's/QA_PLUGIN_HOOK/QA_F26_INST_HOOK/' "$PL/hooks/hooks.json"
  sed -i 's/QA_PLUGIN_HOOK/QA_F26_USER_HOOK/' "$U/qa-f26-user/hooks/hooks.json"
  printf '{"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": "qa-f26-ap", "version": "0.2.0", "description": "QA Agent Plugins layout"}\n' > "$F/qa-f26-ap/plugin.json"
  printf -- '---\nname: qa-f26-ap-skill\ndescription: QA Agent Plugins skill\n---\nQA_AP_SKILL\n' > "$F/qa-f26-ap/skills/qa-f26-ap-skill/SKILL.md"
  printf -- '---\ndescription: QA Agent Plugins command\n---\nReply with QA_AP_RUN.\n' > "$F/qa-f26-ap/dev.openhands/commands/run.md"
  printf '{"name": "qa-f26-ap-noschema", "version": "0.2.0"}\n' > "$F/qa-f26-ap-noschema/plugin.json"
  printf '{bad json' > "$F/qa-f26-badjson/.plugin/plugin.json"
  printf -- '---\nname: qa-f26-x\ndescription: QA skill of a badly named plugin\n---\nBody\n' > "$F/Qa_Plugin/skills/qa-f26-x/SKILL.md"
  printf '{"name": "Bad_Name", "version": "0.1.0"}\n' > "$F/qa-f26-badname/.plugin/plugin.json"
  printf '{"version": "0.1.0"}\n' > "$F/qa-f26-noname/.plugin/plugin.json"
  cp -r "$(control-agent-server fixture plugin --name qa-f26-pub --print-path)" "$E/plugins/"
  printf -- '---\nname: qa-f26-pub-standalone\ndescription: QA standalone skill\n---\nQA_PUB_STANDALONE\n' > "$E/skills/qa-f26-pub-standalone/SKILL.md"
  printf '# QA extensions stand-in\n' > "$E/README.md"
  git -C "$E" init -q -b main
  git -C "$E" add -A
  git -C "$E" commit -q -m 'QA extensions stand-in without a manifest'
  git clone -q "$E" "$PUB"
  ```

- **Session key required (`F26.auth`).** Call every route without a key (one
  with a wrong key), then once with the key.
  ```sh
  control-agent-server api POST /api/plugins --auth none --json '{}' --expect 401
  control-agent-server api POST /api/plugins/install --auth none --json '{"source": "'"$PL"'"}' --expect 401
  control-agent-server api GET /api/plugins/installed --auth bad --expect 401
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --auth none --expect 401
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --auth none --json '{"enabled": false}' --expect 401
  control-agent-server api DELETE /api/plugins/installed/qa-f26-inst --auth none --expect 401
  control-agent-server api POST /api/plugins/installed/qa-f26-inst/refresh --auth none --expect 401
  control-agent-server api GET /api/plugins/marketplace --auth none --expect 401 --check detail eq Unauthorized --save F26.auth/marketplace
  test ! -e "$I"
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 0
  ```
  All eight answer `401 {"detail": "Unauthorized"}`, the store directory does
  not exist, and the same list with the key answers 200 with no plugins.
- **Project plugins (`F26.catalog-project`).** List only the fixture
  project, from its root and from a subdirectory, then with the project tier
  off.
  ```sh
  control-agent-server api POST /api/plugins --json '{"load_user": false, "project_dir": "'"$P"'"}' --expect 200 \
    --check plugins len-eq 2 --check plugins.0.name eq qa-f26-proj-plugin --check plugins.0.version eq 0.1.0 \
    --check plugins.0.path eq "$P/.agents/plugins/qa-f26-proj-plugin" \
    --check plugins.0.skills contains '"name": "qa-f26-proj-plugin-skill"' \
    --check plugins.0.skills contains '"name": "qa-f26-proj-plugin:qa-hello", "description": "Say hello from the QA plugin"' \
    --check plugins.0.files contains .plugin/plugin.json --check plugins.0.files contains commands/qa-hello.md \
    --check plugins.0.files contains hooks/hooks.json --check plugins.1.name eq qa-f26-legacy-plugin \
    --check plugins.1.path eq "$P/.openhands/plugins/qa-f26-legacy-plugin" --save F26.catalog-project/project
  control-agent-server api POST /api/plugins --json '{"load_user": false, "project_dir": "'"$P/sub"'"}' --expect 200 \
    --check plugins len-eq 2 --check plugins.0.path eq "$P/.agents/plugins/qa-f26-proj-plugin" --save F26.catalog-project/git-root
  control-agent-server api POST /api/plugins --json '{"load_user": false, "load_project": false, "project_dir": "'"$P"'"}' --expect 200 --check plugins len-eq 0
  control-agent-server api POST /api/plugins --json '{"load_user": false}' --expect 200 --check plugins len-eq 0
  control-agent-server api POST /api/plugins --json '{"load_user": "maybe"}' --expect 422 --check detail.0.type eq bool_parsing
  control-agent-server api POST /api/plugins --expect 422 --check detail.0.type eq missing --check detail.0.loc.0 eq body \
    --save F26.catalog-project/no-body
  ```
  The project lists `qa-f26-proj-plugin` (from `.agents/plugins`) and
  `qa-f26-legacy-plugin` (from `.openhands/plugins`); each carries its skill,
  its command as the skill `<plugin>:qa-hello`, and its file list. From the
  subdirectory `sub` the same plugins come from the git root. Without the
  project tier, or without `project_dir`, nothing is listed. A request
  without a body is `422` (`missing` at `body`), although every field has a
  default.
- **User plugins and precedence (`F26.catalog-user-precedence`).** List the
  user tier, then both tiers; then install a copy of the user-directory
  plugin `qa-f26-oh` with another description, list again, and uninstall it.
  ```sh
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins len-eq 3 --check plugins.0.name eq qa-f26-proj-plugin --check plugins.0.description eq 'QA user copy' \
    --check plugins.0.path eq "$U/qa-f26-proj-plugin" --check plugins.1.name eq qa-f26-user \
    --check plugins.1.path eq "$U/qa-f26-user" --check plugins.2.name eq qa-f26-oh \
    --check plugins.2.path eq "$O/qa-f26-oh" --save F26.catalog-user-precedence/user
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": true, "project_dir": "'"$P"'"}' --expect 200 \
    --check plugins len-eq 4 --check plugins.0.name eq qa-f26-proj-plugin \
    --check plugins.0.description eq 'QA fixture plugin qa-f26-proj-plugin' \
    --check plugins.0.path eq "$P/.agents/plugins/qa-f26-proj-plugin" --check plugins.3.name eq qa-f26-legacy-plugin \
    --save F26.catalog-user-precedence/merged
  cp -r "$O/qa-f26-oh" "$F/qa-f26-oh-copy"
  sed -i 's/"description": ".*"/"description": "QA installed copy"/' "$F/qa-f26-oh-copy/.plugin/plugin.json"
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/qa-f26-oh-copy"'"}' --expect 200 \
    --check name eq qa-f26-oh --check description eq 'QA installed copy' --check enabled eq true
  control-agent-server api GET /api/plugins/installed/qa-f26-oh --expect 200 --check description eq 'QA installed copy'
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins len-eq 3 --check plugins.2.name eq qa-f26-oh --check plugins.2.path eq "$O/qa-f26-oh" \
    --check plugins.2.description eq 'QA fixture plugin qa-f26-oh' --save F26.catalog-user-precedence/installed-shadowed
  control-agent-server api DELETE /api/plugins/installed/qa-f26-oh --expect 200
  test -d "$O/qa-f26-oh"
  ```
  The user tier lists `~/.agents/plugins` (the user copy of
  `qa-f26-proj-plugin`, `qa-f26-user`) and `$OH_PERSISTENCE_DIR/plugins`
  (`qa-f26-oh`). With both tiers the project's `qa-f26-proj-plugin`
  replaces the user copy (project description and path), and the result has
  four plugins. An enabled installed plugin named like a user-directory
  plugin is installed (its single view shows it) but the ambient catalog
  keeps the directory plugin (path and description), with no duplicate.
  Uninstalling the copy leaves the directory plugin alone.
- **Install from a local path (`F26.install-local`).** Install the fixture
  plugin, then read it back four ways.
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$PL"'"}' --expect 200 \
    --check name eq qa-f26-inst --check version eq 0.1.0 --check description eq 'QA fixture plugin qa-f26-inst' \
    --check enabled eq true --check source eq "$PL" --check resolved_ref eq null --check repo_path eq null \
    --check install_path eq "$I/qa-f26-inst" --check installed_at exists --check skills len-eq 2 \
    --check skills.0.name eq qa-f26-inst-skill --check skills.1.name eq qa-f26-inst:qa-hello \
    --check files len-eq 4 --check files contains hooks/hooks.json --save F26.install-local/install
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check source eq "$PL" \
    --check enabled eq true --check skills len-eq 2 --save F26.install-local/get
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 1 --check plugins.0.name eq qa-f26-inst
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json \
    --check extensions.qa-f26-inst.source eq "$PL" --check extensions.qa-f26-inst.enabled eq true
  test -f "$I/qa-f26-inst/.plugin/plugin.json"
  grep -q QA_F26_INST_HOOK "$I/qa-f26-inst/hooks/hooks.json"
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins len-eq 4 --check plugins.3.name eq qa-f26-inst --check plugins.3.path eq "$I/qa-f26-inst" \
    --check plugins not-contains '"name": "installed"' --save F26.install-local/ambient
  ```
  The install answers the manifest's name, version and description, the
  source as given, no `resolved_ref` (local copy), the store path, and the
  installed copy's skills and four files. The single view, the list,
  `.installed.json` and the copied files agree. The ambient catalog appends
  the installed plugin after the directory plugins and does not mistake the
  store directory `$OH_PERSISTENCE_DIR/plugins/installed` for a plugin.
- **Agent Plugins layout (`F26.install-agent-plugins`).** Install a root
  `plugin.json` with the Agent Plugins `$schema`, then the same manifest
  without it.
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/qa-f26-ap"'"}' --expect 200 \
    --check name eq qa-f26-ap --check version eq 0.2.0 --check description eq 'QA Agent Plugins layout' \
    --check skills len-eq 2 --check skills.0.name eq qa-f26-ap-skill --check skills.1.name eq qa-f26-ap:run \
    --check files contains plugin.json --check files contains dev.openhands/commands/run.md \
    --save F26.install-agent-plugins/install
  control-agent-server api GET /api/plugins/installed/qa-f26-ap --expect 200 --check skills.1.name eq qa-f26-ap:run
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/qa-f26-ap-noschema"'"}' --expect 422 \
    --check detail eq 'Invalid plugin. Ensure it has a valid kebab-case name.' --save F26.install-agent-plugins/no-schema
  test ! -e "$I/qa-f26-ap-noschema"
  ```
  The Agent Plugins format is detected from the root `plugin.json`: the skill
  comes from `skills/` and the command from `dev.openhands/commands/`
  (`qa-f26-ap:run`). Without `$schema` the manifest is claimed by the same
  format and rejected (422), not loaded as a manifest-less plugin.
- **Install from a git source (`F26.install-git`).** Install the plugin
  subdirectory of the `file://` fixture repository.
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "file://'"$G"'", "repo_path": "plugins/qa-f26-src-plugin"}' --expect 200 \
    --check name eq qa-f26-src-plugin --check source eq "file://$G" --check repo_path eq plugins/qa-f26-src-plugin \
    --check resolved_ref eq "$H1" --check install_path eq "$I/qa-f26-src-plugin" --check files len-eq 4 \
    --save F26.install-git/install
  control-agent-server api GET /api/plugins/installed/qa-f26-src-plugin --expect 200 --check resolved_ref eq "$H1" \
    --check repo_path eq plugins/qa-f26-src-plugin
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json \
    --check extensions.qa-f26-src-plugin.resolved_ref eq "$H1" --check extensions.qa-f26-src-plugin.requested_ref eq null
  control-agent-server state ls 'home/.openhands/cache/extensions/qa-f26-src-*/.git/HEAD' --expect-count 1
  test ! -e "$I/qa-f26-src-plugin/.git"
  test ! -e "$I/qa-f26-src-plugin/skills/qa-f26-src-skill"
  ```
  The repository is cloned into `cache/extensions/qa-f26-src-<hash>`, the
  plugin subdirectory alone is copied into the store, and the record keeps
  the source, `repo_path` and the commit (`H1`); no ref was requested.
- **Repository root name (`F26.install-repo-root-name`), known bug.**
  Install the whole fixture repository (it has no plugin manifest at its
  root), remove it, then compare the name.
  ```sh
  N=$(control-agent-server api POST /api/plugins/install --json '{"source": "file://'"$G"'"}' --expect 200 \
    --check resolved_ref eq "$H1" --save F26.install-repo-root-name/install --field name)
  control-agent-server api DELETE "/api/plugins/installed/$N" --expect 200
  control-agent-server api GET "/api/plugins/installed/$N" --expect 404
  test "$N" = qa-f26-src  # bug
  ```
  A manifest-less directory takes its directory's name, so the repository
  should install as `qa-f26-src`. Today it installs as
  `qa-f26-src-<16 hex>`: the name is inferred from the server's cache
  directory (`get_cache_path` appends a hash of the URL), so the same
  repository gets another name under another URL spelling. The install is
  removed before the comparison, so later bullets do not see it.
- **Duplicate install (`F26.install-conflict`).** Install the same plugin
  again, then force it while it is disabled.
  ```sh
  T1=$(control-agent-server api GET /api/plugins/installed/qa-f26-inst --field installed_at)
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$PL"'"}' --expect 409 \
    --check detail eq 'Plugin already installed. Use force=true to overwrite.' --save F26.install-conflict/conflict
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check installed_at eq "$T1"
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": false}' --expect 200
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$PL"'", "force": true}' --expect 200 \
    --check enabled eq false --check installed_at ne "$T1" --save F26.install-conflict/force
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check enabled eq false --check installed_at ne "$T1"
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": true}' --expect 200
  ```
  The second install is `409` and leaves `installed_at` alone. `force: true`
  reinstalls (new `installed_at`) and keeps the plugin disabled; the bullet
  re-enables it.
- **Install errors (`F26.install-errors`).** Unfetchable sources, invalid
  plugins and malformed bodies.
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/qa-f26-missing"'"}' --expect 400 \
    --check detail eq 'Failed to fetch plugin source. Check that the source is valid.' --save F26.install-errors/missing-path
  control-agent-server api POST /api/plugins/install --json '{"source": "github:qa/f26/extra"}' --expect 400
  control-agent-server api POST /api/plugins/install --json '{"source": "qa-f26-not-a-source"}' --expect 400
  control-agent-server api POST /api/plugins/install --json '{"source": "file://'"$F/qa-f26-no-repo"'"}' --expect 400
  control-agent-server api POST /api/plugins/install --json '{"source": "file://'"$G"'", "repo_path": "plugins/qa-f26-missing"}' --expect 400
  control-agent-server api POST /api/plugins/install --json '{"source": "file://'"$G"'", "repo_path": "../.."}' --expect 400 \
    --save F26.install-errors/escaping-repo-path
  for D in qa-f26-badjson Qa_Plugin qa-f26-badname qa-f26-noname; do
    control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/$D"'"}' --expect 422 \
      --check detail eq 'Invalid plugin. Ensure it has a valid kebab-case name.' --save "F26.install-errors/$D"
  done
  control-agent-server api POST /api/plugins/install --json '{"source": ""}' --expect 422 --check detail.0.type eq string_too_short
  control-agent-server api POST /api/plugins/install --json '{}' --expect 422 --check detail.0.type eq missing
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$PL"'", "ref": 5}' --expect 422 --check detail.0.loc.1 eq ref
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 3 --save F26.install-errors/after
  test "$(find "$I" -mindepth 1 -maxdepth 1 | wc -l)" -eq 4
  ```
  A missing path, a GitHub shorthand with a subpath (rejected by the parser
  without any network call; the documented example has the same shape, see
  `F26.install-documented-source`), an unparsable source, a `file://` URL
  that is not a repository, a missing `repo_path` and a `repo_path` that
  climbs out of the repository are all `400 Failed to fetch plugin source`. Invalid JSON in `.plugin/plugin.json`, a manifest-less
  directory named `Qa_Plugin`, a manifest name `Bad_Name` and a manifest
  without a name are all `422` with the same kebab-case message. Body errors
  are FastAPI `422`s. The store still holds exactly the three plugins and
  `.installed.json`.
- **Install from GitHub (`F26.install-github`).** Install the public
  `plugins/city-weather` plugin with the GitHub shorthand and `repo_path`
  (network), read it back, and uninstall it.
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "github:OpenHands/extensions", "repo_path": "plugins/city-weather"}' \
    --timeout 240 --expect 200 --check name eq city-weather --check source eq github:OpenHands/extensions \
    --check repo_path eq plugins/city-weather --check resolved_ref matches '^[0-9a-f]{40}$' \
    --check skills contains '"name": "city-weather:now"' --save F26.install-github/install
  control-agent-server api GET /api/plugins/installed/city-weather --expect 200 --check repo_path eq plugins/city-weather \
    --check resolved_ref matches '^[0-9a-f]{40}$' --save F26.install-github/get
  control-agent-server state ls 'home/.openhands/cache/extensions/extensions-*/.git/HEAD' --expect-count 1
  test ! -e "$I/city-weather/.git"
  control-agent-server api DELETE /api/plugins/installed/city-weather --expect 200
  control-agent-server api GET /api/plugins/installed/city-weather --expect 404
  ```
  The shorthand expands to `https://github.com/OpenHands/extensions.git`,
  which is cloned into `cache/extensions/extensions-<hash>`; only
  `plugins/city-weather` is copied into the store, with the commit it was
  installed at. Without network the install is `400 Failed to fetch plugin
  source`. The plugin is removed so later bullets see the same store.
- **Documented install source (`F26.install-documented-source`), known bug.**
  Install the public plugin in the working form first (the positive control:
  this server reaches GitHub now), then in the GitHub form the OpenAPI
  description of `InstallPluginRequest.source` advertises (network).
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "github:OpenHands/extensions", "repo_path": "plugins/city-weather", "force": true}' \
    --timeout 240 --expect 200 --check name eq city-weather --save F26.install-documented-source/working-form
  control-agent-server api DELETE /api/plugins/installed/city-weather --expect 200
  DOC=$(control-agent-server api GET /openapi.json --auth none --field components.schemas.InstallPluginRequest.properties.source.description)
  case "$DOC" in
    *"'github:OpenHands/extensions/plugins/city-weather'"*)
      control-agent-server api POST /api/plugins/install --json '{"source": "github:OpenHands/extensions/plugins/city-weather"}' \
        --timeout 240 --expect 200 --check name eq city-weather --save F26.install-documented-source/documented  # bug
      control-agent-server api DELETE /api/plugins/installed/city-weather --expect 200
      ;;
  esac
  ```
  The working form (`github:OpenHands/extensions` plus `repo_path`, as in
  `F26.install-github`) installs and is removed again, so GitHub is
  reachable from this server at this moment; the documented example should
  install too (or stop being documented, which also passes this bullet).
  Today it is `400 Failed to fetch plugin source` with no network call:
  `parse_extension_source` accepts only `github:owner/repo` (one slash). The
  same text ships in the TypeScript client's generated schema. Without
  network the positive control fails first, so the bullet reports `fail`,
  not `xfail`.
- **Enable and disable (`F26.enable-disable`).** Disable the plugin, read it
  back everywhere, then enable it again.
  ```sh
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": false}' --expect 200 \
    --check name eq qa-f26-inst --check enabled eq false --save F26.enable-disable/disable
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check enabled eq false
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins.0.name eq qa-f26-inst \
    --check plugins.0.enabled eq false --check plugins.1.enabled eq true
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json --check extensions.qa-f26-inst.enabled eq false
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins not-contains '"name": "qa-f26-inst"' --check plugins contains '"name": "qa-f26-ap"' \
    --save F26.enable-disable/ambient-disabled
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": false}' --expect 200 --check enabled eq false
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{}' --expect 422 --check detail.0.loc.1 eq enabled
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": true}' --expect 200 --check enabled eq true
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check enabled eq true
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins contains '"name": "qa-f26-inst"' --save F26.enable-disable/ambient-enabled
  ```
  `PATCH` answers `{"name": "qa-f26-inst", "enabled": false}`; the single
  view, the list (the other plugins stay enabled) and `.installed.json` show
  the flag, and the ambient catalog drops the plugin while still listing the
  enabled `qa-f26-ap`. Disabling twice is a 200 no-op, a body without
  `enabled` is 422, and enabling brings it back.
- **Unknown and invalid names (`F26.name-errors`).** Address a plugin that is
  not installed, then names that break the pattern.
  ```sh
  control-agent-server api GET /api/plugins/installed/qa-f26-ghost --expect 404 \
    --check detail eq "Plugin 'qa-f26-ghost' is not installed" --save F26.name-errors/get
  control-agent-server api PATCH /api/plugins/installed/qa-f26-ghost --json '{"enabled": true}' --expect 404 \
    --check detail eq "Plugin 'qa-f26-ghost' is not installed"
  control-agent-server api DELETE /api/plugins/installed/qa-f26-ghost --expect 404 --check detail eq "Plugin 'qa-f26-ghost' is not installed"
  control-agent-server api POST /api/plugins/installed/qa-f26-ghost/refresh --expect 404 \
    --check detail eq "Plugin 'qa-f26-ghost' is not installed"
  control-agent-server api GET /api/plugins/installed/Bad_Name --expect 422 --check detail.0.type eq string_pattern_mismatch \
    --save F26.name-errors/bad-name
  control-agent-server api PATCH /api/plugins/installed/Bad_Name --json '{"enabled": true}' --expect 422 \
    --check detail.0.type eq string_pattern_mismatch
  control-agent-server api DELETE /api/plugins/installed/Bad_Name --expect 422 --check detail.0.type eq string_pattern_mismatch
  control-agent-server api POST /api/plugins/installed/Bad_Name/refresh --expect 422 --check detail.0.type eq string_pattern_mismatch
  control-agent-server api DELETE /api/plugins/installed/qa--f26 --expect 422
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 3
  ```
  Every unknown name is `404 Plugin '<name>' is not installed`; `Bad_Name`
  and `qa--f26` fail the path pattern with `422` before the handler runs,
  and nothing changed.
- **Refresh (`F26.refresh`).** Commit a new manifest description to the git
  source and refresh the disabled git plugin; edit the local source and
  refresh the local plugin; then pin the git plugin to `H1` and refresh it.
  ```sh
  sed -i 's/"description": ".*"/"description": "QA git plugin v2"/' "$G/plugins/qa-f26-src-plugin/.plugin/plugin.json"
  git -C "$G" commit -q -am 'QA plugin v2'
  H2=$(git -C "$G" rev-parse HEAD)
  control-agent-server api PATCH /api/plugins/installed/qa-f26-src-plugin --json '{"enabled": false}' --expect 200
  control-agent-server api POST /api/plugins/installed/qa-f26-src-plugin/refresh --expect 200 \
    --check message eq "Plugin 'qa-f26-src-plugin' updated" --check plugin.resolved_ref eq "$H2" \
    --check plugin.description eq 'QA git plugin v2' --check plugin.enabled eq false \
    --check plugin.repo_path eq plugins/qa-f26-src-plugin --save F26.refresh/git
  control-agent-server api GET /api/plugins/installed/qa-f26-src-plugin --expect 200 --check resolved_ref eq "$H2" --check enabled eq false
  grep -q 'QA git plugin v2' "$I/qa-f26-src-plugin/.plugin/plugin.json"
  sed -i 's/"description": ".*"/"description": "QA local plugin v2"/' "$PL/.plugin/plugin.json"
  control-agent-server api POST /api/plugins/installed/qa-f26-inst/refresh --expect 200 \
    --check plugin.description eq 'QA local plugin v2' --check plugin.enabled eq true --check plugin.source eq "$PL" \
    --save F26.refresh/local
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check description eq 'QA local plugin v2'
  control-agent-server api POST /api/plugins/install \
    --json '{"source": "file://'"$G"'", "repo_path": "plugins/qa-f26-src-plugin", "ref": "'"$H1"'", "force": true}' --expect 200 \
    --check resolved_ref eq "$H1" --check description eq 'QA fixture plugin qa-f26-src-plugin' --check enabled eq false \
    --save F26.refresh/pinned
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json --check extensions.qa-f26-src-plugin.requested_ref eq "$H1"
  control-agent-server api POST /api/plugins/installed/qa-f26-src-plugin/refresh --expect 200 \
    --check plugin.resolved_ref eq "$H2" --check plugin.description eq 'QA git plugin v2' --save F26.refresh/unpinned
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json --check extensions.qa-f26-src-plugin.requested_ref eq null
  control-agent-server api PATCH /api/plugins/installed/qa-f26-src-plugin --json '{"enabled": true}' --expect 200
  ```
  Refresh re-fetches the recorded source with its `repo_path`: the git plugin
  moves to `H2` with the new description and stays disabled; the local
  plugin picks up the edited manifest. A force install at `ref` `H1` checks
  out the old commit and records `requested_ref`; refresh reinstalls with
  the ref dropped, by design ("latest version"), so the plugin is back at
  `H2` and `requested_ref` is gone. The git plugin is enabled again at the
  end.
- **Hand-edited store (`F26.self-heal`).** Copy a plugin directory into the
  store, add one whose manifest name differs from its directory, and delete
  the directory of a tracked plugin; then read single views before and after
  a listing.
  ```sh
  cp -r "$(control-agent-server fixture plugin --name qa-f26-healed --print-path)" "$I/"
  mkdir -p "$I/qa-f26-mismatch/.plugin" "$F/qa-f26-stale/.plugin"
  printf '{"name": "qa-f26-other", "version": "0.1.0"}\n' > "$I/qa-f26-mismatch/.plugin/plugin.json"
  printf '{"name": "qa-f26-stale", "version": "0.1.0"}\n' > "$F/qa-f26-stale/.plugin/plugin.json"
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/qa-f26-stale"'"}' --expect 200
  rm -rf "$I/qa-f26-stale"
  control-agent-server api GET /api/plugins/installed/qa-f26-healed --expect 404 --save F26.self-heal/before-list
  control-agent-server api DELETE /api/plugins/installed/qa-f26-healed --expect 404
  control-agent-server api GET /api/plugins/installed/qa-f26-stale --expect 404
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 4 \
    --check plugins.3.name eq qa-f26-healed --check plugins.3.source eq local --check plugins.3.enabled eq true \
    --check plugins not-contains qa-f26-stale --check plugins not-contains qa-f26-other --save F26.self-heal/list
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json --check extensions.qa-f26-healed.source eq local \
    --check extensions.qa-f26-stale missing --check extensions.qa-f26-mismatch missing --check extensions.qa-f26-other missing
  control-agent-server api GET /api/plugins/installed/qa-f26-healed --expect 200 --check source eq local --save F26.self-heal/after-list
  test -d "$I/qa-f26-mismatch"
  rm -rf "$I/qa-f26-mismatch"
  ```
  Only tracked plugins count for the single view and uninstall, so the copied
  `qa-f26-healed` is `404` there (uninstall refuses untracked directories by
  design) until the list adopts it with source `local`. The list also drops
  the record of `qa-f26-stale` (its directory is gone) and skips
  `qa-f26-mismatch` (manifest name `qa-f26-other`), leaving it on disk.
  `POST /api/plugins` and new conversations reconcile the same way, so a
  copied plugin is loaded although its single view said 404.
- **Unfetchable refresh (`F26.refresh-unfetchable`), known bug.**
  Install two plugins from temporary directories and adopt a third copied
  into the store; then delete the first source, break the second one's
  manifest, and refresh all three. The bullet arranges everything itself,
  so it also runs alone (`map run --only`).
  ```sh
  mkdir -p "$F/tmp/qa-f26-gone/.plugin" "$F/tmp/qa-f26-broken/.plugin"
  printf '{"name": "qa-f26-gone", "version": "0.1.0"}\n' > "$F/tmp/qa-f26-gone/.plugin/plugin.json"
  printf '{"name": "qa-f26-broken", "version": "0.1.0"}\n' > "$F/tmp/qa-f26-broken/.plugin/plugin.json"
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/tmp/qa-f26-gone"'"}' --expect 200
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$F/tmp/qa-f26-broken"'"}' --expect 200
  cp -r "$(control-agent-server fixture plugin --name qa-f26-adopted --print-path)" "$I/"
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins contains '"name": "qa-f26-adopted"'
  control-agent-server api GET /api/plugins/installed/qa-f26-adopted --expect 200 --check source eq local
  rm -rf "$F/tmp/qa-f26-gone"
  printf '{bad json' > "$F/tmp/qa-f26-broken/.plugin/plugin.json"
  control-agent-server api POST /api/plugins/installed/qa-f26-gone/refresh --expect 400,422 --save F26.refresh-unfetchable/vanished  # bug
  control-agent-server api GET /api/plugins/installed/qa-f26-gone --expect 200 --check enabled eq true
  control-agent-server api POST /api/plugins/installed/qa-f26-broken/refresh --expect 422 --save F26.refresh-unfetchable/invalid  # bug
  control-agent-server api GET /api/plugins/installed/qa-f26-broken --expect 200 --check version eq 0.1.0
  control-agent-server api POST /api/plugins/installed/qa-f26-adopted/refresh --expect 400,422 --save F26.refresh-unfetchable/local  # bug
  control-agent-server api GET /api/plugins/installed/qa-f26-adopted --expect 200 --check source eq local
  ```
  The install route answers an unfetchable source with 400 and an invalid
  plugin with 422; the refresh route should do the same and keep the
  installed copy. Today all three refreshes are `500` with the exception
  text: `Local extension path does not exist: ...`, `Invalid JSON in ...`
  and `Unable to parse extension source: local`.
  `refresh_plugin_endpoint` does not catch `ExtensionFetchError` or
  `ValueError` as the install route does. The installed copies survive.
  Each refresh alone encodes the bug, so each is marked `# bug`; a replay
  stops at the first one still failing (today the vanished source), and the
  reads after a fixed refresh check that the installation was kept.
- **Uninstall (`F26.uninstall`).** Remove the Agent Plugins layout plugin,
  read it back everywhere, remove it again; then clear the leftovers of the
  earlier bullets.
  ```sh
  control-agent-server api DELETE /api/plugins/installed/qa-f26-ap --expect 200 \
    --check message eq "Plugin 'qa-f26-ap' uninstalled" --save F26.uninstall/delete
  control-agent-server api GET /api/plugins/installed/qa-f26-ap --expect 404
  test ! -e "$I/qa-f26-ap"
  test -f "$F/qa-f26-ap/plugin.json"
  control-agent-server state cat home/.openhands/plugins/installed/.installed.json --check extensions.qa-f26-ap missing
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins not-contains '"name": "qa-f26-ap"'
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins not-contains '"name": "qa-f26-ap"' --save F26.uninstall/ambient
  control-agent-server api DELETE /api/plugins/installed/qa-f26-ap --expect 404 --check detail eq "Plugin 'qa-f26-ap' is not installed"
  for N in qa-f26-healed qa-f26-gone qa-f26-broken qa-f26-adopted; do
    control-agent-server api DELETE "/api/plugins/installed/$N" --expect 200,404 --quiet
  done
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 2 \
    --check plugins.0.name eq qa-f26-inst --check plugins.1.name eq qa-f26-src-plugin
  ```
  The directory and the record are gone (the source directory is untouched),
  no view lists the plugin, and a second uninstall is `404`. Afterwards the
  store holds `qa-f26-inst` and `qa-f26-src-plugin`, both enabled.
- **Ambient load into a conversation (`F26.conversation-ambient`).** Create a
  conversation without a message, capture its events socket, then send the
  plugin's command; also start a second conversation with a plain prompt.
  ```sh
  CID=$(control-agent-server conversation start --tools none --no-autotitle --no-run --print-id)
  control-agent-server ws start "/sockets/events/$CID" --name qa-f26-live --duration 300
  control-agent-server conversation send "$CID" --text '/qa-f26-inst:qa-hello' --wait --timeout 240
  control-agent-server ws stop qa-f26-live --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-min 1 --wait 30 \
    --save F26.conversation-ambient/frames
  control-agent-server conversation events "$CID" --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-count 1 \
    --save F26.conversation-ambient/hook
  control-agent-server conversation events "$CID" --kinds HookExecutionEvent --contains QA_F26_USER_HOOK --expect-count 1
  control-agent-server conversation events "$CID" --kinds MessageEvent --contains '"activated_skills": ["qa-f26-inst:qa-hello"]' \
    --expect-count 1 --save F26.conversation-ambient/activated
  control-agent-server api GET "/api/conversations/$CID" --query include_skills=true --expect 200 --check execution_status eq finished \
    --check agent.agent_context.skills contains '"name": "qa-f26-inst-skill"' \
    --check agent.agent_context.skills contains '"name": "qa-f26-inst:qa-hello"' \
    --check agent.agent_context.skills contains '"name": "qa-f26-user-skill"' --save F26.conversation-ambient/conversation
  CIDP=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with the single word: ok' --wait --timeout 240 --print-id)
  control-agent-server conversation events "$CIDP" --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-count 1
  control-agent-server api GET "/api/conversations/$CIDP" --query include_skills=true --expect 200 \
    --check agent.agent_context.skills contains '"name": "qa-f26-inst:qa-hello"'
  ```
  The installed plugin's `UserPromptSubmit` hook runs once for the message
  (a `HookExecutionEvent` with `stdout` `QA_F26_INST_HOOK`, pushed on the
  socket and stored), as does the user-directory plugin's. The user
  `MessageEvent` lists `activated_skills: ["qa-f26-inst:qa-hello"]`, and the
  conversation's agent context now carries the plugin's skill and command.
  A conversation started with a prompt loads the plugin the same way. The
  model's replies are not asserted.
- **Disabled plugin in a new conversation (`F26.conversation-disabled`).**
  Disable the plugin, then send its command to a new conversation while
  capturing the socket.
  ```sh
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": false}' --expect 200
  CID2=$(control-agent-server conversation start --tools none --no-autotitle --no-run --print-id)
  control-agent-server ws start "/sockets/events/$CID2" --name qa-f26-disabled --duration 300
  control-agent-server conversation send "$CID2" --text '/qa-f26-inst:qa-hello' --wait --timeout 240
  control-agent-server ws read qa-f26-disabled --kinds HookExecutionEvent --contains QA_F26_USER_HOOK --expect-min 1 --wait 30
  control-agent-server ws read qa-f26-disabled --kinds MessageEvent --contains '"text": "/qa-f26-inst:qa-hello"' --expect-min 1 --wait 30
  control-agent-server ws read qa-f26-disabled --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-none
  control-agent-server ws stop qa-f26-disabled --kinds MessageEvent --contains '"activated_skills": ["qa-f26-inst:qa-hello"]' --expect-none \
    --save F26.conversation-disabled/frames
  control-agent-server api GET "/api/conversations/$CID2" --query include_skills=true --expect 200 \
    --check agent.agent_context.skills not-contains qa-f26-inst --check agent.agent_context.skills contains '"name": "qa-f26-user-skill"' \
    --save F26.conversation-disabled/conversation
  control-agent-server conversation events "$CID2" --kinds HookExecutionEvent --contains QA_F26_USER_HOOK --expect-count 1
  control-agent-server conversation events "$CID2" --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-count 0
  control-agent-server conversation events "$CID2" --kinds MessageEvent --contains '"activated_skills": ["qa-f26-inst:qa-hello"]' \
    --expect-count 0 --save F26.conversation-disabled/stored
  ```
  The user-directory plugin's hook runs and its skill is present (hooks and
  ambient loading work, and the user message arrived), while the disabled
  plugin contributes neither a hook, an activated command, nor a skill. The
  negative checks read both the socket capture (`--expect-none`) and the
  stored events (`--expect-count 0`), each restricted to the event kind (the
  first `full_state` frame embeds hook configuration); the same reads with
  the plugin enabled match in `F26.conversation-ambient`. The plugin stays
  disabled.
- **Resume after disabling (`F26.conversation-resume-disabled`), known bug.**
  Install and enable the plugin (`force`, so the bullet also runs alone with
  `map run --only`), let a conversation load it with a plain prompt, disable
  the plugin, restart the server, and send the command, for the first time,
  to that conversation.
  ```sh
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$PL"'", "force": true}' --expect 200
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": true}' --expect 200
  CIDR=$(control-agent-server conversation start --tools none --no-autotitle --prompt 'Reply with the single word: ok' --wait --timeout 240 --print-id)
  control-agent-server conversation events "$CIDR" --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-count 1
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": false}' --expect 200
  control-agent-server restart
  control-agent-server ws start "/sockets/events/$CIDR" --name qa-f26-resume --duration 300
  control-agent-server conversation send "$CIDR" --text '/qa-f26-inst:qa-hello' --wait --timeout 240
  control-agent-server ws read qa-f26-resume --kinds HookExecutionEvent --contains QA_F26_USER_HOOK --expect-min 1 --wait 30
  control-agent-server ws read qa-f26-resume --kinds MessageEvent --contains '"text": "/qa-f26-inst:qa-hello"' --expect-min 1 --wait 30
  control-agent-server ws read qa-f26-resume --kinds HookExecutionEvent --contains QA_F26_INST_HOOK --expect-none
  control-agent-server ws stop qa-f26-resume --kinds MessageEvent --contains '"activated_skills": ["qa-f26-inst:qa-hello"]' --expect-none \
    --save F26.conversation-resume-disabled/frames  # bug
  control-agent-server api GET "/api/conversations/$CIDR" --query include_skills=true --expect 200 \
    --check agent.agent_context.skills not-contains qa-f26-inst --save F26.conversation-resume-disabled/conversation  # bug
  ```
  The conversation loaded the plugin before it was disabled (its hook ran
  for the prompt). Ambient plugins are not pinned to a conversation; on
  resume they are "re-discovered from disk / current enabled state"
  (`LocalConversation._ensure_plugins_loaded`), so the resumed turn should
  behave like `F26.conversation-disabled`. It does run the user hook and not
  the disabled plugin's hook, but the disabled plugin's command still
  activates (`activated_skills: ["qa-f26-inst:qa-hello"]` on the user
  message, the first marked check), and the agent context still lists its
  skills (the second): they were merged into the agent and persisted in
  `base_state.json`, so only its hooks follow the current state (by the same
  path, read from source and not driven here, its MCP servers stay too). The command is sent here for the
  first time because a keyword skill activates once per conversation
  (`activated_knowledge_skills`). The plugin stays disabled.
- **Persistence across a restart (`F26.restart-persist`).** Disable
  `qa-f26-inst` (a no-op after the previous bullets), restart, read the store
  back, then re-enable the plugin.
  ```sh
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": false}' --expect 200
  control-agent-server restart
  control-agent-server doctor
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 2 \
    --check plugins.0.name eq qa-f26-inst --check plugins.0.enabled eq false --check plugins.0.description eq 'QA local plugin v2' \
    --check plugins.1.name eq qa-f26-src-plugin --check plugins.1.resolved_ref eq "$H2" --check plugins.1.enabled eq true \
    --save F26.restart-persist/list
  control-agent-server api POST /api/plugins --json '{"load_user": true, "load_project": false}' --expect 200 \
    --check plugins not-contains '"name": "qa-f26-inst"' --check plugins contains '"name": "qa-f26-src-plugin"'
  control-agent-server api PATCH /api/plugins/installed/qa-f26-inst --json '{"enabled": true}' --expect 200
  control-agent-server api GET /api/plugins/installed/qa-f26-inst --expect 200 --check enabled eq true --save F26.restart-persist/re-enabled
  ```
  Both plugins come back with their refreshed metadata, the disabled flag
  survives (the ambient catalog still skips `qa-f26-inst`), and the plugin
  is enabled again at the end.
- **Marketplace against a local stand-in (`F26.marketplace-local`).** Read
  the catalog while the stand-in has no manifest, then commit a manifest with
  a local plugin, a standalone skill and a plugin in another repository, read
  it again, and install the local entry from its coordinates.
  ```sh
  control-agent-server api GET /api/plugins/marketplace --expect 200 --check plugins len-eq 0 --save F26.marketplace-local/no-manifest
  test -d "$PUB/.git"
  mkdir -p "$E/.plugin"
  cat > "$E/.plugin/marketplace.json" <<EOF
  {"name": "qa-f26-ext", "owner": {"name": "QA"}, "plugins": [
    {"name": "qa-f26-pub", "source": "./plugins/qa-f26-pub", "description": "QA public plugin"},
    {"name": "qa-f26-pub-standalone", "source": "./skills/qa-f26-pub-standalone", "description": "QA standalone skill"},
    {"name": "qa-f26-src-plugin", "source": {"source": "url", "url": "file://$G", "path": "plugins/qa-f26-src-plugin"}, "description": "QA plugin in another repository"}
  ]}
  EOF
  git -C "$E" add -A
  git -C "$E" commit -q -m 'QA marketplace manifest'
  control-agent-server api GET /api/plugins/marketplace --expect 200 --check plugins len-eq 2 \
    --check plugins.0.name eq qa-f26-pub --check plugins.0.description eq 'QA public plugin' \
    --check plugins.0.source eq "$PUB/plugins/qa-f26-pub" --check plugins.0.path eq "$PUB/plugins/qa-f26-pub" \
    --check plugins.0.ref eq null --check plugins.0.repo_path eq null --check plugins.0.installed eq false \
    --check plugins.0.skills contains '"name": "qa-f26-pub:qa-hello"' --check plugins.0.files contains .plugin/plugin.json \
    --check plugins.1.name eq qa-f26-src-plugin --check plugins.1.source eq "file://$G" \
    --check plugins.1.repo_path eq plugins/qa-f26-src-plugin --check plugins.1.installed eq true \
    --check plugins.1.path eq null --check plugins.1.skills eq null --check plugins.1.files eq null \
    --check plugins not-contains qa-f26-pub-standalone --save F26.marketplace-local/catalog
  SRC=$(control-agent-server api GET /api/plugins/marketplace --field plugins.0.source)
  control-agent-server api POST /api/plugins/install --json '{"source": "'"$SRC"'"}' --expect 200 \
    --check name eq qa-f26-pub --check source eq "$PUB/plugins/qa-f26-pub"
  control-agent-server api GET /api/plugins/marketplace --expect 200 --check plugins.0.installed eq true \
    --save F26.marketplace-local/installed
  ```
  Without `.plugin/marketplace.json` (and without the
  `marketplaces/default.json` fallback) the catalog is `{"plugins": []}`, and
  that empty answer is not cached: the next call, after the commit, fetches
  the stand-in again and lists the two `./plugins/`-style entries. The local
  entry resolves to its directory in the clone with skills and files; the
  entry in another repository carries attach coordinates
  (`source` + `repo_path`) and no local contents, and is `installed` because a
  plugin of that name is in the store. The `./skills/` entry is excluded.
  Installing from the catalog's `source` copies the clone's directory, and
  the next read shows `installed: true`.
- **Catalog cache (`F26.marketplace-cache`).** Commit a second local plugin
  to the stand-in, read the catalog, uninstall the catalog plugin, read
  again, then restart.
  ```sh
  cp -r "$E/plugins/qa-f26-pub" "$E/plugins/qa-f26-pub2"
  sed -i 's/"name": "qa-f26-pub"/"name": "qa-f26-pub2"/' "$E/plugins/qa-f26-pub2/.plugin/plugin.json"
  sed -i 's#\]}#, {"name": "qa-f26-pub2", "source": "./plugins/qa-f26-pub2", "description": "QA second plugin"}]}#' "$E/.plugin/marketplace.json"
  git -C "$E" add -A
  git -C "$E" commit -q -m 'QA second plugin'
  control-agent-server api GET /api/plugins/marketplace --expect 200 --check plugins len-eq 2 --check plugins not-contains qa-f26-pub2 \
    --check plugins.0.installed eq true --save F26.marketplace-cache/cached
  control-agent-server api DELETE /api/plugins/installed/qa-f26-pub --expect 200
  control-agent-server api GET /api/plugins/marketplace --expect 200 --check plugins len-eq 2 --check plugins.0.installed eq false
  control-agent-server restart
  control-agent-server api GET /api/plugins/marketplace --expect 200 --check plugins len-eq 3 \
    --check plugins.2.name eq qa-f26-pub2 --check plugins.2.skills contains '"name": "qa-f26-pub2:qa-hello"' \
    --save F26.marketplace-cache/after-restart
  ```
  Within five minutes of the last fetch the catalog structure comes from the
  process cache, so the new upstream entry is missing while `installed`
  follows the store at once. After a restart the catalog is fetched again
  and lists `qa-f26-pub2`. The TTL (`_PLUGIN_CATALOG_TTL_SECONDS = 300`) is
  read from source, not timed here.
- **TypeScript client (`F26.ts-client`).** Every `PluginsClient` method from
  the built client, on a plugin of its own.
  ```sh
  TSP=$(control-agent-server fixture plugin --name qa-f26-ts --print-path)
  cat > "$F/ts_plugins.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { PluginsClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const client = new PluginsClient({ host: process.env.AGENT_SERVER_URL, apiKey: process.env.SESSION_API_KEY });
  const source = process.env.QA_F26_TS_SOURCE;
  const ambient = (await client.getPlugins({ load_user: true, load_project: false })).plugins.map((p) => p.name);
  const installed = await client.installPlugin({ source });
  assert.equal(installed.name, 'qa-f26-ts');
  assert.equal(installed.enabled, true);
  assert.equal(installed.source, source);
  assert.equal((await client.getInstalledPlugin('qa-f26-ts')).install_path, installed.install_path);
  assert.ok((await client.listInstalledPlugins()).plugins.some((p) => p.name === 'qa-f26-ts'));
  const withTs = (await client.getPlugins({ load_user: true, load_project: false })).plugins.map((p) => p.name);
  assert.deepEqual(withTs, [...ambient, 'qa-f26-ts']);
  assert.deepEqual(await client.setPluginEnabled('qa-f26-ts', false), { name: 'qa-f26-ts', enabled: false });
  assert.equal((await client.getInstalledPlugin('qa-f26-ts')).enabled, false);
  assert.deepEqual((await client.getPlugins({ load_user: true, load_project: false })).plugins.map((p) => p.name), ambient);
  const refreshed = await client.refreshPlugin('qa-f26-ts');
  assert.equal(refreshed.message, "Plugin 'qa-f26-ts' updated");
  assert.equal(refreshed.plugin.enabled, false);
  const market = await client.getPluginsMarketplace();
  assert.deepEqual(market.plugins.map((p) => p.name), ['qa-f26-pub', 'qa-f26-src-plugin', 'qa-f26-pub2']);
  assert.deepEqual(await client.uninstallPlugin('qa-f26-ts'), { message: "Plugin 'qa-f26-ts' uninstalled" });
  await assert.rejects(client.getInstalledPlugin('qa-f26-ts'),
    (err) => err.name === 'HttpError' && err.status === 404 && err.detail === "Plugin 'qa-f26-ts' is not installed");
  await assert.rejects(client.installPlugin({ source: `${source}-missing` }),
    (err) => err.name === 'HttpError' && err.status === 400 && err.detail === 'Failed to fetch plugin source. Check that the source is valid.');
  client.close();
  console.log('QA_F26_TS_OK');
  JS
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  control-agent-server exec --env QA_F26_TS_SOURCE="$TSP" --timeout 180 --expect-output QA_F26_TS_OK --save F26.ts-client/program -- node "$F/ts_plugins.mjs"
  control-agent-server api GET /api/plugins/installed/qa-f26-ts --expect 404
  control-agent-server api GET /api/plugins/installed --expect 200 --check plugins len-eq 2
  ```
  The program prints `QA_F26_TS_OK`: install, get, list and the ambient
  catalog agree, the toggle answers `{name, enabled}` and takes the plugin
  out of the ambient catalog, refresh keeps it disabled, the marketplace
  lists the stand-in's three plugins, uninstall answers its message, and the
  errors reject with an `HttpError` whose `status` (and `detail`) are the
  server's. The server-side reads confirm the plugin is gone.
- **Public marketplace (`F26.marketplace-public`).** Remove the stand-in
  clone, restart, and read the catalog from the real `OpenHands/extensions`
  repository (network).
  ```sh
  rm -rf "$PUB" "$PUB.lock"
  control-agent-server restart
  control-agent-server api GET /api/plugins/marketplace --timeout 240 --expect 200 --check plugins len-ge 1 --quiet \
    --save F26.marketplace-public/catalog
  test -s "$PUB/.plugin/marketplace.json"
  N=$(python3 -c 'import json, sys; m = json.load(open(sys.argv[1])); print(sum(1 for p in m["plugins"] if str(p.get("source")).startswith("./plugins/")))' "$PUB/.plugin/marketplace.json")
  ALL=$(python3 -c 'import json, sys; print(len(json.load(open(sys.argv[1]))["plugins"]))' "$PUB/.plugin/marketplace.json")
  FIRST=$(python3 -c 'import json, sys; m = json.load(open(sys.argv[1])); print(next(p["name"] for p in m["plugins"] if str(p.get("source")).startswith("./plugins/")))' "$PUB/.plugin/marketplace.json")
  test "$N" -ge 1
  test "$ALL" -gt "$N"
  control-agent-server api GET /api/plugins/marketplace --expect 200 --quiet --check plugins len-eq "$N" \
    --check plugins.0.name eq "$FIRST" --check plugins.0.path eq "$PUB/plugins/$FIRST" --check plugins.0.files len-ge 1 \
    --check plugins.0.installed eq false
  ```
  The clone's `.plugin/marketplace.json` lists about 70 entries, most of them
  skills (`./skills/<name>`); the catalog has exactly the `./plugins/` ones
  (nine at the time of writing, `city-weather` first), each with its
  directory in the clone and its files. Without network the clone is missing
  and the bullet fails at the first read.

## Gotchas

- Store and caches: installed plugins live in
  `$OH_PERSISTENCE_DIR/plugins/installed/<manifest name>/` with
  `.installed.json`; git sources are cloned into
  `$OH_PERSISTENCE_DIR/cache/extensions/<repo>-<hash>/`; the marketplace
  reads `$OH_PERSISTENCE_DIR/cache/skills/public-skills` (shared with F25).
  The user directories are `~/.agents/plugins` and `$OH_PERSISTENCE_DIR/plugins`
  (the `installed/` child is skipped). All of these are fixed at import.
- The Claude Code layout is the fallback for any directory: a directory
  without a manifest installs under its own name with version `1.0.0`, and
  the whole directory is copied (a git source without `repo_path` copies the
  clone including `.git`, which `files` hides). Never install a large
  directory such as `/tmp` to test this.
- Every `ValueError` while loading a plugin (invalid JSON, missing `$schema`,
  Agent Plugins schema violations, a non-kebab-case name) answers the same
  `422 Invalid plugin. Ensure it has a valid kebab-case name.`; the real
  reason is in neither the response nor the server log (the log repeats the
  same detail), so tell the causes apart by the fixture, one defect at a time.
  Dotted names (`qa.f26-dotted`), which the Agent Plugins schema allows, are
  rejected by the store's kebab-case rule with the same 422.
- The install name comes from the manifest, not from the source directory;
  the 409 for an existing plugin is decided after the fetch, so a
  conflicting git install still clones.
- Only the list call, `PATCH`, `POST /api/plugins`, the marketplace (for
  its `installed` flags) and conversations reconcile `.installed.json` with
  the disk; the single view, `DELETE` and refresh do not, so call order
  matters for hand edits (`F26.self-heal`).
- Refresh reinstalls from the recorded `source` and `repo_path` with the ref
  dropped. A plugin installed from a marketplace entry records the absolute
  path into the public clone, so its refresh copies whatever the clone holds
  (only refreshed by a later catalog fetch). A plugin adopted from disk has
  source `local` and cannot be refreshed (`F26.refresh-unfetchable`).
- The marketplace always reads `https://github.com/OpenHands/extensions` at
  `EXTENSIONS_REF` (default `main`); an existing clone is updated from its own
  `origin`, which is what makes the local stand-in work. A non-empty catalog
  is cached for five minutes per process (restart to clear it); an empty one
  is not cached. `installed` is by name only: any installed plugin of that
  name counts, whatever its source.
- Ambient plugins load once per conversation, at its first message or run.
  Disabling or uninstalling a plugin does not unload it from a conversation
  that is already running; after a restart its hooks are gone but its skills
  and commands stay (`F26.conversation-resume-disabled`).
- `GET /api/conversations/{id}` trims `agent.agent_context.skills` unless
  `include_skills=true`, so a plain `GET` shows no plugin skills.
- `POST /api/plugins` needs a JSON body; without one it is 422 (the
  TypeScript client sends `{}`).
- Absence is asserted with `conversation events --kinds K --contains X
  --expect-count 0` (stored events) or `ws read|stop ... --kinds K --contains X
  --expect-none` (a socket capture), next to the same read matching where the
  plugin is enabled. Always restrict such checks with `--kinds`: the
  `full_state` frame sent on connect embeds the conversation's `hook_config`,
  including hook commands.
- Error 500s carry the exception text in an `exception` field; the
  `known bug` bullets save it as evidence.
