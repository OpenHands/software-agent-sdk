# Plugins, marketplaces and the extension store

A plugin is a directory that bundles skills, slash commands, hooks, MCP servers and sub-agent definitions. A Python program attaches plugins with `Conversation(..., plugins=[PluginSource(source=..., ref=..., repo_path=...)])`, where the source is a local path, `github:owner/repo`, or any git URL. On the first `send_message()` or `run()` the conversation fetches each source into a git cache, records the resolved commit SHA in `conversation.resolved_plugins`, loads the directory in one of two layouts (the Claude Code layout with `.plugin/` or `.claude-plugin/plugin.json`, or the portable Agent Plugins v1.0.0 layout with a root `plugin.json`), and merges it into the agent: skills and command-derived skills override same-named skills, MCP servers override same-named servers, hooks are appended after the explicit `hook_config`, and agent definitions are registered as sub-agent types. Plugins in `~/.agents/plugins`, `~/.openhands/plugins`, the workspace's `.agents/plugins` / `.openhands/plugins`, and enabled plugins in the install store (`install_plugin` and friends) are merged the same way without being asked for. A marketplace is a `marketplace.json` that lists plugins and standalone skills; `AgentContext(registered_marketplaces=[MarketplaceRegistration(...)])` makes its entries loadable, with `auto_load` choosing which ones start with the conversation and `conversation.load_plugin("name@marketplace")` loading one later. Plugins and installed skills share a generic installation layer in `openhands.sdk.extensions`.

