# Skills catalog, install and marketplace

Skills are the markdown instructions an agent loads by name, trigger word or
file path. Consumers ask the server for the merged skill catalog an agent
would get (sandbox hosts, registered marketplaces, the public
`OpenHands/extensions` repository, user skills, organization repositories and
a project directory, merged by name with later sources winning), force a
refresh of the cached public repository, and manage the user's installed
skills: install from a local directory or a git source, list, inspect, enable
or disable, refresh from the recorded source, and uninstall. Enabled installed
skills join the user tier of the catalog and of conversations that load user
skills, where a trigger word activates them. The marketplace catalog lists the
installable entries of the public repository with a fresh `installed` flag.

Source: `openhands-agent-server/openhands/agent_server/skills_router.py`, `openhands-agent-server/openhands/agent_server/skills_service.py`, `openhands-sdk/openhands/sdk/skills/`, `openhands-sdk/openhands/sdk/extensions/`, `openhands-sdk/openhands/sdk/marketplace/`, `openhands-sdk/openhands/sdk/git/cached_repo.py`, `openhands-sdk/openhands/sdk/context/agent_context.py`, `openhands-sdk/openhands/sdk/workspace/remote/base.py`, `clients/typescript/src/client/skills-client.ts`

Needs: `llm`, `git`, `network`, `node`

Routes: `POST /api/skills`, `POST /api/skills/sync`, `POST /api/skills/install`,
`GET /api/skills/installed`, `GET /api/skills/installed/{skill_name}`,
`PATCH /api/skills/installed/{skill_name}`,
`DELETE /api/skills/installed/{skill_name}`,
`POST /api/skills/installed/{skill_name}/refresh`,
`GET /api/skills/marketplace`

## Sub-features

- `F25.auth`: every skills route answers 401 without a valid session key, and nothing is installed.
- `F25.public-repo`: against the real public extensions repository (network), sync clones it into the public cache and `POST /api/skills` with `load_public` lists skills loaded from that clone.
- `F25.marketplace-public`: against the real public extensions repository, `GET /api/skills/marketplace` lists its installable entries; today it returns an empty catalog because the repository has `.plugin/marketplace.json` but no `marketplaces/default.json`.
- `F25.catalog-project`: `POST /api/skills` offline with a `project_dir` lists the project's AgentSkills (`agentskills`), legacy trigger skill (`knowledge`) and `AGENTS.md` (`repo`), counts them in `sources.project`, and silently skips a SKILL.md with an invalid name.
- `F25.catalog-user-precedence`: user skills from `~/.agents/skills` appear only with `load_user`, and a project skill overrides a user skill of the same name.
- `F25.catalog-server-sources`: `sandbox_config` URLs named `WORKER_*` become one always-on `work_hosts` skill, and `org_configs` repositories are cloned from a git URL (an unreachable one contributes nothing).
- `F25.catalog-validation`: a wrong type, an incomplete `org_configs` entry or a traversing marketplace `repo_path` is 422.
- `F25.install-local`: installing a local skill directory copies it into the installed store, records metadata (`resolved_ref` null), and the skill appears in the installed list, the single view, `.installed.json` and the user tier of `POST /api/skills`.
- `F25.enable-disable`: `PATCH {"enabled": false}` keeps the files but hides the skill from the catalog, `{"enabled": true}` restores it, and a body without `enabled` is 422.
- `F25.install-conflict`: re-installing an installed skill is 409 and changes nothing; `force: true` reinstalls it and keeps the disabled flag.
- `F25.install-errors`: an unfetchable source (missing path, unparsable source, escaping `repo_path`) is 400, an invalid skill (no SKILL.md, name mismatch, invalid directory name) is 422, an empty source is 422, and nothing is installed.
- `F25.install-bad-frontmatter`: a SKILL.md with malformed YAML frontmatter is rejected as an invalid skill (422); today it is a 500.
- `F25.install-git`: a `file://` git source with `repo_path` installs offline and records the source, `repo_path` and the resolved commit SHA.
- `F25.sdk-load-skills`: `RemoteWorkspace.load_skills_from_agent_server()` turns the merged catalog of its `working_dir` into SDK `Skill` objects (project, user and enabled installed skills, keyword triggers kept) and an `AgentContext` that does not reload public skills.
- `F25.install-github`: the working GitHub form (`github:OpenHands/extensions` plus `repo_path`) installs a public skill from github.com at a full commit SHA, and uninstalling it removes it.
- `F25.install-documented-sources`: every source form the OpenAPI description of `InstallSkillRequest.source` advertises installs; today both GitHub examples are 400 while `github:owner/repo` plus `repo_path` works.
- `F25.refresh`: refresh re-fetches from the recorded source: a new commit in the git source updates `resolved_ref` and the installed files, an edited local source updates the description, and the enabled flag is preserved.
- `F25.install-ref`: installing a git source with `ref` pins that commit (`resolved_ref`, the installed files, `requested_ref` in `.installed.json`); refresh moves it to the source's latest commit and drops `requested_ref`.
- `F25.install-unknown-ref`: a `ref` the git source does not have is 400 and leaves the installation unchanged; today that holds only for a first clone, while a source whose clone is cached installs the cached HEAD with 200.
- `F25.refresh-unfetchable`: refreshing a skill whose recorded source no longer exists is a client error and keeps the installation; today it is a 500.
- `F25.name-errors`: an unknown skill name is 404 and a name outside `^[a-z0-9]+(-[a-z0-9]+)*$` is 422 on get, enable/disable, uninstall and refresh.
- `F25.self-heal`: a skill directory copied into the store by hand is invisible to the single view until the list call discovers it with source `local`; a tracked directory removed by hand is pruned from the metadata by the list call.
- `F25.sync`: `POST /api/skills/sync` updates the cached public repository from its origin and clears the 60 s public-skills cache, so a skill committed upstream is listed immediately (translated: a local git origin stands in for GitHub).
- `F25.sync-offline`: when the public repository cannot be fetched, sync reports `status: error`; today it reports success and keeps serving the stale clone.
- `F25.catalog-registered-marketplace`: a registered marketplace with `auto_load` adds its standalone skills and plugin skills (and plugin commands) to the catalog, a name list selects some, and `auto_load: false` or `load_public: false` adds none.
- `F25.conversation-activation`: in a real DeepSeek conversation that loads user skills, a trigger word activates an enabled installed skill: `activated_knowledge_skills`, the user `MessageEvent` (REST and WebSocket replay) and the system prompt name it.
- `F25.invoke-skill`: an enabled installed AgentSkill without triggers is reachable only through the auto-attached `invoke_skill` tool; when the agent calls it, the observation carries the skill body and the conversation lists the skill in `invoked_skills`, not in `activated_knowledge_skills`.
- `F25.restart-persist`: installed skills, their enabled flags and resolved commits survive a server restart and keep their catalog visibility.
- `F25.catalog-server-marketplaces`: a `registered_marketplaces` entry in the server config contributes without a request entry, a request entry of the same name replaces it, and one of another name adds to it.
- `F25.marketplace-catalog`: the catalog lists the public repository's entries (name, description, absolute source) with `installed: false`, and installing an entry's `source` flips it to `installed: true` immediately (translated: a local stand-in with `marketplaces/default.json`).
- `F25.ts-client`: the TypeScript `SkillsClient` installs a skill from a git source, lists, reads, disables and re-enables it (`toggleSkill`, with `getSkills` following the flag), refreshes, syncs, reads the marketplace and uninstalls it; an unknown skill is a 404 `HttpError` and a client without a key gets 401.
- `F25.marketplace-stale-cache`: after a sync brings a new entry into the public repository, the catalog lists it; today the catalog keeps a 5-minute cache (including an empty one) that sync does not clear.
- `F25.uninstall`: uninstall deletes the directory and the metadata entry, the single view and a second uninstall are 404, the catalog drops the skill and the marketplace entry reads `installed: false` again.

## How to get to it (agent POV)

- REST, merged catalog: `POST /api/skills` with `SkillsRequest`
  (`load_public`, `load_user`, `load_project`, `load_org` all default to
  `true`, plus `project_dir`, `marketplace_path`, `registered_marketplaces`,
  `org_configs`, `sandbox_config`) returns `{"skills": [SkillInfo], "sources":
  {...}}`; `POST /api/skills/sync` force-updates the public repository clone.
  Driven by the `F25.catalog-*` and `F25.sync*` bullets.