Source: `openhands-sdk/openhands/sdk/plugin/`, `openhands-sdk/openhands/sdk/marketplace/`, `openhands-sdk/openhands/sdk/extensions/`, `openhands-sdk/openhands/sdk/git/`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/context/agent_context.py`

Needs: `llm` (example demo 1 and real-LLM test), `git`, `network` (GitHub sources), `uvx` (the example plugin's `fetch` MCP server)

Examples: `examples/05_skills_and_plugins/02_loading_plugins/main.py`, `examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py` (marketplace manifest)

## Sub-features

- `S08.claude-code-layout`: `Plugin.load(dir)` reads `.plugin/plugin.json` or `.claude-plugin/plugin.json` (author as string or object; no manifest means the name comes from the directory), `skills/` (or a root `SKILL.md` for a single-skill plugin), `commands/`, `agents/`, `hooks/hooks.json` and `.mcp.json`; one bad skill is skipped without dropping the rest. Unit: `tests/sdk/plugin/test_plugin_loading.py`. Example: `examples/05_skills_and_plugins/02_loading_plugins/main.py` (`example_plugins/code-quality`). Real-LLM: none.
- `S08.agent-plugins-layout`: a directory with a root `plugin.json` is loaded as an Agent Plugins v1.0.0 package (detected ahead of the Claude Code fallback): the manifest must validate against the vendored schema version, `skills/` and `mcp.json` are portable components, and hooks, commands, agents and `entry_command` come only from the `dev.openhands/` extension directory and namespace; a fatal violation rejects the whole package. Unit: `tests/sdk/plugin/test_agent_plugins_format.py`, `tests/sdk/plugin/test_agent_plugins_conformance.py`. Example: none. Real-LLM: `tests/integration/tests/t10_agent_plugins_skill.py`.
- `S08.agent-plugins-mcp`: an Agent Plugins `mcp.json` expands only `${PLUGIN_ROOT}` and `${PLUGIN_DATA}` (single pass), resolves plugin-relative commands and `cwd` inside the package, requires `https` for non-loopback URLs, rejects user info and fragments, drops client-generated headers, and skips an invalid entry while its siblings load; an invalid document disables MCP only. Unit: `tests/sdk/plugin/test_agent_plugins_mcp.py`. Example: none. Real-LLM: none.
- `S08.path-containment`: manifests, skills, agents, commands and MCP files that resolve outside the plugin root (symlinks included) are denied while the rest of the plugin loads. Unit: `tests/sdk/plugin/test_path_containment.py`. Example: none. Real-LLM: none.
- `S08.commands-as-skills`: each `commands/<name>.md` becomes a keyword-triggered skill `/<plugin>:<name>` with its description and `$ARGUMENTS` hint, and `entry_command` exposes `Plugin.entry_slash_command`. Unit: `tests/sdk/plugin/test_plugin_loading.py` (`test_command_to_skill_conversion`, `test_get_all_skills_with_commands`, `test_load_plugin_with_entry_command`), `tests/sdk/conversation/test_local_conversation_agent_plugins.py::TestSkills::test_the_entry_command_arrives_as_a_skill`. Example: none. Real-LLM: none.
- `S08.merge-semantics`: `Plugin.add_skills_to(ctx, max_skills=)` and `add_mcp_config_to(mcp)` return new objects in which plugin entries override same-named ones (with a warning) and `max_skills` raises when exceeded; `load_plugins(specs, agent)` applies this in order (last plugin wins) and concatenates hooks via `HookConfig.merge`. Unit: `tests/sdk/plugin/test_plugin_merging.py`, `tests/sdk/plugin/test_plugin_loader.py`. Example: none. Real-LLM: none.
- `S08.conversation-attach`: `Conversation(plugins=[...])` (also through the `Conversation` factory) loads plugins lazily on the first `send_message()`/`run()`, puts their skills in `conversation.agent.agent_context.skills`, starts their MCP servers, and runs their hooks after the explicit `hook_config`. Unit: `tests/sdk/conversation/test_local_conversation_plugins.py::TestLocalConversationPlugins` (`test_create_conversation_with_plugins`, `test_plugins_not_loaded_until_needed`, `test_conversation_with_multiple_plugins`, `test_plugin_hooks_combined_with_explicit_hooks`, `test_plugin_mcp_config_are_initialized`), `::TestConversationFactoryPlugins`, `tests/sdk/conversation/test_local_conversation_agent_plugins.py`. Example: `examples/05_skills_and_plugins/02_loading_plugins/main.py` (demo 1). Real-LLM: `tests/integration/tests/t10_agent_plugins_skill.py`.
- `S08.plugin-agents`: agent definitions from a plugin's `agents/` are registered as sub-agent types for the task tool (details in S11). Unit: `tests/sdk/conversation/test_local_conversation_agent_plugins.py::TestClientExtension::test_agents_are_registered`. Example: none. Real-LLM: none.
- `S08.resolved-refs`: `conversation.resolved_plugins` lists each explicitly attached plugin with its resolved commit SHA (`None` for a local path), built from the original spec so `${VAR}` placeholders and inline credentials are not stored. Unit: `tests/sdk/conversation/test_local_conversation_plugins.py::TestLocalConversationPlugins::test_create_conversation_with_plugins`, `tests/sdk/git/test_url_redaction.py::TestResolvedPluginSourceCredentialRedaction`. Example: `examples/05_skills_and_plugins/02_loading_plugins/main.py` (prints `Resolved plugin refs`). Real-LLM: none.
- `S08.secret-expansion`: `${VAR}` in a plugin source, ref and Claude Code `.mcp.json` is expanded from the conversation's secrets (never from the host env), defaults apply only when no secret exists, Agent Plugins package values are left literal, and load logs never print credentials. Unit: `tests/sdk/conversation/test_local_conversation_plugins.py::TestPluginMcpSecretsExpansion`, `::TestPluginSourceSecretExpansion`, `::TestAgentPluginsMCPExpansion`. Example: none. Real-LLM: none.
- `S08.fetch-cache`: `fetch_plugin(source, ref=, repo_path=, update=)` clones git sources into `~/.openhands/cache/plugins/` (keyed by source), updates or reuses the cache, checks out a ref, returns `repo_path` inside the clone, and raises `PluginFetchError` on git or path errors; local paths are returned as-is. Unit: `tests/sdk/plugin/test_plugin_fetch.py`, `tests/sdk/plugin/test_plugin_fetch_integration.py`, `tests/sdk/extensions/test_fetch.py`. Example: `examples/05_skills_and_plugins/02_loading_plugins/main.py`. Real-LLM: none.
- `S08.source-parsing`: `github:owner/repo`, https, ssh and `git://` URLs (GitHub, GitLab, Bitbucket, self-hosted) and local paths are classified; GitHub `blob`/`tree` URLs split into repo, branch and subpath; `repo_path` must be relative without `..`. Unit: `tests/sdk/extensions/test_fetch.py`, `tests/sdk/plugin/test_source.py`, `tests/sdk/plugin/test_plugin_loader.py::TestPluginSource`. Example: none. Real-LLM: none.
- `S08.installed-plugins`: `install_plugin(source, ref=, installed_dir=, force=)` copies a fetched plugin into `~/.openhands/plugins/installed/` and records it in `.installed.json`; `list_installed_plugins`, `load_installed_plugins` (enabled only), `disable_plugin`/`enable_plugin`, `get_installed_plugin`, `update_plugin` and `uninstall_plugin` act on that store. Unit: `tests/sdk/plugin/test_installed_plugins.py`. Example: `examples/05_skills_and_plugins/02_loading_plugins/main.py` (demos 2 to 6). Real-LLM: none.
- `S08.installation-manager`: the generic `InstallationManager[T]` validates names (no path traversal), records requested and resolved refs, keeps the enabled flag across `force` reinstalls, re-clones on `update`, and migrates legacy `plugins`/`skills` keys of `.installed.json` into `extensions`. Unit: `tests/sdk/extensions/installation/`. Example: none. Real-LLM: none.
- `S08.ambient-plugins`: `load_user_plugins()` (`~/.agents/plugins`, then `~/.openhands/plugins`, then enabled installed plugins) and `load_project_plugins(work_dir)` (work dir and git root, `.agents/plugins` then `.openhands/plugins`) are merged into every `LocalConversation`, project over user; an explicitly attached plugin of the same name wins, and ambient plugins are not listed in `resolved_plugins`. Unit: `tests/sdk/plugin/test_discovery.py`, `tests/sdk/conversation/test_local_conversation_plugins.py::TestAmbientPluginAutoLoad`. Example: none. Real-LLM: none.
- `S08.marketplace-manifest`: `Marketplace.load(dir)` reads `.plugin/marketplace.json` or `.claude-plugin/marketplace.json` with `name`, `owner`, `metadata`, `plugins` (string source or `{source: github|url, repo, url, ref, path}`) and `skills`, and `resolve_plugin_source(entry)` turns an entry into `(source, ref, repo_path)` relative to the marketplace. Unit: `tests/sdk/marketplace/test_marketplace.py`. Example: `examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py` (default mode prints the manifest). Real-LLM: none.
- `S08.marketplace-registry`: `MarketplaceRegistry(registrations)` rejects duplicate names, fetches and caches each manifest lazily (keeping the resolved ref), and `resolve_plugin("name@market")` or `resolve_plugin("name")` returns a `PluginSource`, raising `PluginNotFoundError`, `AmbiguousPluginError` or `MarketplaceNotFoundError`. Unit: `tests/sdk/marketplace/test_marketplace_registry.py`. Example: none. Real-LLM: none.
- `S08.marketplace-auto-load`: `AgentContext(registered_marketplaces=[MarketplaceRegistration(name, source, ref, repo_path, auto_load=True | [names])])` loads the chosen plugins and standalone skills when the conversation starts; a plugin wins over a standalone skill of the same name, explicit `plugins=` win over auto-loaded ones, a marketplace that fails to fetch is skipped, and `auto_load=False` only registers it. Unit: `tests/sdk/conversation/test_local_conversation_plugins.py::TestLocalConversationPlugins` (`test_auto_load_marketplace_plugins`, `test_auto_load_marketplace_standalone_skills`, `test_plugin_wins_over_standalone_on_name_collision`, `test_auto_load_marketplace_continues_after_fetch_failure`, `test_registered_only_marketplace_does_not_auto_load`, `test_explicit_plugins_override_auto_loaded_marketplace_plugins`), `tests/sdk/skills/test_installed_skills.py::TestLoadMarketplaceStandaloneSkills`. Example: none. Real-LLM: none.
- `S08.load-plugin-runtime`: `conversation.load_plugin("name@market")` resolves through the registered marketplaces, adds the plugin's skills, MCP tools and hooks to a running conversation without rebuilding existing tools, and raises when no marketplace is registered. Unit: `tests/sdk/conversation/test_local_conversation_plugins.py::TestLocalConversationPlugins` (`test_load_plugin_from_registered_marketplace`, `test_load_plugin_adds_runtime_tools_without_reinitializing_existing_tools`, `test_load_plugin_merges_runtime_hooks_and_restarts_processor`, `test_load_plugin_requires_registered_marketplaces`). Example: none. Real-LLM: none.
- `S08.marketplace-import-path`: marketplace classes import only from `openhands.sdk.marketplace`; the old `openhands.sdk.plugin` names raise. Unit: `tests/sdk/marketplace/test_deprecation.py`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `Conversation(agent=..., workspace=..., plugins=[PluginSource(source="github:owner/repo" | "<git url>" | "/abs/path", ref="v1.0.0", repo_path="plugins/x")])`, then `conversation.resolved_plugins` and `conversation.agent.agent_context.skills` after the first `send_message()`. Covers `S08.conversation-attach`, `S08.resolved-refs`, `S08.secret-expansion`, `S08.plugin-agents`.
- `openhands.sdk.plugin`: `Plugin.load(path)`, `Plugin.load_all(dir)`, `Plugin.fetch(source, ref=, repo_path=)`, `fetch_plugin_with_resolution`, `plugin.add_skills_to(ctx)`, `plugin.add_mcp_config_to(mcp)`, `plugin.get_all_skills()`, `load_plugins(specs, agent, max_skills=100)`, `detect_format(dir)`, `AgentPluginsFormat`, `ClaudeCodePluginFormat`, `get_plugin_data_dir`. Covers the layout, merge and fetch sub-features.
- Install store: `install_plugin`, `uninstall_plugin`, `enable_plugin`, `disable_plugin`, `list_installed_plugins`, `load_installed_plugins`, `get_installed_plugin`, `update_plugin` (all take `installed_dir=`). `S08.installed-plugins`.
- Ambient discovery: `load_user_plugins()`, `load_project_plugins(work_dir)`, `load_available_plugins(work_dir, include_user=, include_project=)`; called by every `LocalConversation`. `S08.ambient-plugins`.
- `openhands.sdk.marketplace`: `Marketplace.load(dir)`, `MarketplaceRegistration`, `MarketplaceRegistry`; `AgentContext(registered_marketplaces=[...])`; `LocalConversation.load_plugin("name@market")`. Covers `S08.marketplace-*`, `S08.load-plugin-runtime`.
- `openhands.sdk.extensions`: `fetch` / `fetch_with_resolution`, `parse_extension_source`, `installation.InstallationManager`, `InstallationInterface`. `S08.installation-manager`, `S08.source-parsing`.
- Files on disk: `<plugin>/.plugin/plugin.json` or `.claude-plugin/plugin.json`, `<plugin>/plugin.json` (Agent Plugins), `skills/`, `commands/`, `agents/`, `hooks/hooks.json`, `.mcp.json` (Claude Code) or `mcp.json` (Agent Plugins), `dev.openhands/`; `<repo>/.plugin/marketplace.json`; `~/.openhands/plugins/installed/.installed.json`; `~/.openhands/cache/plugins/` (git cache); `~/.agents/plugins/`, `<workspace>/.agents/plugins/`.
- Env vars: `OH_PERSISTENCE_DIR` relocates every `~/.openhands` path above; hook commands from plugins see `OPENHANDS_PROJECT_DIR` and `OPENHANDS_TOOL_NAME` (S10).

## Driving it

Preconditions:
- `uv sync --dev` has run, `git` is on `PATH`, and `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does all three.
- Live bullets need `DEEPSEEK_API_KEY`. Example 02 also needs network for `github:anthropics/skills`, and `uvx` for the `code-quality` plugin's `fetch` server.

The programs build every plugin, marketplace and git repository inside the run (`programs/S08/build.py`). Git sources are local repositories reached through `file://` URLs, so nothing goes over the network. Plugin MCP servers start `programs/S09/mcp_server.py` over stdio. Each scripted conversation records the tools that every LLM call offered (`recorder.py`).

- **Claude Code layout, Agent Plugins layout and its `mcp.json`, containment, commands, merging and source strings (`S08.claude-code-layout`, `S08.agent-plugins-layout`, `S08.agent-plugins-mcp`, `S08.path-containment`, `S08.commands-as-skills`, `S08.merge-semantics`, `S08.source-parsing`).** These calls are made with `Plugin.load`, without a conversation. The inputs are:
  - a Claude Code plugin: a broken skill, a command, an agent, a hook and `.mcp.json`
  - a `.plugin/` variant, a plugin with no manifest, and a single-skill plugin
  - an Agent Plugins package. Its `mcp.json` uses `${PLUGIN_ROOT}`, `${PLUGIN_DATA}` and `${TOKEN}`, and has an absolute command, an `http://` remote and a `Host` header.
  - a fatal manifest, a broken `mcp.json`, and a plugin whose skill and `.mcp.json` are symlinks out of its root
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/layouts \
    --check cc.format eq ClaudeCodePluginFormat --check cc.skills eq '["cc-plugin-skill"]' \
    --check cc.commands eq '["review"]' --check cc.agents eq '["cc-plugin-helper"]' \
    --check cc.hook_events eq '["post_tool_use"]' --check cc.mcp_servers eq '["cc-plugin-mcp"]' \
    --check author_as_string eq "Plugin Author" --check dot_plugin_name eq dot-plugin \
    --check no_manifest_name eq no-manifest --check single_skill_plugin eq '["single"]' \
    --check command_skill.name eq cc-plugin:review --check command_skill.triggers eq '["/cc-plugin:review"]' \
    --check command_skill.description eq "Review the change." --check command_skill.has_arguments_hint eq true \
    --check portable.format eq AgentPluginsFormat --check portable.skills eq '["summarize"]' \
    --check portable.commands eq '["report"]' --check portable.agents eq '["portable-helper"]' \
    --check portable.entry_slash_command eq /portable:report \
    --check portable.mcp_servers eq '["local-http", "tools"]' --check portable.tools_command matches '/portable/bin/serve\.sh$' \
    --check portable.root_expanded eq true --check portable.data_expanded eq true \
    --check portable.literal_kept eq '${TOKEN}' --check portable.local_http_headers eq '["X-Root"]' \
    --check fatal_manifest_error contains "Invalid Agent Plugins manifest" \
    --check bad_mcp.skills eq '["still-here"]' --check bad_mcp.mcp_servers eq '[]' \
    --check contained.skills eq '["contained-skill"]' --check contained.mcp_servers eq '[]' \
    --check merge.names eq '["cc-plugin-skill", "other", "cc-plugin:review"]' \
    --check merge.overridden_content eq CC-PLUGIN-SKILL-BODY --check merge.original_untouched eq true \
    --check merge.max_skills_error eq "ValueError: Total skills (3) exceeds maximum (2)" \
    --check merge_mcp.names eq '["cc-plugin-mcp", "keep"]' --check merge_mcp.overridden eq true \
    --check merge_mcp.original_untouched eq true --check load_all eq '["cc-plugin", "portable", "single"]' \
    --check sources.0 eq '["github:owner/repo", "github", "https://github.com/owner/repo.git"]' \
    --check sources.1 eq '["https://gitlab.com/org/repo", "git", "https://gitlab.com/org/repo.git"]' \
    --check sources.2 eq '["git@bitbucket.org:team/repo.git", "git", "git@bitbucket.org:team/repo.git"]' \
    --check sources.3.1 eq git --check sources.4.1 eq local --check sources.5.1 eq local \
    --check bad_source_error contains "Invalid GitHub shorthand format" \
    --check repo_path_absolute_error eq true --check repo_path_dotdot_error eq true \
    --check github_blob eq '["owner", "repo", "main", "plugins/x"]'
  ```
  What the checks show:
  - The broken skill is skipped and its sibling skill still loads.
  - The command becomes the skill `/cc-plugin:review`. Its `argument-hint` is kept.
  - The Agent Plugins package expands only its two placeholders and keeps `${TOKEN}` literal. It drops the absolute-command server, the insecure remote and the `Host` header.
  - A fatal manifest rejects the whole package. A broken `mcp.json` disables only MCP.
  - The symlinked skill and `.mcp.json` are denied.
  - A merge returns new objects and leaves the originals unchanged.
- **Plugins attached to a conversation, with ambient plugins (`S08.conversation-attach`, `S08.plugin-agents`, `S08.resolved-refs`, `S08.ambient-plugins`).** The conversation is built with:
  - `Conversation(plugins=[...])`:
    - the local Claude Code plugin
    - a git plugin whose source is the secret `${GITP_URL}`, with `ref="v1"` and `repo_path="plugins/gitp"`. The repository has moved on to v2 after the v1 tag.
    - the Agent Plugins package
  - an explicit `hook_config`
  - an `amb` plugin in both `~/.agents/plugins` and `<workspace>/.agents/plugins`
  - a user plugin named `cc-plugin` whose skill says `AMBIENT-SHADOW`

  The scripted agent first sends `/cc-plugin:review`, then runs `echo hi`, calls the plugins' MCP tools, and finishes.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/attach --arg conversation \
    --check resolved_before eq null --check skills_before eq '[]' \
    --check skills_after eq '["amb-skill", "cc-plugin-skill", "cc-plugin:review", "gitp-skill", "portable:report", "summarize"]' \
    --check cc_skill_body eq CC-PLUGIN-SKILL-BODY --check gitp_skill_body eq GITP-V1 --check amb_skill_body eq PROJECT-AMB \
    --check command_injected eq true \
    --check first_tools contains cc-plugin-mcp_getenv --check first_tools contains tools_getenv \
    --check observations.0 eq hi \
    --check registered_agents eq '["cc-plugin-helper", "portable-helper"]' \
    --check hook_log.0 eq explicit --check hook_log.1 eq plugin-cc-plugin --check hook_log len-eq 14 \
    --check resolved len-eq 3 --check resolved.0.resolved_ref eq null \
    --check resolved.1.source eq '${GITP_URL}' --check resolved.1.original_ref eq v1 \
    --check resolved.1.repo_path eq plugins/gitp --check git_ref_is_v1 eq true \
    --check resolved_has_secret_value eq false --check resolved_names_ambient eq false
  ```
  What the checks show:
  - Nothing loads before the first message.
  - The explicit `cc-plugin` wins over the ambient one of the same name, and the project `amb` wins over the user `amb`.
  - The git plugin is pinned at v1.
  - The plugin hook runs after the explicit hook on each of the 7 tool calls.
  - `resolved_plugins` keeps the placeholder rather than the secret's value, and lists no ambient plugin.