- REST, installed skills: `POST /api/skills/install` (`source`, `ref`,
  `repo_path`, `force`), `GET /api/skills/installed`,
  `GET|PATCH|DELETE /api/skills/installed/{skill_name}` and
  `POST /api/skills/installed/{skill_name}/refresh`. The store is
  `$OH_PERSISTENCE_DIR/skills/installed/<name>/` with metadata in
  `.installed.json`; git sources are cached under
  `$OH_PERSISTENCE_DIR/cache/extensions/`.
- REST, marketplace: `GET /api/skills/marketplace` reads the public
  repository clone at `$OH_PERSISTENCE_DIR/cache/skills/public-skills`.
- Conversations: an agent whose `agent_context.load_user_skills` is true gets
  the enabled installed skills; a user message containing a skill's trigger
  injects it (`MessageEvent.activated_skills`, conversation
  `activated_knowledge_skills`). An AgentSkills-format skill also gets the
  built-in `invoke_skill` tool attached to the agent; the agent calls it by
  name and the conversation records `invoked_skills` (driven by
  `F25.invoke-skill`). Agent profiles resolve user and public skills
  at launch instead. The recipes enable `load_user_skills` in the saved
  settings and start conversations from them, as `conversation start` does.
- SDK: `RemoteWorkspace.load_skills_from_agent_server()` (and
  `CloudWorkspace`) call `POST /api/skills` with explicit flags and
  `project_dir`, retrying and returning `[]` on errors (driven by
  `F25.sdk-load-skills` through `exec`).
- TypeScript client: `SkillsClient.getSkills`, `syncSkills`, `installSkill`,
  `listInstalledSkills`, `getInstalledSkill`, `toggleSkill`, `uninstallSkill`,
  `refreshSkill`, `getMarketplace` (`clients/typescript/src/client/skills-client.ts`;
  every method is driven by `F25.ts-client`, a `node` program on the built
  `clients/typescript/dist`).
- Configuration: `OH_REGISTERED_MARKETPLACES` (or `registered_marketplaces`
  in the config file) adds server-default registrations that request entries
  override by name (driven through the config file by
  `F25.catalog-server-marketplaces`); `EXTENSIONS_REF` pins the public
  repository ref.
- WebSocket: `/sockets/events/{conversation_id}` carries the user
  `MessageEvent` with `activated_skills` (owned by the events family; the
  activation bullet replays it with `resend_mode=all`).
- Agent Canvas (context only): the skills page lists the catalog, installs,
  toggles and removes skills, and shows the Marketplace tab.

## Driving it with control-agent-server

Preconditions:

- A run is live and exported, `doctor` is ok, `$DEEPSEEK_API_KEY` is set and
  `git` and `node` are installed. No launch flags are needed. Outbound HTTPS
  to github.com is needed by `F25.public-repo` and `F25.install-github`, which
  arrange the network state for the two known-bug bullets after them
  (`F25.marketplace-public`, `F25.install-documented-sources`); every other
  bullet runs offline (apart from the DeepSeek calls). `F25.ts-client` builds
  `clients/typescript/dist` with `npm ci && npm run build` only when it is
  missing (that build needs the npm registry).
- The block below activates `deepseek-flash`, creates the run-owned fixtures
  (the `qa-f25-proj` project with an invalid extra skill, the `qa-f25-skill`
  skill, the `qa-f25-src` git source, the `qa-f25-mk` marketplace, two user
  skills in the run's `HOME`, broken skill directories for the error cases,
  and a local git repository `extensions` that later stands in for the public
  repository), and turns on `load_user_skills` in the saved settings so that
  conversations started from them load installed skills.
  ```sh
  control-agent-server llm preset deepseek
  export GIT_AUTHOR_NAME=QA
  export GIT_AUTHOR_EMAIL=qa@example.invalid
  export GIT_COMMITTER_NAME=QA
  export GIT_COMMITTER_EMAIL=qa@example.invalid
  P=$(control-agent-server fixture project --name qa-f25-proj --print-path)
  S=$(control-agent-server fixture skill --name qa-f25-skill --print-path)
  G=$(control-agent-server fixture git-source --name qa-f25-src --print-path)
  M=$(control-agent-server fixture marketplace --name qa-f25-mk --print-path)
  F="$AGENT_SERVER_VERIFY_RUN/fixtures/qa-f25"
  U="$AGENT_SERVER_VERIFY_RUN/home/.agents/skills"
  I="$AGENT_SERVER_VERIFY_RUN/home/.openhands/skills/installed"
  PUB="$AGENT_SERVER_VERIFY_RUN/home/.openhands/cache/skills/public-skills"
  E="$F/extensions"
  mkdir -p "$P/.agents/skills/qa-f25-badname" "$U/qa-f25-user" "$U/qa-f25-proj-skill" "$F/qa-f25-nomd" \
    "$F/qa-f25-mismatch" "$F/qa-f25-badyaml" "$F/Qa_Bad" "$E/skills/qa-f25-pub" "$E/.plugin" "$E/marketplaces"
  printf -- '---\nname: Not_Kebab\ndescription: QA invalid name\n---\nQA_BAD_NAME_BODY\n' > "$P/.agents/skills/qa-f25-badname/SKILL.md"
  printf -- '---\nname: qa-f25-user\ndescription: QA user skill\n---\nQA_USER_BODY\n' > "$U/qa-f25-user/SKILL.md"
  printf -- '---\nname: qa-f25-proj-skill\ndescription: QA user copy\n---\nQA_USER_COPY\n' > "$U/qa-f25-proj-skill/SKILL.md"
  printf '# not a skill\n' > "$F/qa-f25-nomd/README.md"
  printf -- '---\nname: qa-f25-other\ndescription: QA wrong name\n---\nBody\n' > "$F/qa-f25-mismatch/SKILL.md"
  printf -- '---\nname: qa-f25-badyaml\ndescription: [unclosed\n---\nBody\n' > "$F/qa-f25-badyaml/SKILL.md"
  printf -- '---\ndescription: QA invalid directory name\n---\nBody\n' > "$F/Qa_Bad/SKILL.md"
  printf -- '---\nname: qa-f25-pub\ndescription: QA public stand-in skill\n---\nQA_PUBLIC_BODY\n' > "$E/skills/qa-f25-pub/SKILL.md"
  printf '{"name": "qa-f25-ext", "owner": {"name": "QA"}, "plugins": [{"name": "qa-f25-pub", "source": "./skills/qa-f25-pub", "description": "QA public stand-in skill"}]}\n' \
    | tee "$E/.plugin/marketplace.json" > "$E/marketplaces/default.json"
  git -C "$E" init -q -b main
  git -C "$E" add -A
  git -C "$E" commit -q -m 'QA extensions stand-in'
  control-agent-server api PATCH /api/settings --json '{"agent_settings_diff": {"agent_context": {"load_user_skills": true}}}' \
    --expect 200 --check agent_settings.agent_context.load_user_skills eq true
  ```

- **Session key required (`F25.auth`).** Call every route without a key (one
  with a wrong key).
  ```sh
  control-agent-server api POST /api/skills --auth none --json '{"load_public": false}' --expect 401
  control-agent-server api POST /api/skills/sync --auth bad --expect 401
  control-agent-server api POST /api/skills/install --auth none --json '{"source": "'"$S"'"}' --expect 401
  control-agent-server api GET /api/skills/installed --auth none --expect 401
  control-agent-server api GET /api/skills/installed/qa-f25-skill --auth none --expect 401
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --auth none --json '{"enabled": false}' --expect 401
  control-agent-server api DELETE /api/skills/installed/qa-f25-skill --auth none --expect 401
  control-agent-server api POST /api/skills/installed/qa-f25-skill/refresh --auth none --expect 401
  control-agent-server api GET /api/skills/marketplace --auth none --expect 401 --save F25.auth/marketplace
  test ! -e "$I/qa-f25-skill"
  ```
  All nine answer `401 {"detail": "Unauthorized"}` and nothing is installed.