- **Secret expansion in plugin sources and `.mcp.json` (`S08.secret-expansion`).** This runs the same conversation as the bullet above, with these values:
  - conversation secrets: `TOKEN=secret-token` and `PLUGIN_KEY=plugin-key`
  - host environment: `TOKEN=host-token`
  - the Claude Code `.mcp.json` env: `TOKEN=${TOKEN}`, `KEY=${PLUGIN_KEY}` and `DFLT=${NOPE:-fallback}`
  - the Agent Plugins `mcp.json`: `LITERAL=${TOKEN}`

  The server's `getenv` tool returns each value reversed.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/attach --arg conversation \
    --check observations.2 contains yek-nigulp --check observations.3 contains fallback \
    --check observations.4 contains '${TOKEN}' --check observations.5 matches '/src/portable$' \
    --check resolved.1.source eq '${GITP_URL}' --check resolved_has_secret_value eq false \
    --check observations.1 contains nekot-terces --check observations.1 not-contains nekot-tsoh
  ```
  Expected results:
  - Secrets reach the Claude Code server, and a default applies only when no secret exists.
  - The Agent Plugins server gets `${TOKEN}` literally.
  - The git source is fetched through the secret.
  - `TOKEN` comes from the conversation secret (`nekot-terces`), never from the host.

  Known failure (reproduced 2026-10-13, not yet filed): the server receives the host's `TOKEN` (`nekot-tsoh` is `host-token` reversed) instead of the conversation secret `secret-token`. A placeholder with no host variable (`${PLUGIN_KEY}`) correctly takes the secret. The cause is that `Plugin.load` reads `.mcp.json` through `load_mcp_config` with no secret lookup. `expand_mcp_variables` uses `check_env=True`, so at load time `${TOKEN}` is replaced from `os.environ`. When the conversation later applies its secrets, the placeholder is already gone. Any variable a plugin names that also exists in the agent process's environment leaks that value into the plugin's MCP server, and the user's secret is ignored.
- **A plugin source that cannot be fetched (`S08.conversation-attach`, `S08.fetch-cache`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/attach --arg bad-source --check constructed eq true --check send_error eq PluginFetchError
  ```
- **The git fetch cache (`S08.fetch-cache`).** The program calls `fetch_plugin` in these cases:
  - a tag with `repo_path`
  - a second repository fetched from its default branch
  - after a new commit, a fetch with `update=False`, then the default fetch
  - a missing `repo_path`
  - a URL nothing serves
  - a local directory
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/attach --arg fetch \
    --check cached_under_persistence eq true --check cache_dir_name matches '^fetchable-[0-9a-f]{16}$' \
    --check v1_version eq v1 --check default_version eq v1 --check same_cache_dir eq true \
    --check distinct_cache_per_url eq true --check no_update_version eq v1 --check update_version eq v2 \
    --check missing_repo_path_error eq PluginFetchError --check bad_url_error eq PluginFetchError \
    --check local_returned_as_is eq true
  ```
- **A tag requested after the default branch was cached (`S08.fetch-cache`).** A repository has `v1` tagged and `v2` on `main`. It is fetched from its default branch, and then with `ref="v1"`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/attach --arg fetch-ref \
    --check default_version eq v2 --check v1_after_cached_clone eq v1 --check v1_resolved_is_v1_sha eq true
  ```
  Expected: the second fetch checks out the tag and returns v1 with v1's SHA. Known failure (reproduced 2026-10-13, not yet filed): it returns the cached v2 content and v2's SHA. The cached clone is shallow and on a single branch, so the tag is not there. The log shows `git checkout v1` failing with `pathspec 'v1' did not match`, followed by `Using cached version.` No error is raised. A `PluginSource(ref="v1.0.0")`, or a marketplace pin, silently loads whatever branch was fetched first from that URL. The reverse also happens. A plugin first fetched or installed at a tag stays there on later fetches without a ref, as `update_plugin` shows in the next bullet.