- **The real public repository (`F25.public-repo`).** Sync against
  `github.com/OpenHands/extensions` (network), which clones it into the public
  cache, then load the public tier from that clone.
  ```sh
  control-agent-server api POST /api/skills/sync --timeout 240 --expect 200 --check status eq success --save F25.public-repo/sync
  test -d "$PUB/.git"
  test -s "$PUB/.plugin/marketplace.json"
  control-agent-server api POST /api/skills --json '{"load_public": true, "load_user": false, "load_project": false, "load_org": false}' \
    --timeout 240 --expect 200 --quiet --check sources.sdk_base ge 1 --check skills len-ge 1 \
    --check skills contains "$PUB/skills/" --save F25.public-repo/catalog
  ```
  The first sync clones the repository (there is no clone yet, so this is the
  clone path, not a fetch). The clone holds `.plugin/marketplace.json`, and
  the public tier lists skills whose `source` is a `SKILL.md` inside the
  clone. This bullet is the network arrange step for
  `F25.marketplace-public`; without network it fails here.
- **Public marketplace catalog (`F25.marketplace-public`), known bug.** Read
  the catalog against the clone of the real `OpenHands/extensions`
  repository that `F25.public-repo` made.
  ```sh
  control-agent-server api GET /api/skills/marketplace --timeout 180 --expect 200 --check skills len-ge 1 --save F25.marketplace-public/catalog  # bug
  ```
  The clone holds `.plugin/marketplace.json` with about 70 plugin entries
  whose sources are `./skills/<name>`, so the catalog should list them. Today
  it returns `{"skills": []}`: `_fetch_catalog_entries` gives up when
  `marketplaces/default.json` is missing (the repository ships
  `marketplaces/openhands-extensions.json` and `large-codebase.json`
  instead) before it tries `Marketplace.load`, and the empty list is then
  cached for 5 minutes.
- **Project skills (`F25.catalog-project`).** Load only the fixture project.
  ```sh
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": false, "load_org": false, "project_dir": "'"$P"'"}' \
    --expect 200 --check sources.project eq 3 --check sources.sdk_base eq 0 --check sources.org eq 0 \
    --check sources.sandbox eq 0 --check sources.registered_marketplaces eq 0 --check skills len-eq 3 \
    --check skills contains '"name": "qa-f25-proj-skill", "type": "agentskills"' \
    --check skills contains '"name": "qa-legacy", "type": "knowledge"' \
    --check skills contains '"name": "agents", "type": "repo"' \
    --check skills contains '"triggers": ["qa-legacy"]' --check skills contains '"triggers": ["qa-f25-proj-skill"]' \
    --check skills contains '"is_agentskills_format": true' \
    --check skills not-contains QA_BAD_NAME_BODY --save F25.catalog-project/project
  ```
  Exactly three skills: the AgentSkills directory (with `is_agentskills_format`
  true and its name as trigger), the legacy `.openhands/skills/qa-legacy.md`
  and `AGENTS.md` (name `agents`, always on). The `Not_Kebab` skill is
  skipped with only a server log warning; the request still succeeds.
- **User skills and precedence (`F25.catalog-user-precedence`).** Load the
  user tier, then the user tier with the project.
  ```sh
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check sources.sdk_base eq 2 \
    --check skills contains '"name": "qa-f25-user", "type": "agentskills", "content": "QA_USER_BODY' \
    --check skills contains "\"source\": \"$U/qa-f25-user/SKILL.md\"" --save F25.catalog-user-precedence/user
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_org": false, "project_dir": "'"$P"'"}' \
    --expect 200 --check skills contains '"name": "qa-f25-user"' \
    --check skills contains "\"source\": \"$P/.agents/skills/qa-f25-proj-skill/SKILL.md\"" \
    --check skills not-contains QA_USER_COPY --save F25.catalog-user-precedence/project-wins
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": false, "load_project": false, "load_org": false}' \
    --expect 200 --check skills len-eq 0
  ```
  The user tier holds the two `HOME` skills; with the project, the project's
  `qa-f25-proj-skill` replaces the user copy (`QA_USER_COPY` is gone); with
  every source off the catalog is empty.
- **Sandbox and organization sources (`F25.catalog-server-sources`).** Pass
  exposed URLs, then two organization repositories (one unreachable).
  ```sh
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": false, "load_project": false, "load_org": false, "sandbox_config": {"exposed_urls": [{"name": "WORKER_1", "url": "http://localhost:12000", "port": 12000}, {"name": "VSCODE", "url": "http://localhost:12001", "port": 12001}]}}' \
    --expect 200 --check sources.sandbox eq 1 --check skills len-eq 1 --check skills.0.name eq work_hosts \
    --check skills.0.type eq repo --check skills.0.source missing \
    --check skills.0.content contains 'http://localhost:12000 (port 12000)' --check skills.0.content not-contains 12001 \
    --save F25.catalog-server-sources/sandbox
  ORG='[{"repository": "qa/org", "provider": "github", "org_repo_url": "file://'"$G"'", "org_name": "qa-f25-org"}, {"repository": "qa/gone", "provider": "github", "org_repo_url": "file:///nonexistent/qa-f25-gone", "org_name": "qa-f25-gone"}]'
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": false, "load_project": false, "load_org": true, "org_configs": '"$ORG"'}' \
    --expect 200 --check sources.org eq 1 --check skills len-eq 1 --check skills.0.name eq qa-f25-src-skill \
    --save F25.catalog-server-sources/org
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": false, "load_project": false, "load_org": false, "org_configs": '"$ORG"'}' \
    --expect 200 --check sources.org eq 0 --check skills len-eq 0
  ```
  Only `WORKER_*` names become the `work_hosts` skill (no source, always on);
  the organization repository is shallow-cloned and its `skills/` directory
  loaded, the unreachable one is skipped silently, and `load_org: false`
  ignores both.
- **Request validation (`F25.catalog-validation`).** Send malformed bodies.
  ```sh
  control-agent-server api POST /api/skills --json '{"load_public": "x"}' --expect 422 --check detail.0.loc contains load_public
  control-agent-server api POST /api/skills --json '{"load_public": false, "org_configs": [{"repository": "qa/org", "provider": "github", "org_repo_url": "file:///x"}]}' \
    --expect 422 --check detail.0.loc contains org_name
  control-agent-server api POST /api/skills --json '{"load_public": false, "registered_marketplaces": [{"name": "qa", "source": "/x", "repo_path": "../up"}]}' \
    --expect 422 --check detail.0.msg contains traversal --save F25.catalog-validation/repo-path
  ```
  Each is a 422 naming the offending field.
- **Install from a local directory (`F25.install-local`).** Install the
  fixture skill and read it back through every view.
  ```sh
  control-agent-server api POST /api/skills/install --json '{"source": "'"$S"'"}' --expect 200 \
    --check name eq qa-f25-skill --check enabled eq true --check source eq "$S" --check resolved_ref missing \
    --check install_path eq "$I/qa-f25-skill" --check installed_at exists --save F25.install-local/install
  test -f "$I/qa-f25-skill/SKILL.md"
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --check extensions.qa-f25-skill.source eq "$S"
  control-agent-server api GET /api/skills/installed --expect 200 --check skills len-eq 1 --check skills.0.name eq qa-f25-skill
  control-agent-server api GET /api/skills/installed/qa-f25-skill --expect 200 --check enabled eq true \
    --check description contains QA_SKILL_MARKER --save F25.install-local/get
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check sources.sdk_base eq 3 --check skills contains "\"source\": \"$I/qa-f25-skill/SKILL.md\""
  ```
  The response, the list, the single view and `.installed.json` agree; the
  user tier grows from two to three skills, the new one sourced from the
  installed copy.
- **Disable and enable (`F25.enable-disable`).** Toggle the installed skill.
  ```sh
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": false}' --expect 200 \
    --check name eq qa-f25-skill --check enabled eq false --save F25.enable-disable/disable
  control-agent-server api GET /api/skills/installed/qa-f25-skill --expect 200 --check enabled eq false
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --check extensions.qa-f25-skill.enabled eq false
  test -f "$I/qa-f25-skill/SKILL.md"
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check skills not-contains '"name": "qa-f25-skill"' --save F25.enable-disable/catalog-disabled
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": true}' --expect 200 --check enabled eq true
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": true}' --expect 200 --check enabled eq true
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check skills contains '"name": "qa-f25-skill"'
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{}' --expect 422 --check detail.0.loc contains enabled
  ```
  Disabled, the skill stays on disk but leaves the catalog; enabling (twice:
  idempotent) brings it back; a body without `enabled` is 422.
- **Already installed (`F25.install-conflict`).** Re-install without and with
  `force`.
  ```sh
  T1=$(control-agent-server api GET /api/skills/installed/qa-f25-skill --field installed_at)
  control-agent-server api POST /api/skills/install --json '{"source": "'"$S"'"}' --expect 409 \
    --check detail contains force=true --save F25.install-conflict/conflict
  control-agent-server api GET /api/skills/installed/qa-f25-skill --expect 200 --check installed_at eq "$T1"
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": false}' --expect 200
  control-agent-server api POST /api/skills/install --json '{"source": "'"$S"'", "force": true}' --expect 200 \
    --check enabled eq false --check installed_at ne "$T1" --save F25.install-conflict/force
  control-agent-server api GET /api/skills/installed/qa-f25-skill --expect 200 --check enabled eq false --check installed_at ne "$T1"
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": true}' --expect 200 --check enabled eq true
  ```
  The 409 leaves `installed_at` unchanged; the forced reinstall gets a new
  `installed_at` and keeps the skill disabled until it is enabled again.
- **Install errors (`F25.install-errors`).** Unfetchable sources, then invalid
  skills.
  ```sh
  control-agent-server api POST /api/skills/install --json '{"source": "/nonexistent/qa-f25"}' --expect 400 --check detail contains 'Failed to fetch'
  control-agent-server api POST /api/skills/install --json '{"source": "foo"}' --expect 400
  control-agent-server api POST /api/skills/install --json '{"source": "'"$S"'", "repo_path": "../.."}' --expect 400
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/qa-f25-nomd"'"}' --expect 422 \
    --check detail contains SKILL.md --save F25.install-errors/no-skill-md
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/qa-f25-mismatch"'"}' --expect 422
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/Qa_Bad"'"}' --expect 422
  control-agent-server api POST /api/skills/install --json '{"source": ""}' --expect 422 --check detail.0.loc contains source
  control-agent-server api GET /api/skills/installed --expect 200 --check skills len-eq 1
  test ! -e "$I/qa-f25-other" && test ! -e "$I/Qa_Bad" && test ! -e "$I/qa-f25-nomd"
  ```
  Fetch failures are `400 Failed to fetch skill source...`, invalid skills
  `422 Invalid skill. Ensure the source contains a valid SKILL.md.`, and the
  store still holds only `qa-f25-skill`.
- **Malformed frontmatter (`F25.install-bad-frontmatter`), known bug.**
  Install a SKILL.md whose YAML does not parse.
  ```sh
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/qa-f25-badyaml"'"}' --expect 422 \
    --save F25.install-bad-frontmatter/install  # bug
  test ! -e "$I/qa-f25-badyaml"
  ```
  An unparsable skill should be the documented 422 like the other invalid
  skills. Today it is `500 Internal Server Error` (with the YAML parser
  message): `Skill.load` lets `yaml` errors escape instead of raising
  `SkillValidationError`, and the route maps only that.
- **Install from a git source (`F25.install-git`).** Install the skill
  directory of the `qa-f25-src` repository through `file://`.
  ```sh
  H1=$(git -C "$G" rev-parse HEAD)
  control-agent-server api POST /api/skills/install --json '{"source": "file://'"$G"'", "repo_path": "skills/qa-f25-src-skill"}' \
    --expect 200 --check name eq qa-f25-src-skill --check source eq "file://$G" \
    --check repo_path eq skills/qa-f25-src-skill --check resolved_ref eq "$H1" --save F25.install-git/install
  control-agent-server api GET /api/skills/installed/qa-f25-src-skill --expect 200 --check resolved_ref eq "$H1"
  test -f "$I/qa-f25-src-skill/SKILL.md"
  ls -d "$AGENT_SERVER_VERIFY_RUN"/home/.openhands/cache/extensions/qa-f25-src-*/.git
  ```
  `file://` is treated as a git URL: the repository is cloned into the
  extensions cache and `resolved_ref` is the fixture's HEAD.
- **SDK consumer (`F25.sdk-load-skills`).** `RemoteWorkspace` with
  `working_dir` set to the fixture project loads skills through the server,
  as cloud conversations do, through `exec`.
  ```sh
  cat > "$F/qa_f25_sdk_skills.py" <<'PY'
  import json
  import os
  import sys

  from openhands.sdk.workspace import RemoteWorkspace

  project = sys.argv[1]
  ws = RemoteWorkspace(host=os.environ["AGENT_SERVER_URL"], api_key=os.environ["SESSION_API_KEY"], working_dir=project)
  skills, ctx = ws.load_skills_from_agent_server(load_public=False, load_org=False)
  by_name = {s.name: s for s in skills}
  print(json.dumps({n: [type(s.trigger).__name__, s.source] for n, s in sorted(by_name.items())}))
  assert set(by_name) == {"agents", "qa-legacy", "qa-f25-proj-skill", "qa-f25-user", "qa-f25-skill", "qa-f25-src-skill"}, by_name
  assert type(by_name["qa-legacy"].trigger).__name__ == "KeywordTrigger"
  assert by_name["agents"].trigger is None
  assert by_name["qa-f25-proj-skill"].source.startswith(project), by_name["qa-f25-proj-skill"].source
  assert "/skills/installed/qa-f25-skill/" in by_name["qa-f25-skill"].source
  assert ctx.load_public_skills is False
  assert {s.name for s in ctx.skills} == set(by_name)
  print("QA_F25_SDK_OK")
  PY
  control-agent-server exec --expect-output QA_F25_SDK_OK --save F25.sdk-load-skills/program -- uv run python "$F/qa_f25_sdk_skills.py" "$P"
  ```
  The SDK sends `project_dir` = `working_dir` with the flags it was given and
  gets exactly the six project, user and installed skills (the project copy
  of `qa-f25-proj-skill` wins); keyword triggers survive the conversion,
  `AGENTS.md` stays always-on, and the returned context carries the same
  skills with `load_public_skills` false.
- **Install from GitHub (`F25.install-github`).** Install the public
  `skills/github` skill from github.com (network) in the form that works, read
  it back, uninstall it.
  ```sh
  control-agent-server api POST /api/skills/install --json '{"source": "github:OpenHands/extensions", "repo_path": "skills/github"}' \
    --timeout 240 --expect 200 --check name eq github --check source eq github:OpenHands/extensions \
    --check repo_path eq skills/github --check resolved_ref matches '^[0-9a-f]{40}$' --save F25.install-github/install
  control-agent-server api GET /api/skills/installed/github --expect 200 --check resolved_ref matches '^[0-9a-f]{40}$'
  test -f "$I/github/SKILL.md"
  control-agent-server api DELETE /api/skills/installed/github --expect 200
  control-agent-server api GET /api/skills/installed/github --expect 404
  ```
  The shorthand names the repository and `repo_path` the skill directory; the
  repository is cloned into the extensions cache, `resolved_ref` is its HEAD
  commit, and the skill leaves the store again. This bullet is the network
  arrange step for `F25.install-documented-sources`: it proves github.com is
  reachable and `skills/github` installs.
- **Documented install sources (`F25.install-documented-sources`), known bug.**
  Install the same skill in each GitHub form the OpenAPI description of
  `InstallSkillRequest.source` still advertises.
  ```sh
  DOC=$(control-agent-server api GET /openapi.json --auth none --field components.schemas.InstallSkillRequest.properties.source.description)
  for SRC in github:OpenHands/extensions/skills/github https://github.com/OpenHands/extensions/tree/main/skills/github; do
    case "$DOC" in
      *"'$SRC'"*)
        control-agent-server api POST /api/skills/install --json '{"source": "'"$SRC"'"}' --timeout 240 --expect 200 \
          --check name eq github --save F25.install-documented-sources/documented  # bug
        control-agent-server api DELETE /api/skills/installed/github --expect 200
        ;;
    esac
  done
  ```
  `F25.install-github` showed the skill installs from github.com, so a
  documented example should install it too (or stop being documented, which
  also passes this bullet). Today both are `400 Failed to fetch skill
  source`: `parse_extension_source` accepts only `github:owner/repo` (one
  slash), and the `/tree/main/...` URL is cloned as
  `.../tree/main/skills/github.git`. The same text ships in the TypeScript
  client's generated schema.