- **The install store (`S08.installed-plugins`, `S08.ambient-plugins`).** Plugins are installed into the default store, which lives under the run's `OH_PERSISTENCE_DIR`:
  - a git plugin at tag `v1`
  - a local plugin

  The program then works through: list; a second install without `force`; `disable` (a new conversation drops that plugin's skill); a `force` reinstall; `enable`; `update_plugin` for a plugin installed from its default branch after a new commit; `update_plugin` of an unknown name; `uninstall` twice.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/store --arg plugins \
    --check store_under_persistence eq true --check install_name eq gitplug --check install_version eq 1.0.0 \
    --check install_requested_ref eq v1 --check install_resolved_is_v1 eq true --check install_path_in_store eq true \
    --check listed eq '["gitplug", "localplug"]' --check metadata_keys eq '["extensions"]' \
    --check loaded eq '["gitplug", "localplug"]' --check skills_enabled eq '["git-skill", "local-skill"]' \
    --check reinstall_error eq FileExistsError --check disable eq true --check disabled_flag eq false \
    --check loaded_after_disable eq '["gitplug"]' --check skills_disabled eq '["git-skill"]' \
    --check force_keeps_disabled eq true --check enable eq true --check enabled_flag eq true \
    --check track_update_version eq 2.0.0 --check track_update_resolved_is_new eq true \
    --check track_installed_file_version eq 2.0.0 --check update_missing eq null \
    --check uninstall eq true --check uninstall_again eq false --check dir_removed eq true \
    --check listed_end eq '["localplug"]' --check get_missing eq null
  ```
- **Updating a plugin that was installed at a tag (`S08.installed-plugins`).** This is the same program. `gitplug` was installed at `v1` and `main` has since moved to 2.0.0. `update_plugin("gitplug")` then runs; it re-fetches with `ref=None`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/store --arg plugins \
    --check update_requested_ref eq null --check update_version eq 2.0.0 \
    --check update_resolved_is_v2 eq true --check installed_file_version eq 2.0.0
  ```
  Expected: the plugin moves to the latest version, as the docstring says ("Update an installed extension to the latest version"). Known failure (reproduced 2026-10-13, not yet filed): it stays at 1.0.0 with v1's SHA. The installation record's `requested_ref` is cleared to `null` anyway, so the store now claims to track the default branch while it holds v1. This is the same single-branch cache as the previous known failure.
- **The generic installation manager and legacy metadata (`S08.installation-manager`).** These calls go to `InstallationManager` directly:
  - a manifest named `../escaped`
  - a non-kebab-case name
  - `uninstall("../store")`
  - a git install at a tag
  - a plugin directory copied in by hand
  - a directory whose name differs from its manifest name

  After that, a `.installed.json` with the legacy `plugins` and `skills` keys, and an `extensions` entry that conflicts with one of them, is read.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/store --arg manager \
    --check traversal_error contains "Invalid extension name" --check escaped_dir_exists eq false \
    --check caps_error eq ValueError --check uninstall_traversal_error eq ValueError \
    --check requested_ref eq v1 --check resolved_is_sha eq true \
    --check listed eq '["manual", "refd"]' --check manual_source eq local \
    --check manual_tracked_after_list eq true --check mismatch_adopted eq false
  control-sdk program S08/store --arg legacy \
    --check listed eq '["both", "old-plugin", "old-skillish"]' --check old_plugin_enabled eq false \
    --check both_source eq github:o/new-both --check loaded eq '["both", "old-skillish"]' \
    --check keys_after eq '["extensions"]'
  ```
  The `force` reinstall keeping the enabled flag, and `update` re-cloning, are checked in the install-store bullet above.
- **Marketplace manifests (`S08.marketplace-manifest`).** A `.plugin/marketplace.json` lists four plugin entries: a relative path, a `github` object, a `url` object and an absolute path. It also has a standalone skill and `metadata`. The program also loads a `.claude-plugin/` marketplace, a directory with no manifest, and a `github` entry with no `repo`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/market --arg manifest \
    --check name eq m-one --check owner eq "Verify Team" --check metadata_version eq 3.0.0 \
    --check plugins eq '["rel", "gh", "url", "abs"]' --check skills eq '["solo"]' --check path eq true \
    --check rel eq true --check gh eq '["github:owner/repo", "v2", "plugins/gh"]' \
    --check url eq '["https://git.example/x.git", "main", null]' --check abs eq '["/opt/plugins/abs", null, null]' \
    --check rel_entry_version eq 1.2.0 --check missing_entry eq null --check claude_plugin_dir_name eq m-two \
    --check no_manifest_error contains "Marketplace manifest not found" \
    --check bad_github_error contains "GitHub source requires 'repo' field"
  ```
- **The marketplace registry (`S08.marketplace-registry`).** The registry holds two local marketplaces that both list `shared`, and one git marketplace pinned to a tag.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/market --arg registry \
    --check duplicate_error eq "Duplicate marketplace registration: a" \
    --check alpha_at_a eq '[true, null, null]' --check beta_bare eq true --check shared_at_b eq true \
    --check ambiguous eq AmbiguousPluginError --check ambiguous_message contains "Use 'shared@<marketplace-name>'" \
    --check not_found eq PluginNotFoundError --check not_found_in_a eq PluginNotFoundError \
    --check unknown_market eq MarketplaceNotFoundError \
    --check list_all eq '["alpha", "beta", "shared", "shared"]' \
    --check cached_plugins_after_edit eq '["alpha", "shared"]' --check fresh_registry_plugins eq '[]' \
    --check git_resolved_ref_is_sha eq true --check git_plugin_source_in_cache eq true \
    --check repo_path_traversal_error eq ValueError
  ```
  The manifest is read once per registry. A registry that already exists keeps the old list after the file changes, while a new registry sees the edit.
- **Auto-loading marketplaces in a conversation (`S08.marketplace-auto-load`).** The conversation's `AgentContext` registers these marketplaces:
  - `broken`: cannot be fetched; `auto_load=True`
  - `m-all`: `auto_load=True`. It lists the plugins `alpha` and `dupper` (whose skill is `dup-skill`), and the standalone skills `solo` and `dup-skill`.
  - `m-some`: `auto_load=["beta", "picked"]`
  - `m-none`: `auto_load` left at its default

  The conversation also passes an explicit plugin named `alpha`. A second conversation registers only `m-none`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/market --arg auto-load \
    --check skills_before eq '[]' --check status eq finished \
    --check skills eq '["alpha-skill", "beta-skill", "dup-skill", "picked", "solo"]' \
    --check alpha_body eq ALPHA-EXPLICIT --check dup_body eq DUP-FROM-PLUGIN --check solo_body eq SOLO-STANDALONE \
    --check registered_only_skills eq '[]'
  ```
- **Loading a plugin into a running conversation (`S08.load-plugin-runtime`).** The marketplace `shop` lists `late`, a plugin with a skill, a `.mcp.json` server and a `PostToolUse` hook that logs `$OPENHANDS_TOOL_NAME`. The program does this:
  1. Runs one turn.
  2. Calls `conversation.load_plugin("late@shop")`.
  3. Runs a second turn, in which the scripted agent calls the new MCP tool `echo`.
  4. Asks for an unknown plugin.
  5. Calls `load_plugin` on a conversation with no `AgentContext`, and on one with an empty context.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/market --arg runtime \
    --check status eq finished --check skills_before eq '[]' --check skills_after eq '["late-skill"]' \
    --check hook_log_before eq '[]' --check terminal_same_object eq true --check resolved_plugins eq '["late"]' \
    --check tools_first_call eq '["finish", "terminal", "think"]' \
    --check tools_last_call contains echo --check tools_last_call contains invoke_skill \
    --check observations.2 contains "echo: loaded at runtime" \
    --check hook_log eq '["plugin-late:echo", "plugin-late:terminal", "plugin-late:finish"]' \
    --check unknown_plugin_error eq PluginNotFoundError \
    --check no_context_error contains "No agent context available" \
    --check no_market_error contains "No marketplaces registered"
  ```
  The existing `terminal` tool object survives the load. The plugin's MCP tool and `invoke_skill` are offered from the next LLM call on, and its hook runs only for later tool calls.
- **Marketplace import path (`S08.marketplace-import-path`).**
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S08/market --arg imports \
    --check plugin_Marketplace eq AttributeError --check plugin_MarketplaceRegistry eq AttributeError \
    --check types_MarketplaceOwner eq AttributeError --check types_MarketplacePluginEntry eq AttributeError \
    --check from_import eq ImportError \
    --check canonical eq '["Marketplace", "MarketplaceOwner", "MarketplaceRegistration", "MarketplaceRegistry"]'
  ```
- **Example 02: a GitHub plugin and the install store with a real model (`S08.conversation-attach`, `S08.resolved-refs`, `S08.fetch-cache`, `S08.installed-plugins`).** Live, and it needs network.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "Resolved plugin refs" -- python examples/05_skills_and_plugins/02_loading_plugins/main.py
  ```
  Demo 1 loads `github:anthropics/skills@main`, prints the resolved SHA, and runs one turn. Demos 2 to 6 install, disable, enable and uninstall plugins in a temporary store. A GitHub failure prints a warning and still exits 0, which is why the output check is there.
- **Agent Plugins package used by a model (`S08.agent-plugins-layout`, `S08.conversation-attach`).** Live.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t10_agent_plugins_skill
  ```
  The run reports `t10_agent_plugins_skill` as passed: the model invoked the package's skill and answered 6.472 m.
- **Example 04: the marketplace manifest (`S08.marketplace-manifest`).** No model call is made, so the block runs without a key.
  ```sh
  # no model
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk exec --expect-output "Skills defined: 2" -- python examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py
  ```

## Gotchas

- Plugins, ambient plugins, marketplaces and project skills are all resolved in `_ensure_plugins_loaded()`, on the first `send_message()` or `run()`, not in the `Conversation` constructor. A fetch error raises from that call (`PluginFetchError`), not from construction.
- Every `LocalConversation` merges the user's installed and `~/.agents/plugins` plugins, so a developer machine with installed plugins gives tests and examples extra skills, MCP servers and hooks. Point `OH_PERSISTENCE_DIR` and `HOME` at empty directories for clean runs.
- The `code-quality` example plugin's `.mcp.json` starts `uvx mcp-server-fetch` and its `PostToolUse` hook appends to `$OPENHANDS_PROJECT_DIR/.hook_log`; installing it into the real store makes every later conversation do both.
- Agent Plugins `mcp.json` servers are left literal: `${VAR}` secrets are not expanded there, only `${PLUGIN_ROOT}` and `${PLUGIN_DATA}`. Claude Code `.mcp.json` servers do get secret expansion.
- `resolved_plugins` is held on the `LocalConversation` object; the standalone SDK does not reuse it to pin plugin versions when a persisted conversation is reopened, so a branch ref is fetched again at its current head.
- Demo 1 and demo 3 of `02_loading_plugins/main.py` depend on `github:anthropics/skills@main` being reachable and unchanged in shape; both swallow `PluginFetchError`, so a network failure still exits 0.
- `MarketplaceRegistration.auto_load` defaults to `False`: registering a marketplace alone loads nothing until `load_plugin()` is called.
- Plugin MCP tool names depend on how many servers the agent has. With a single server the tools keep their bare names (`echo`). With two or more servers, each name gets the server's name as a prefix (`cc-plugin-mcp_getenv`, `tools_getenv`). The same plugin can therefore offer different tool names depending on what else is attached, and a scripted or prompted tool name has to match.
- A Claude Code `.mcp.json` is expanded once by `Plugin.load` (host environment included) and again with the conversation's secrets. A variable that exists in the agent process's environment wins over the secret of the same name (see the known failure under Driving it).
- The plugin git cache is one directory per URL, and a clone made for a ref is single-branch. Pinned and unpinned fetches of the same URL get in each other's way: a later ref that is not in the clone falls back to the cached content with a warning (`Using cached version.`), not an error.
- `MarketplaceRegistry` reads each manifest once and keeps it. A conversation builds a fresh registry for each `load_plugin()` call and at start, so a manifest edited on disk is seen by the next call.