- **Refresh from the source (`F25.refresh`).** Commit to the git source and
  edit the local source, then refresh both.
  ```sh
  control-agent-server fixture git-source --name qa-f25-src
  H2=$(git -C "$G" rev-parse HEAD)
  test "$H2" != "$H1"
  control-agent-server api POST /api/skills/installed/qa-f25-src-skill/refresh --expect 200 \
    --check message eq "Skill 'qa-f25-src-skill' updated" --check skill.resolved_ref eq "$H2" \
    --check skill.repo_path eq skills/qa-f25-src-skill --save F25.refresh/git
  control-agent-server api GET /api/skills/installed/qa-f25-src-skill --expect 200 --check resolved_ref eq "$H2"
  grep -q Revision "$I/qa-f25-src-skill/SKILL.md"
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": false}' --expect 200
  sed -i 's/^description: .*/description: QA fixture skill v2/' "$S/SKILL.md"
  control-agent-server api POST /api/skills/installed/qa-f25-skill/refresh --expect 200 \
    --check skill.description eq 'QA fixture skill v2' --check skill.enabled eq false --save F25.refresh/local
  control-agent-server api GET /api/skills/installed/qa-f25-skill --expect 200 --check description eq 'QA fixture skill v2' --check enabled eq false
  grep -q 'QA fixture skill v2' "$I/qa-f25-skill/SKILL.md"
  control-agent-server api PATCH /api/skills/installed/qa-f25-skill --json '{"enabled": true}' --expect 200
  ```
  The second `fixture git-source` call commits a change; refresh reinstalls
  at the new HEAD. The edited local description reaches the response, the
  single view and the installed file, and the skill stays disabled across the
  refresh (it is enabled again afterwards).
- **Install at a ref (`F25.install-ref`).** Reinstall the git skill pinned to
  the first commit, then refresh it.
  ```sh
  control-agent-server api POST /api/skills/install --json '{"source": "file://'"$G"'", "repo_path": "skills/qa-f25-src-skill", "ref": "'"$H1"'", "force": true}' \
    --expect 200 --check resolved_ref eq "$H1" --check enabled eq true --save F25.install-ref/pinned
  control-agent-server api GET /api/skills/installed/qa-f25-src-skill --expect 200 --check resolved_ref eq "$H1"
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --check extensions.qa-f25-src-skill.requested_ref eq "$H1"
  if grep -q Revision "$I/qa-f25-src-skill/SKILL.md"; then exit 1; fi
  control-agent-server api POST /api/skills/installed/qa-f25-src-skill/refresh --expect 200 \
    --check skill.resolved_ref eq "$H2" --save F25.install-ref/refresh
  grep -q Revision "$I/qa-f25-src-skill/SKILL.md"
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --check extensions.qa-f25-src-skill.requested_ref missing
  ```
  The pinned install checks out `H1` in the cached clone (no `Revision` line
  in the installed file) and records the requested ref. Refresh reinstalls
  with `ref` dropped, by design ("latest version"), so the skill is back at
  `H2` and `requested_ref` is gone.
- **Unknown ref (`F25.install-unknown-ref`), known bug.** Ask for a ref that
  neither git source has: first for a source never cloned, then for the
  cached `qa-f25-src` source.
  ```sh
  G2=$(control-agent-server fixture git-source --name qa-f25-ref --print-path)
  control-agent-server api POST /api/skills/install --json '{"source": "file://'"$G2"'", "repo_path": "skills/qa-f25-ref-skill", "ref": "qa-f25-no-such-ref"}' \
    --expect 400 --check detail contains 'Failed to fetch' --save F25.install-unknown-ref/first-clone
  test ! -e "$I/qa-f25-ref-skill"
  T2=$(control-agent-server api GET /api/skills/installed/qa-f25-src-skill --field installed_at)
  ls -d "$AGENT_SERVER_VERIFY_RUN"/home/.openhands/cache/extensions/qa-f25-src-*/.git
  control-agent-server api POST /api/skills/install --json '{"source": "file://'"$G"'", "repo_path": "skills/qa-f25-src-skill", "ref": "qa-f25-no-such-ref", "force": true}' \
    --expect 400 --save F25.install-unknown-ref/cached  # bug
  control-agent-server api GET /api/skills/installed/qa-f25-src-skill --expect 200 --check installed_at eq "$T2"
  ```
  The first clone fails (`git clone --branch qa-f25-no-such-ref`), so the
  install is `400` and nothing is installed (the positive control). The
  `qa-f25-src` clone is in the extensions cache, and that cached source should
  fail the same way. Today it answers `200` and reinstalls the cached HEAD (`H2`)
  with `requested_ref: "qa-f25-no-such-ref"` in `.installed.json`:
  `_try_checkout_and_reset` only logs `Failed to checkout ... Using cached
  version.` and `fetch_with_resolution` resolves whatever is checked out.
  From source, a real ref that the cache does not hold yet ends the same way
  when the fetch fails (offline).
- **Refresh of a vanished source (`F25.refresh-unfetchable`), known bug.**
  Install from a temporary directory, delete it, refresh.
  ```sh
  mkdir -p "$F/tmp/qa-f25-gone"
  printf -- '---\nname: qa-f25-gone\ndescription: QA source that disappears\n---\nGone soon\n' > "$F/tmp/qa-f25-gone/SKILL.md"
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/tmp/qa-f25-gone"'"}' --expect 200
  rm -rf "$F/tmp/qa-f25-gone"
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/tmp/qa-f25-gone"'"}' --expect 400 --check detail contains 'Failed to fetch'
  control-agent-server api POST /api/skills/installed/qa-f25-gone/refresh --expect 400,422 --save F25.refresh-unfetchable/refresh  # bug
  control-agent-server api GET /api/skills/installed/qa-f25-gone --expect 200 --check enabled eq true
  ```
  The source cannot be fetched: installing from the vanished path again is
  `400 Failed to fetch skill source` (the positive control), so the refresh
  route should report a client error too and keep the installed copy. Today it is
  `500` (`Local extension path does not exist: ...`): `refresh_skill_endpoint`
  does not catch `ExtensionFetchError`/`SkillValidationError`. The installed
  copy does survive. A skill discovered by `F25.self-heal` (source `local`)
  fails the same way.
- **Unknown and invalid names (`F25.name-errors`).** Address a skill that is
  not installed, then a name that breaks the pattern.
  ```sh
  control-agent-server api GET /api/skills/installed/qa-f25-ghost --expect 404 --check detail eq "Skill 'qa-f25-ghost' is not installed"
  control-agent-server api PATCH /api/skills/installed/qa-f25-ghost --json '{"enabled": false}' --expect 404
  control-agent-server api DELETE /api/skills/installed/qa-f25-ghost --expect 404
  control-agent-server api POST /api/skills/installed/qa-f25-ghost/refresh --expect 404 --save F25.name-errors/refresh-unknown
  control-agent-server api GET /api/skills/installed/Bad_Name --expect 422 --check detail.0.loc contains skill_name
  control-agent-server api PATCH /api/skills/installed/Bad_Name --json '{"enabled": false}' --expect 422
  control-agent-server api DELETE /api/skills/installed/Bad_Name --expect 422
  control-agent-server api POST /api/skills/installed/Bad_Name/refresh --expect 422 --save F25.name-errors/bad-name
  ```
  Unknown names are `404 Skill '<name>' is not installed`; `Bad_Name` fails
  path validation (`string_pattern_mismatch`) on all four routes.
- **Hand-copied and hand-deleted directories (`F25.self-heal`).** Copy a
  skill into the store, read it, then remove it by hand.
  ```sh
  mkdir -p "$I/qa-f25-manual"
  printf -- '---\nname: qa-f25-manual\ndescription: QA manual copy\n---\nManual\n' > "$I/qa-f25-manual/SKILL.md"
  control-agent-server api GET /api/skills/installed/qa-f25-manual --expect 404
  control-agent-server api GET /api/skills/installed --expect 200 --check skills contains '"name": "qa-f25-manual"' --save F25.self-heal/discovered
  control-agent-server api GET /api/skills/installed/qa-f25-manual --expect 200 --check source eq local --check enabled eq true
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --check extensions.qa-f25-manual.source eq local
  rm -rf "$I/qa-f25-manual"
  control-agent-server api GET /api/skills/installed --expect 200 --check skills not-contains qa-f25-manual
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --not-contains qa-f25-manual
  ```
  The single view does not sync metadata, so it is 404 until the list call
  records the directory with source `local`; after the directory is deleted
  the next list call prunes the entry.
- **Sync the public repository (`F25.sync`).** Translated: the cached clone is
  replaced by a clone of the local `extensions` repository, so sync fetches
  from a local origin instead of GitHub through the same code path.
  ```sh
  rm -rf "$PUB"
  git clone -q "$E" "$PUB"
  control-agent-server api POST /api/skills/sync --expect 200 --check status eq success
  Q='{"load_public": true, "load_user": false, "load_project": false, "load_org": false, "marketplace_path": null}'
  control-agent-server api POST /api/skills --json "$Q" --expect 200 --check skills contains '"name": "qa-f25-pub"' --check skills not-contains qa-f25-pub-new
  mkdir -p "$E/skills/qa-f25-pub-new"
  printf -- '---\nname: qa-f25-pub-new\ndescription: QA new upstream skill\n---\nQA_PUBLIC_NEW\n' > "$E/skills/qa-f25-pub-new/SKILL.md"
  git -C "$E" add -A
  git -C "$E" commit -q -m 'Add a new upstream skill'
  control-agent-server api POST /api/skills --json "$Q" --expect 200 --check skills not-contains qa-f25-pub-new
  control-agent-server api POST /api/skills/sync --expect 200 --check status eq success \
    --check message eq 'Skills repository synced successfully' --save F25.sync/sync
  test "$(git -C "$PUB" rev-parse HEAD)" = "$(git -C "$E" rev-parse HEAD)"
  control-agent-server api POST /api/skills --json "$Q" --expect 200 \
    --check skills contains '"name": "qa-f25-pub-new", "type": "agentskills", "content": "QA_PUBLIC_NEW' --save F25.sync/after-sync
  control-agent-server api POST /api/skills --json '{"load_public": true, "load_user": false, "load_project": false, "load_org": false}' \
    --expect 200 --check skills contains '"name": "qa-f25-pub"' --check skills not-contains qa-f25-pub-new
  ```
  Before the sync the new upstream skill is hidden by the 60 s in-process
  cache (the server's own timer, hence no wait); after it the clone's HEAD
  equals the origin's and the skill is listed. With the default
  `marketplace_path` only the entries of `marketplaces/default.json` load, so
  `qa-f25-pub-new` stays out.
- **Sync without a reachable origin (`F25.sync-offline`), known bug.** Point
  the clone's origin at a missing path, sync, restore it on exit.
  ```sh
  trap 'git -C "$PUB" remote set-url origin "$E"' EXIT
  git -C "$PUB" remote set-url origin "$F/qa-f25-unreachable"
  if git -C "$PUB" fetch -q origin 2>/dev/null; then false; fi
  control-agent-server api POST /api/skills/sync --expect 200 --check status eq error --save F25.sync-offline/sync  # bug
  ```
  The origin is unreachable (a plain `git fetch` fails before the sync), and
  the server's fetch fails too (the log shows `Failed to fetch updates`), so the forced
  refresh did not happen and the response should say `status: error`. Today
  it says `{"status": "success", "message": "Skills repository synced
  successfully"}`: `update_skills_repository` returns the stale clone after a
  failed fetch and `sync_public_skills` treats any path as success. The same
  happens offline against GitHub once a clone exists.
- **Registered marketplace skills (`F25.catalog-registered-marketplace`).**
  Register the `qa-f25-mk` fixture in the request (the public tier is the
  offline stand-in now).
  ```sh
  control-agent-server api POST /api/skills --json '{"load_public": true, "load_user": false, "load_project": false, "load_org": false, "registered_marketplaces": [{"name": "qa-f25-mk", "source": "'"$M"'", "auto_load": true}]}' \
    --expect 200 --check sources.registered_marketplaces eq 3 --check skills contains '"name": "qa-f25-mk-skill"' \
    --check skills contains '"name": "qa-f25-mk-plugin-skill"' --check skills contains '"name": "qa-f25-mk-plugin:qa-hello"' \
    --check skills contains '"name": "qa-f25-pub"' --save F25.catalog-registered-marketplace/auto-load
  control-agent-server api POST /api/skills --json '{"load_public": true, "load_user": false, "load_project": false, "load_org": false, "registered_marketplaces": [{"name": "qa-f25-mk", "source": "'"$M"'", "auto_load": ["qa-f25-mk-skill"]}]}' \
    --expect 200 --check sources.registered_marketplaces eq 1 --check skills contains '"name": "qa-f25-mk-skill"' \
    --check skills not-contains qa-f25-mk-plugin
  control-agent-server api POST /api/skills --json '{"load_public": true, "load_user": false, "load_project": false, "load_org": false, "registered_marketplaces": [{"name": "qa-f25-mk", "source": "'"$M"'", "auto_load": false}]}' \
    --expect 200 --check sources.registered_marketplaces eq 0
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": false, "load_project": false, "load_org": false, "registered_marketplaces": [{"name": "qa-f25-mk", "source": "'"$M"'", "auto_load": true}]}' \
    --expect 200 --check sources.registered_marketplaces eq 0 --check skills len-eq 0
  ```
  `auto_load: true` adds the standalone skill, the plugin's skill and the
  plugin's command (a `knowledge` skill named `qa-f25-mk-plugin:qa-hello`)
  next to the public skill; a name list selects the standalone skill only;
  `false`, or `load_public: false`, adds nothing.
- **Installed skill in a conversation (`F25.conversation-activation`).** Start
  a DeepSeek conversation from the saved settings whose first message carries
  the installed skill's trigger word.
  ```sh
  CID=$(control-agent-server conversation start --tools none --no-autotitle \
    --prompt 'qa-f25-skill: reply with one short sentence.' --wait --until finished,idle --timeout 300 --print-id)
  control-agent-server api GET "/api/conversations/$CID" --expect 200 \
    --check activated_knowledge_skills contains qa-f25-skill --save F25.conversation-activation/conversation
  control-agent-server conversation events "$CID" --kinds MessageEvent --contains '"activated_skills": ["qa-f25-skill"]' \
    --save F25.conversation-activation/message
  control-agent-server conversation events "$CID" --kinds MessageEvent --contains 'skills/installed/qa-f25-skill/SKILL.md'
  control-agent-server conversation events "$CID" --kinds SystemPromptEvent --contains '<name>qa-f25-skill</name>'
  control-agent-server ws listen "/sockets/events/$CID" --query resend_mode=all --until-kind MessageEvent \
    --until activated_skills.0=qa-f25-skill --duration 15 --save F25.conversation-activation/ws
  ```
  The conversation lists the skill in the system prompt's
  `<available_skills>`, the user `MessageEvent` has `activated_skills:
  ["qa-f25-skill"]` with the skill body injected from the installed copy's
  path (in the REST event search and in the WebSocket replay alike), and
  `activated_knowledge_skills` names it. The model's reply is not asserted.
- **Skill invoked through `invoke_skill` (`F25.invoke-skill`).** Install an
  AgentSkill without triggers, then ask a DeepSeek conversation to invoke it
  by name; uninstall it afterwards.
  ```sh
  mkdir -p "$F/qa-f25-invoke"
  printf -- '---\nname: qa-f25-invoke\ndescription: QA skill without triggers\n---\nQA_INVOKE_BODY\n' > "$F/qa-f25-invoke/SKILL.md"
  control-agent-server api POST /api/skills/install --json '{"source": "'"$F/qa-f25-invoke"'"}' --expect 200 --check name eq qa-f25-invoke
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check skills contains '"name": "qa-f25-invoke", "type": "agentskills", "content": "QA_INVOKE_BODY' \
    --check skills contains '"triggers": [], "source": "'"$I/qa-f25-invoke/SKILL.md"'"'
  CID2=$(control-agent-server conversation start --tools none --no-autotitle \
    --prompt 'Call the invoke_skill tool with name "qa-f25-invoke", then finish.' --wait --until finished,idle --timeout 300 --print-id)
  control-agent-server conversation events "$CID2" --kinds SystemPromptEvent --contains '<name>qa-f25-invoke</name>'
  control-agent-server conversation events "$CID2" --kinds SystemPromptEvent --contains '"kind": "InvokeSkillTool"'
  control-agent-server conversation events "$CID2" --kinds ActionEvent --contains '"name": "qa-f25-invoke", "kind": "InvokeSkillAction"' \
    --save F25.invoke-skill/action
  control-agent-server conversation events "$CID2" --kinds ObservationEvent --contains '"skill_name": "qa-f25-invoke"' \
    --save F25.invoke-skill/observation
  control-agent-server conversation events "$CID2" --kinds ObservationEvent --contains '"skill_name": "qa-f25-invoke"' --full \
    | grep QA_INVOKE_BODY > /dev/null
  control-agent-server conversation events "$CID2" --kinds MessageEvent --contains '"activated_skills": ["qa-f25-invoke"]' --expect-count 0
  control-agent-server api GET "/api/conversations/$CID2" --expect 200 --check invoked_skills contains qa-f25-invoke \
    --check activated_knowledge_skills not-contains qa-f25-invoke --save F25.invoke-skill/conversation
  control-agent-server api DELETE /api/skills/installed/qa-f25-invoke --expect 200
  ```
  The skill has no trigger (`"triggers": []`), so the prompt naming it
  activates nothing (no `MessageEvent` has it in `activated_skills`): the
  system prompt lists it in `<available_skills>` and carries the
  auto-attached `InvokeSkillTool`, the agent's `InvokeSkillAction` returns
  an `InvokeSkillObservation` (`skill_name`) with the skill body and its
  installed location, and the conversation records the skill in
  `invoked_skills` only. The model's reply is not asserted.
- **Restart (`F25.restart-persist`).** Restart with one skill disabled.
  ```sh
  control-agent-server api PATCH /api/skills/installed/qa-f25-src-skill --json '{"enabled": false}' --expect 200
  control-agent-server restart
  control-agent-server doctor
  control-agent-server api GET /api/skills/installed --expect 200 --check skills contains '"name": "qa-f25-skill"' \
    --check skills contains '"name": "qa-f25-src-skill"' --save F25.restart-persist/list
  control-agent-server api GET /api/skills/installed/qa-f25-src-skill --expect 200 --check enabled eq false --check resolved_ref eq "$H2"
  control-agent-server api GET /api/skills/installed/qa-f25-skill --expect 200 --check enabled eq true --check description eq 'QA fixture skill v2'
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check skills contains '"name": "qa-f25-skill"' --check skills not-contains '"name": "qa-f25-src-skill"'
  ```
  Installation state lives on disk, so the restarted server reports the same
  skills, flags and commit, and the disabled skill stays out of the catalog.
  Restarts also empty the in-process catalog caches, which
  `F25.marketplace-catalog` relies on.
- **Server-default marketplaces (`F25.catalog-server-marketplaces`).** Restart
  with the `qa-f25-mk` fixture registered in the config file, then override it
  by name and add a second registration from the request.
  ```sh
  control-agent-server restart --config-json '{"registered_marketplaces": [{"name": "qa-f25-mk", "source": "'"$M"'", "auto_load": ["qa-f25-mk-skill"]}]}'
  control-agent-server doctor
  B='"load_public": true, "load_user": false, "load_project": false, "load_org": false'
  control-agent-server api POST /api/skills --json "{$B}" --expect 200 --check sources.registered_marketplaces eq 1 \
    --check skills contains '"name": "qa-f25-mk-skill"' --check skills not-contains qa-f25-mk-plugin \
    --save F25.catalog-server-marketplaces/config-only
  control-agent-server api POST /api/skills --json "{$B"', "registered_marketplaces": [{"name": "qa-f25-mk", "source": "'"$M"'", "auto_load": false}]}' \
    --expect 200 --check sources.registered_marketplaces eq 0 --check skills not-contains qa-f25-mk \
    --save F25.catalog-server-marketplaces/override
  control-agent-server api POST /api/skills --json "{$B"', "registered_marketplaces": [{"name": "qa-f25-mk-2", "source": "'"$M"'", "auto_load": ["qa-f25-mk-plugin"]}]}' \
    --expect 200 --check sources.registered_marketplaces eq 3 --check skills contains '"name": "qa-f25-mk-skill"' \
    --check skills contains '"name": "qa-f25-mk-plugin-skill"' --save F25.catalog-server-marketplaces/union
  control-agent-server restart --reset-config
  control-agent-server api POST /api/skills --json "{$B}" --expect 200 --check sources.registered_marketplaces eq 0
  ```
  The config registration alone adds its selected standalone skill; a request
  entry named `qa-f25-mk` replaces it (here with `auto_load: false`, so
  nothing); a request entry of another name is added next to it (the plugin's
  skill and command plus the config's skill). After `--reset-config` the
  server has no default registration again. This is the same merge
  `OH_REGISTERED_MARKETPLACES` feeds.
- **Marketplace catalog (`F25.marketplace-catalog`).** Translated: the public
  clone is the local stand-in, which has `marketplaces/default.json`. Read the
  catalog, install its entry, read it again.
  ```sh
  control-agent-server api GET /api/skills/marketplace --expect 200 --check skills len-eq 1 --check skills.0.name eq qa-f25-pub \
    --check skills.0.description eq 'QA public stand-in skill' --check skills.0.source eq "$PUB/skills/qa-f25-pub" \
    --check skills.0.installed eq false --save F25.marketplace-catalog/before
  SRC=$(control-agent-server api GET /api/skills/marketplace --field skills.0.source)
  control-agent-server api POST /api/skills/install --json '{"source": "'"$SRC"'"}' --expect 200 --check name eq qa-f25-pub
  control-agent-server api GET /api/skills/marketplace --expect 200 --check skills.0.installed eq true --save F25.marketplace-catalog/after-install
  ```
  A relative entry source becomes an absolute path into the clone, which the
  install route accepts as a local source; `installed` is recomputed on every
  call, so it flips at once.
- **TypeScript client (`F25.ts-client`).** `SkillsClient` from the built
  client in `clients/typescript/dist` (built here when missing) walks the
  installed-skill lifecycle on its own git source, then REST reads the result.
  ```sh
  test -f clients/typescript/dist/clients.js || (cd clients/typescript && npm ci && npm run build)
  G3=$(control-agent-server fixture git-source --name qa-f25-ts --print-path)
  H3=$(git -C "$G3" rev-parse HEAD)
  cat > "$F/qa_f25_ts.mjs" <<'JS'
  import assert from 'node:assert/strict';
  import { resolve } from 'node:path';
  import { pathToFileURL } from 'node:url';

  const { SkillsClient } = await import(pathToFileURL(resolve('clients/typescript/dist/clients.js')).href);
  const [src, head] = process.argv.slice(2);
  const host = process.env.AGENT_SERVER_URL;
  const client = new SkillsClient({ host, apiKey: process.env.SESSION_API_KEY });
  const name = 'qa-f25-ts-skill';
  const userTier = { load_public: false, load_user: true, load_project: false, load_org: false };
  const inCatalog = async () => (await client.getSkills(userTier)).skills.some((s) => s.name === name);

  const info = await client.installSkill({ source: `file://${src}`, repo_path: `skills/${name}` });
  assert.equal(info.name, name);
  assert.equal(info.enabled, true);
  assert.equal(info.resolved_ref, head);
  assert.ok((await client.listInstalledSkills()).skills.some((s) => s.name === name));
  assert.equal((await client.getInstalledSkill(name)).resolved_ref, head);
  assert.ok(await inCatalog());
  assert.deepEqual(await client.toggleSkill(name, false), { name, enabled: false });
  assert.equal((await client.getInstalledSkill(name)).enabled, false);
  assert.equal(await inCatalog(), false);
  assert.deepEqual(await client.toggleSkill(name, true), { name, enabled: true });
  assert.ok(await inCatalog());
  const refreshed = await client.refreshSkill(name);
  assert.equal(refreshed.message, `Skill '${name}' updated`);
  assert.equal(refreshed.skill.resolved_ref, head);
  assert.equal(refreshed.skill.enabled, true);
  assert.equal((await client.syncSkills()).status, 'success');
  const pub = (await client.getMarketplace()).skills.find((s) => s.name === 'qa-f25-pub');
  assert.equal(pub.installed, true);
  assert.deepEqual(await client.uninstallSkill(name), { message: `Skill '${name}' uninstalled` });
  await assert.rejects(client.getInstalledSkill(name), (e) => e.status === 404);
  await assert.rejects(new SkillsClient({ host }).listInstalledSkills(), (e) => e.status === 401);
  console.log('QA_F25_TS_OK');
  JS
  control-agent-server exec --timeout 180 --expect-output QA_F25_TS_OK --save F25.ts-client/program -- node "$F/qa_f25_ts.mjs" "$G3" "$H3"
  control-agent-server api GET /api/skills/installed --expect 200 --check skills not-contains qa-f25-ts-skill
  test ! -e "$I/qa-f25-ts-skill"
  control-agent-server api GET /api/skills/marketplace --expect 200 --check skills.0.name eq qa-f25-pub --check skills.0.installed eq true
  ```
  Every client method returns what the matching REST route returns: the
  install at the source's HEAD, the list and single view, `toggleSkill`
  hiding and restoring the skill in `getSkills`, a refresh that keeps it
  enabled, a successful sync of the stand-in clone, the marketplace entry
  installed by `F25.marketplace-catalog`, and the uninstall that REST then
  confirms. An unknown skill rejects with a 404 `HttpError`; a client without
  `apiKey` gets 401.
- **Catalog after a sync (`F25.marketplace-stale-cache`), known bug.** Warm
  the catalog, list a second entry upstream, sync, read the catalog.
  ```sh
  control-agent-server api GET /api/skills/marketplace --expect 200 --check skills not-contains qa-f25-pub-extra
  mkdir -p "$E/skills/qa-f25-pub-extra"
  printf -- '---\nname: qa-f25-pub-extra\ndescription: QA second catalog entry\n---\nQA_PUBLIC_EXTRA\n' > "$E/skills/qa-f25-pub-extra/SKILL.md"
  printf '{"name": "qa-f25-ext", "owner": {"name": "QA"}, "plugins": [{"name": "qa-f25-pub", "source": "./skills/qa-f25-pub", "description": "QA public stand-in skill"}, {"name": "qa-f25-pub-extra", "source": "./skills/qa-f25-pub-extra", "description": "QA second catalog entry"}]}\n' \
    | tee "$E/.plugin/marketplace.json" > "$E/marketplaces/default.json"
  git -C "$E" add -A
  git -C "$E" commit -q -m 'List a second catalog entry'
  control-agent-server api POST /api/skills/sync --expect 200 --check status eq success
  test -f "$PUB/skills/qa-f25-pub-extra/SKILL.md"
  grep -q '"name": "qa-f25-pub-extra"' "$PUB/marketplaces/default.json"
  control-agent-server api GET /api/skills/marketplace --expect 200 --check skills contains '"name": "qa-f25-pub-extra"' \
    --save F25.marketplace-stale-cache/after-sync  # bug
  ```
  The sync brings the new entry into the clone, so a refreshed catalog should
  list it. Today the catalog serves its module-level 5-minute cache: sync only
  clears the public-skills cache, and unlike that cache the catalog also
  caches an empty result (so an offline first call or `F25.marketplace-public`
  leaves the tab empty for 5 minutes). A restart clears it.
- **Uninstall (`F25.uninstall`).** Remove the marketplace skill and the local
  skill.
  ```sh
  control-agent-server api DELETE /api/skills/installed/qa-f25-pub --expect 200 \
    --check message eq "Skill 'qa-f25-pub' uninstalled" --save F25.uninstall/delete
  control-agent-server api GET /api/skills/installed/qa-f25-pub --expect 404
  test ! -e "$I/qa-f25-pub"
  control-agent-server state cat home/.openhands/skills/installed/.installed.json --not-contains '"qa-f25-pub"'
  control-agent-server api DELETE /api/skills/installed/qa-f25-pub --expect 404
  control-agent-server api GET /api/skills/marketplace --expect 200 --check skills.0.name eq qa-f25-pub --check skills.0.installed eq false
  control-agent-server api DELETE /api/skills/installed/qa-f25-skill --expect 200
  control-agent-server api GET /api/skills/installed --expect 200 --check skills not-contains '"name": "qa-f25-skill"' \
    --check skills not-contains '"name": "qa-f25-pub"' --save F25.uninstall/list
  control-agent-server api POST /api/skills --json '{"load_public": false, "load_user": true, "load_project": false, "load_org": false}' \
    --expect 200 --check skills not-contains '"name": "qa-f25-skill"'
  ```
  The directory and the metadata entry are gone, a second uninstall is 404,
  the marketplace entry reads `installed: false` again, and the user tier no
  longer has `qa-f25-skill`. `qa-f25-src-skill` and `qa-f25-gone` stay
  installed in this run.

## Gotchas

- `load_public`, `load_user`, `load_project` and `load_org` all default to
  `true`: an empty `POST /api/skills` body clones `github.com/OpenHands/extensions`
  (git, network, up to a 120 s clone timeout). Offline recipes send
  `load_public: false`, or seed the clone first as `F25.sync` does.
- The skills paths are frozen at import (`Path.home()` and
  `OH_PERSISTENCE_DIR`): user skills `~/.agents/skills`,
  `$OH_PERSISTENCE_DIR/skills` and `.../microagents`; the installed store
  `$OH_PERSISTENCE_DIR/skills/installed`; the public clone
  `$OH_PERSISTENCE_DIR/cache/skills/public-skills`. `launch` sets them per
  run; changing them on a live server has no effect.
- Project discovery walks up to the nearest `.git`, so a `project_dir` inside
  another checkout also loads that checkout's `AGENTS.md` and skills. The
  `fixture project` directory is its own repository for this reason.
- SKILL.md directories are strict: the frontmatter `name` (if any) must equal
  the directory name and match `^[a-z0-9]+(-[a-z0-9]+)*$`. In the catalog a
  bad skill is skipped with only a log warning; install answers 422.
- `sources` counts are per tier before the name merge (`sdk_base` is public
  plus user plus enabled installed skills), so their sum can exceed the
  number of skills.
- Registered marketplaces contribute only when `load_public` is true; the
  server-default registrations (`OH_REGISTERED_MARKETPLACES`) merge with the
  request's, the request winning by name.
- The public-skills cache lives 60 s (forever for a tag or SHA
  `EXTENSIONS_REF`) and caches only non-empty results; `/api/skills/sync`
  clears it. The marketplace catalog has its own 5-minute cache that sync does
  not clear and that also keeps an empty result
  (`F25.marketplace-stale-cache`); restart the server to clear it.
- The public clone is updated through its own `origin`, which is what makes
  the local stand-in work: replacing `public-skills` with a clone of a local
  repository makes `load_public`, sync and the marketplace catalog offline and
  deterministic. The real repository has no `marketplaces/default.json`, so
  `load_public` loads every `skills/` entry there while the catalog stays
  empty (`F25.marketplace-public`).
- Install sources: plain absolute paths are copied; `file://` and other URLs
  are cloned into `$OH_PERSISTENCE_DIR/cache/extensions/`. The examples in the
  OpenAPI description of `InstallSkillRequest.source` do not work
  (`F25.install-documented-sources`); the working form is
  `{"source": "github:OpenHands/extensions", "repo_path": "skills/github"}`.
- The 409 for an existing skill is decided after the fetch, so a conflicting
  git install still clones. `version` is always `1.0.0` (the frontmatter
  `version` is not read). Refresh reinstalls with `ref` dropped, so a skill
  installed at a tag moves to the default branch (`F25.install-ref`). A
  `ref` that cannot be checked out in an already cached clone is ignored
  with a log warning (`F25.install-unknown-ref`): compare `resolved_ref`
  with the commit you asked for.
- Only the list call and `PATCH` reconcile `.installed.json` with the disk;
  the single view, `DELETE` and refresh do not, so call order matters for hand
  edits (`F25.self-heal`).
- Conversations load installed skills only when `agent_context.load_user_skills`
  is true; the default settings have it false, hence the `PATCH /api/settings`
  precondition. Agent profiles (`conversation start --agent-profile default`)
  resolve user and public skills at launch instead, but their default tools
  include the terminal, whose tmux socket path exceeds the Unix limit under
  long run directories (`launch --name` longer than a few characters);
  `--tools none` on the settings path avoids tmux.
- An AgentSkills-format skill in the agent's context auto-attaches the
  built-in `invoke_skill` tool, even with `--tools none`. A SKILL.md without
  `triggers` is never activated by a keyword, so the agent reaches it only
  through `invoke_skill`, which records `invoked_skills` instead of
  `activated_knowledge_skills` (`F25.invoke-skill`). Models tend to echo the
  skill body in their reply, so select the `InvokeSkillObservation` by its
  `skill_name` before looking for the body text.
- Network: only `F25.public-repo` and `F25.install-github` reach github.com.
  They are normal bullets so that the two known-bug bullets after them start
  from a proven clone or install; without network the run fails at them
  rather than reporting a misleading `xfail`.
- `conversation events --contains` fails when no selected event contains the
  text, so it asserts content on its own. `ws listen` has no content filter;
  `--until-kind K --until FIELD=VALUE` fails unless a frame of kind `K` with
  that field arrives within `--duration`, which is how the activation bullet
  asserts the WebSocket replay.
- Error 500s carry the exception text in an `exception` field; the
  `known bug` bullets save it as evidence.
