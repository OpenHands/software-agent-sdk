# Skills (AgentSkills, triggers, invoke_skill)

A Python program gives an agent extra instructions through `AgentContext(skills=[...])` or lets the SDK find them on disk. A skill is either a programmatic `Skill(name, content, trigger)`, a legacy Markdown file with optional frontmatter, a third-party rules file (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.cursorrules`), or an AgentSkills directory with a `SKILL.md` (plus optional `scripts/`, `references/`, `assets/`, `.mcp.json`). What the model sees depends on the kind: a legacy skill without a trigger is always in the system prompt (`<REPO_CONTEXT>`); a `KeywordTrigger` skill is appended to the user message the first time one of its keywords appears; a `PathTrigger` rule is appended to the observation the first time the agent touches a matching file; an AgentSkills skill is listed in `<available_skills>` and its body is fetched on demand through the built-in `invoke_skill` tool, which the agent gets automatically as soon as one such skill is loaded. Skills can also be loaded from the user home (`~/.agents/skills`), the project workspace (`.agents/skills`, repo root included), the public `OpenHands/extensions` repository, and an install store under `~/.openhands/skills/installed/` managed by `install_skill` / `enable_skill` / `uninstall_skill`. `disabled_skills` removes named skills from whatever was loaded.

Source: `openhands-sdk/openhands/sdk/skills/`, `openhands-sdk/openhands/sdk/context/agent_context.py`, `openhands-sdk/openhands/sdk/context/prompts/`, `openhands-sdk/openhands/sdk/tool/builtins/invoke_skill.py`, `openhands-sdk/openhands/sdk/agent/base.py`, `openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py`, `openhands-sdk/openhands/sdk/conversation/state.py`

Needs: `llm` (examples and real-LLM test), `git`, `network` (public skills, GitHub skill sources)

Examples: `examples/01_standalone_sdk/03_activate_skill.py`, `examples/05_skills_and_plugins/01_loading_agentskills/main.py`, `examples/05_skills_and_plugins/03_managing_installed_skills/main.py`, `examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py`

## Sub-features

- `S07.repo-skill-always-on`: a skill with `trigger=None` in legacy format (programmatic, plain `.md`, or third-party file) is rendered into the system prompt's repo context on every turn. Unit: `tests/sdk/context/test_agent_context.py` (`test_get_system_message_suffix_with_repo_skills`, `test_mixed_skill_types`). Example: `examples/01_standalone_sdk/03_activate_skill.py`. Real-LLM: none.
- `S07.keyword-trigger`: a `KeywordTrigger` skill is injected into the user message (`skill_knowledge_info.j2`) when a keyword matches as a whole word, case-insensitively; the first matching keyword in list order is reported. Unit: `tests/sdk/context/test_agent_context.py` (`test_get_user_message_suffix_with_single_trigger`, `test_case_insensitive_trigger_matching`), `tests/sdk/skills/test_skill_utils.py::test_match_trigger_whole_word`. Example: `examples/01_standalone_sdk/03_activate_skill.py` (`flarglebargle`). Real-LLM: none.
- `S07.trigger-once`: a triggered skill is recorded in `state.activated_knowledge_skills` and is not injected again later in the same conversation; a message with no match stays plain. Unit: `tests/sdk/conversation/local/test_conversation_send_message.py` (`test_send_message_activates_skill_on_trigger_match`, `test_send_message_skips_already_activated_skill`, `test_send_message_without_trigger_match_leaves_event_plain`). Example: none. Real-LLM: none.
- `S07.task-trigger`: a `TaskTrigger` skill with `inputs` gets a prompt appended asking the user for missing `${variables}`. Unit: `tests/sdk/skills/test_task_skill.py`, `tests/sdk/skills/test_skill_info.py`. Example: none. Real-LLM: none.
- `S07.message-suffixes`: `system_message_suffix` is appended to the system prompt and `user_message_suffix` to every user message, also when no skill triggers. Unit: `tests/sdk/context/test_agent_context.py` (`test_get_system_message_suffix_with_custom_suffix`, `test_get_user_message_suffix_empty_query_with_suffix`). Example: `examples/01_standalone_sdk/03_activate_skill.py`. Real-LLM: none.
- `S07.agentskills-load`: `load_skills_from_dir` returns three dicts (repo, knowledge, AgentSkills); a `SKILL.md` directory yields one skill with the standard fields (`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`), other Markdown inside it is treated as reference material, and the name must match the directory and the AgentSkills name rules. Unit: `tests/sdk/skills/test_skill_md_convention.py`, `tests/sdk/skills/test_agentskills_fields.py`, `tests/sdk/skills/test_skill_utils.py`, `tests/sdk/skills/test_skill_no_header.py`. Example: `examples/05_skills_and_plugins/01_loading_agentskills/main.py` (part 1). Real-LLM: none.
- `S07.resources`: `discover_skill_resources(dir)` and `Skill.resources` list files under `scripts/`, `references/`, `assets/` with the skill root. Unit: `tests/sdk/skills/test_resource_directories.py`. Example: `examples/05_skills_and_plugins/01_loading_agentskills/main.py`. Real-LLM: none.
- `S07.available-skills-prompt`: AgentSkills skills (and triggered legacy skills) are listed in `<available_skills>` with name and description (XML-escaped, descriptions over 1024 chars truncated with a pointer to `invoke_skill`); the skill body is not in the prompt. Unit: `tests/sdk/skills/test_validation_prompt.py`, `tests/sdk/skills/test_validation_improvements.py`, `tests/sdk/context/test_agent_context.py::TestAgentContext::test_agentskills_format_progressive_disclosure`, `tests/sdk/context/prompts/test_default_registry.py::test_available_skills_section`. Example: `examples/05_skills_and_plugins/01_loading_agentskills/main.py`. Real-LLM: `tests/integration/tests/t09_invoke_skill.py`.
- `S07.agentskills-trigger`: an AgentSkills skill with `triggers:` in its frontmatter is both listed and auto-injected on a keyword match. Unit: `tests/sdk/context/test_agent_context.py::TestAgentContext::test_agentskills_format_progressive_disclosure`. Example: `examples/05_skills_and_plugins/01_loading_agentskills/main.py` (`encrypt`). Real-LLM: none.
- `S07.invoke-skill-autoattach`: the `invoke_skill` tool is attached to the agent iff some loaded AgentSkills skill does not set `disable_model_invocation`, whatever `include_default_tools` says. Unit: `tests/sdk/tool/test_invoke_skill.py::test_agent_auto_attaches_invoke_skill_tool`. Example: `examples/05_skills_and_plugins/01_loading_agentskills/main.py`. Real-LLM: `tests/integration/tests/t09_invoke_skill.py`.
- `S07.invoke-skill-run`: `invoke_skill(name)` trims the name, renders the skill body (including inline commands), appends a footer with the skill's path (relative inside the workspace, absolute outside, none for programmatic skills), records the name once in `state.invoked_skills`, and returns an error observation without state change for an unknown name. Unit: `tests/sdk/tool/test_invoke_skill.py`. Example: none. Real-LLM: `tests/integration/tests/t09_invoke_skill.py`.
- `S07.disable-model-invocation`: `disable_model_invocation: true` hides a skill from `<available_skills>`, makes `invoke_skill` refuse it, and keeps its triggers working. Unit: `tests/sdk/context/test_agent_context.py::TestAgentContext::test_disable_model_invocation_hides_skill_but_preserves_triggers`, `tests/sdk/tool/test_invoke_skill.py::test_disable_model_invocation_rejects_direct_invocation`. Example: none. Real-LLM: none.
- `S07.inline-commands`: `` !`cmd` `` in skill content runs in the workspace at render time (10 s timeout, 50 KB cap) and is replaced by its output; fenced and inline code, unclosed fences and `` \!`cmd` `` stay literal. Unit: `tests/sdk/skills/test_skill_commands.py`. Example: none. Real-LLM: none.
- `S07.path-rules`: a skill with `paths:` frontmatter (a `PathTrigger` rule), and every nested `AGENTS.md`-style file below the workspace, is kept out of the catalog and the repo context and is appended once to the observation of the first tool call that touches a matching file inside the workspace. Unit: `tests/sdk/skills/test_path_trigger.py`, `tests/sdk/conversation/test_path_rules_injection.py`. Example: none. Real-LLM: none.
- `S07.project-skills`: `load_project_skills(work_dir)` reads third-party files, then `.agents/skills`, `.openhands/skills`, `.openhands/microagents` in that order (first wins), for the work dir and then the enclosing git root; one bad skill does not stop the others. Unit: `tests/sdk/skills/test_load_project_skills.py`. Example: none. Real-LLM: none.
- `S07.load-project-flag`: `AgentContext(load_project_skills=True)` is resolved by `LocalConversation` on the first `send_message()`/`run()` from `workspace.working_dir`; project skills override same-named explicit skills, `disabled_skills` still applies, and a load failure does not stop the conversation. Unit: `tests/sdk/conversation/test_repo_root_project_skills.py`. Example: none. Real-LLM: none.
- `S07.user-skills`: `AgentContext(load_user_skills=True)` merges `~/.agents/skills`, `$OH_PERSISTENCE_DIR`-or-`~/.openhands` `skills/` and `microagents/`, then enabled installed skills; explicit skills win on a name conflict. Unit: `tests/sdk/skills/test_load_user_skills.py`. Example: none. Real-LLM: none.
- `S07.public-skills`: `AgentContext(load_public_skills=True)` (or `load_public_skills()`) clones `https://github.com/OpenHands/extensions` into the skills cache, keeps only skills listed in `marketplace_path` (default `marketplaces/default.json`, `None` for all), caches the result in-process (60 s for a branch, forever for a tag or SHA), honors `EXTENSIONS_REF`, and falls back to the cached clone when the update fails. Unit: `tests/sdk/skills/test_load_public_skills.py`, `tests/sdk/skills/test_extensions_ref.py`. Example: `examples/01_standalone_sdk/03_activate_skill.py`. Real-LLM: none.
- `S07.merge-precedence`: `load_available_skills` layers public, then user, then project (later wins); `merge_skills_by_name` keeps the primary list's order; duplicate names inside `AgentContext.skills` raise. Unit: `tests/sdk/context/test_agent_context.py::TestAgentContext::test_skill_validation_duplicate_names`, `tests/sdk/conversation/test_repo_root_project_skills.py::test_load_project_skills_flag_merges_with_project_precedence`. Example: none. Real-LLM: none.
- `S07.disabled-skills`: `AgentContext(disabled_skills=[...])` drops those names after every source is loaded; an absent name is a no-op. Unit: `tests/sdk/context/test_agent_context.py::TestAgentContext::test_disabled_skills_drops_named_skill`, `tests/sdk/conversation/test_repo_root_project_skills.py::test_disabled_skills_drops_project_skill_at_lazy_merge`. Example: none. Real-LLM: none.
- `S07.skill-mcp-json`: an AgentSkills directory's `.mcp.json` (legacy skills: frontmatter `mcp_tools`) is parsed into `Skill.mcp_tools`, with `${VAR}` expanded from secrets, then env, then defaults; invalid JSON raises. Unit: `tests/sdk/skills/test_mcp_json.py`, `tests/sdk/skills/test_mcp_config_expansion.py`. Example: none. Real-LLM: none.
- `S07.installed-lifecycle`: `install_skill(source)` copies a skill into the install store and records it in `.installed.json`; `list_installed_skills`, `load_installed_skills` (enabled only), `disable_skill`/`enable_skill`, `update_skill` and `uninstall_skill` act on that store; an existing name needs `force=True`. Unit: `tests/sdk/skills/test_installed_skills.py`. Example: `examples/05_skills_and_plugins/03_managing_installed_skills/main.py`. Real-LLM: none.
- `S07.marketplace-install`: `install_skills_from_marketplace(dir)` installs the standalone skills and plugin skills a `marketplace.json` lists, from local paths and GitHub `blob` URLs, skipping a missing source. Unit: `tests/sdk/skills/test_installed_skills.py::TestInstallSkillsFromMarketplace`. Example: `examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py`. Real-LLM: none.
- `S07.serialization`: skills of every trigger type round-trip through JSON, and `mcp_tools` secrets are redacted unless a cipher is given. Unit: `tests/sdk/skills/test_skill_serialization.py`, `tests/sdk/context/test_agent_context_serialization.py`. Example: none. Real-LLM: none.

## How to get to it (agent POV)

- `AgentContext(skills=[Skill(name=..., content=..., trigger=None | KeywordTrigger(keywords=[...]) | TaskTrigger(triggers=[...]))], system_message_suffix=..., user_message_suffix=...)` passed as `Agent(agent_context=...)`. `Skill`, `KeywordTrigger`, `TaskTrigger`, `PathTrigger` import from `openhands.sdk.skills` (also re-exported by `openhands.sdk.context`). Covers `S07.repo-skill-always-on`, `S07.keyword-trigger`, `S07.task-trigger`, `S07.message-suffixes`, `S07.trigger-once`.
- Loader functions in `openhands.sdk.skills`: `load_skills_from_dir(dir)`, `Skill.load(path)`, `load_project_skills(work_dir)`, `load_user_skills()`, `load_public_skills(repo_url=..., ref=..., marketplace_path=...)`, `load_available_skills(work_dir, include_user=, include_project=, include_public=)`, `merge_skills_by_name`, `discover_skill_resources`, `validate_skill_name`, `to_prompt(skills)`. Covers `S07.agentskills-load`, `S07.resources`, `S07.project-skills`, `S07.user-skills`, `S07.public-skills`, `S07.merge-precedence`.
- `AgentContext` flags: `load_user_skills`, `load_public_skills`, `marketplace_path`, `load_project_skills` (lazy, resolved by `LocalConversation`), `disabled_skills`. Covers `S07.load-project-flag`, `S07.disabled-skills`.
- Files on disk: `<workspace>/.agents/skills/<name>/SKILL.md` (preferred), `<workspace>/.openhands/skills/*.md`, `<workspace>/.openhands/microagents/` (deprecated), `<workspace>/AGENTS.md` / `CLAUDE.md` / `GEMINI.md` / `.cursorrules`, nested `<workspace>/<sub>/AGENTS.md` (path rules), `~/.agents/skills/`, `~/.openhands/skills/`, `~/.openhands/skills/installed/.installed.json`, `~/.openhands/cache/skills/` (public clone; the `load_public_skills` docstring still says `skills-cache`).
- Frontmatter keys: `name`, `description`, `triggers`, `paths`, `inputs`, `license`, `compatibility`, `metadata`, `allowed-tools`, `disable-model-invocation` / `disable_model_invocation`, `mcp_tools` (legacy only).
- Built-in tool: `invoke_skill` (`InvokeSkillTool`, `openhands.sdk.tool.builtins`), auto-attached; state fields `ConversationState.invoked_skills`, `activated_knowledge_skills`, `activated_path_rules`. Covers `S07.invoke-skill-*`, `S07.path-rules`.
- Install store API: `install_skill(source, ref=None, installed_dir=None, force=False)`, `uninstall_skill`, `enable_skill`, `disable_skill`, `list_installed_skills`, `load_installed_skills`, `get_installed_skill`, `update_skill`, `install_skills_from_marketplace(marketplace_dir, force=)`. Covers `S07.installed-lifecycle`, `S07.marketplace-install`.
- Env vars: `EXTENSIONS_REF` (public skills ref, read at import), `OH_PERSISTENCE_DIR` (replaces `~/.openhands`, must be set before `openhands.sdk` is imported), `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` for the examples.

## Driving it

Preconditions:
- `uv sync --dev` has run; `control-sdk` is on `PATH` (`export PATH="$PWD/.agents/skills/verify-sdk/scripts:$PATH"`). `sdk-map run` does both.
- `git` and `tmux` on `PATH`.
- The public-skills and remote marketplace bullets reach `github.com` (network); live bullets need `DEEPSEEK_API_KEY`.

Each run has its own `HOME` and `OH_PERSISTENCE_DIR`, so user skills, the install store and the skills cache start empty. Programs in `programs/S07/` record what every scripted LLM call was sent (`recorder.py`).

- **Always-on, keyword and task skills in a conversation (`S07.repo-skill-always-on`, `S07.keyword-trigger`, `S07.trigger-once`, `S07.task-trigger`, `S07.message-suffixes`, `S07.serialization`).** Three always-on skills (programmatic, a plain `.md` and an `AGENTS.md` loaded with `Skill.load`), a keyword skill on `rot13`/`cipher`, a task skill on `/deploy` with an input, both suffixes; four scripted turns: "ciphertext" (no whole-word match), "CIPHER, maybe ROT13", "rot13 again", "/deploy".
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/triggers \
    --check llm_calls eq 4 --check loaded_names eq '["inline-rules", "style", "agents", "crypto", "deploy"]' \
    --check always_on_every_call eq true --check always_on_in_static eq false --check sys_suffix_every_call eq true \
    --check turn1_injected eq false --check turn2_injected eq true --check turn2_reported_keyword eq true \
    --check turn3_injected eq false --check turn4_task_injected eq true --check turn4_asks_for_inputs eq true \
    --check user_suffix_every_turn eq true --check activated eq '["crypto", "deploy"]' \
    --check roundtrip_equal eq true --check trigger_types eq '[null, null, null, "keyword", "task"]'
  ```
  The keyword skill is injected once, reporting `rot13` (first in its keyword list) although `CIPHER` appears first in the message.
- **AgentSkills directories, `invoke_skill` and inline commands (`S07.agentskills-load`, `S07.resources`, `S07.available-skills-prompt`, `S07.agentskills-trigger`, `S07.invoke-skill-autoattach`, `S07.invoke-skill-run`, `S07.disable-model-invocation`, `S07.inline-commands`, `S07.skill-mcp-json`, `S07.serialization`).** `<workspace>/.agents/skills/` holds `rot13` (all frontmatter fields, `triggers: [encrypt]`, `scripts/`, `references/`, `assets/`, `.mcp.json`, inline commands), `hidden` (`disable-model-invocation`, `triggers: [sesame]`), `longdesc` (a 1500-character description) and `badname`; `~/.agents/skills/homeskill` is outside the workspace. The scripted agent calls `invoke_skill` for `"  rot13 "`, `nope`, `hidden`, `rot13`, `homeskill`; the user says "encrypt" and later "sesame".
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/agentskills \
    --check dict_sizes eq '[0, 0, 3]' --check agentskills eq '["hidden", "longdesc", "rot13"]' \
    --check fields.license eq MIT --check fields.compatibility eq "needs bash" \
    --check fields.metadata.author eq verify-sdk --check fields.allowed_tools eq '["terminal"]' \
    --check fields.is_agentskills_format eq true --check reference_md_is_skill eq false \
    --check name_errors len-eq 2 \
    --check resources.scripts eq '["encrypt.sh"]' --check resources.references eq '["guide.md"]' \
    --check resources.assets eq '["table.txt"]' --check resources.root_ok eq true \
    --check discovered_scripts eq '["encrypt.sh"]' \
    --check mcp_args.0 eq fallback --check mcp_args.1 matches '/\.agents/skills/rot13$' \
    --check mcp_env_token eq from-env --check mcp_env_token_with_secret eq from-secret \
    --check invalid_mcp_json_error eq SkillValidationError \
    --check secret_in_dump eq false --check secret_after_cipher eq true --check skill_json_roundtrip eq true \
    --check tools contains invoke_skill --check listed eq '["rot13", "longdesc", "homeskill"]' \
    --check description_escaped eq true --check body_in_prompt eq false \
    --check turn1_trigger_injected eq true --check turn2_hidden_injected eq true \
    --check obs_is_error eq '[false, true, true, false, false]' --check first_has_body eq true \
    --check inline_rendered eq true --check fenced_literal eq true --check escaped_literal eq true \
    --check footer_relative eq true --check footer_home_absolute eq true \
    --check unknown_error_lists eq true --check hidden_refused eq true --check invoked eq '["rot13", "homeskill"]' \
    --check tools_default_tools_off eq '["invoke_skill"]' --check tools_hidden_only not-contains invoke_skill \
    --check tools_legacy_only not-contains invoke_skill
  ```
- **Long descriptions in `<available_skills>` (`S07.available-skills-prompt`).** The same layout; the `longdesc` entry the model sees.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/agentskills \
    --check longdesc_full_in_prompt eq false --check longdesc_points_to_invoke_skill eq true \
    --check longdesc_leaks_location eq false
  ```
  Expected: the description is cut at 1024 characters with `Call invoke_skill(name="longdesc") to load the full skill`, and no file location is shown (`to_prompt` omits `<location>` on purpose). Known failure (reproduced 2026-10-13, not yet filed): `Skill` truncates the description when the `SKILL.md` is loaded, with the notice `<response clipped><NOTE>... You can view the complete skill content at /abs/path/.agents/skills/longdesc/SKILL.md.</NOTE>`, so the prompt carries the absolute file path and no `invoke_skill` pointer (`longdesc_points_to_invoke_skill` false, `longdesc_leaks_location` true). The unit test `test_to_prompt_truncates_long_descriptions` sets the description after construction and so never sees the load-time notice.
- **Path rules (`S07.path-rules`).** A `paths: [src/**/*.py]` AgentSkills rule and a nested `docs/AGENTS.md`; the scripted agent views `README.md`, `src/app/main.py`, `src/app/other.py` and `docs/guide.md` with `file_editor`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/path_rules \
    --check tool_results eq 4 --check readme_has_rule eq false --check main_has_py_rule eq true \
    --check other_has_py_rule eq false --check guide_has_docs_rule eq true \
    --check rules_in_system len-eq 0 --check root_rule_in_system eq true \
    --check activated_path_rules eq '["py-rules", "agents:docs"]'
  ```
- **Project, user and installed sources and their precedence (`S07.project-skills`, `S07.load-project-flag`, `S07.user-skills`, `S07.merge-precedence`, `S07.disabled-skills`).** A git repo with skills at its root and in `pkg/` (the name `dup` in `.agents/skills`, `.openhands/skills` and `.openhands/microagents`, plus a skill with an invalid name), user skills in `~/.agents/skills`, `$OH_PERSISTENCE_DIR/skills` and `microagents`, and two installed skills (one disabled).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/sources \
    --check project_names eq '["agents", "claude", "dup", "layered", "only-micro", "only-oh", "shared"]' \
    --check dup_content eq DUP-FROM-AGENTS \
    --check user_names eq '["layered", "u-agents", "u-installed", "u-micro", "u-oh"]' \
    --check layered_content eq PROJECT-LAYER \
    --check merged eq '["b:primary-b", "a:primary-a", "c:secondary-c"]' \
    --check flag_before_first_message eq '["dup", "keep"]' \
    --check flag_after eq '["agents", "claude", "dup", "keep", "layered", "only-micro", "shared"]' \
    --check flag_dup eq DUP-FROM-AGENTS --check flag_status eq finished \
    --check flag_system_has_project eq true --check flag_system_has_explicit_dup eq false \
    --check user_flag_names eq '["layered", "u-agents", "u-installed", "u-oh"]' \
    --check user_flag_u_oh eq EXPLICIT-U-OH --check duplicate_error eq ValidationError \
    --check no_flags_names len-eq 0
  ```
  `agents` (repo root `AGENTS.md`) and `shared` come from the git root; the broken skill is skipped without stopping the rest; project skills replace the explicit `dup` only once the first message is sent; `disabled_skills` drops `only-oh` and `u-micro` and ignores a name that never loaded.
- **Install store and marketplace installs (`S07.installed-lifecycle`, `S07.marketplace-install`).** A local skill through install, reinstall, disable, forced reinstall, enable, update and uninstall; then a local `marketplace.json` with a standalone skill, a plugin with two skills and a missing source, installed twice.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/installed \
    --check installed_name eq greeter --check store_file eq true --check reinstall_error eq FileExistsError \
    --check enabled_after_disable len-eq 0 --check listed_after_disable eq '["greeter"]' \
    --check enabled_after_force_reinstall len-eq 0 --check enabled_after_enable eq '["greeter"]' \
    --check content_after_update eq GREETER-V2 --check get_installed eq true \
    --check uninstall eq true --check uninstall_again eq false --check after_uninstall len-eq 0 \
    --check dir_after_uninstall eq false \
    --check marketplace_installed eq '["plug-one", "plug-two", "standalone"]' --check again_without_force len-eq 0
  ```
- **Marketplace install from a GitHub blob URL (`S07.marketplace-install`).** Network: the same marketplace plus `https://github.com/OpenHands/extensions/blob/main/skills/github`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/installed --arg remote --check after_marketplace eq '["github", "plug-one", "plug-two", "standalone"]'
  ```
- **Public skills from a local extensions-shaped repository (`S07.public-skills`).** `load_public_skills(repo_url=<local git repo>)`: the default marketplace filter, no filter, another marketplace, the in-process cache within the branch TTL, a tag, then a fresh process after the repository is moved away (falls back to the cached clone), then `EXTENSIONS_REF=v1`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/public --arg local \
    --check main_default eq '["alpha", "skills/gamma"]' --check main_all eq '["alpha", "beta", "skills/gamma"]' \
    --check main_small eq '["beta"]' --check cache_clone eq true \
    --check main_default_again eq '["alpha", "skills/gamma"]' --check tag_v1 eq '["alpha"]'
  control-sdk exec -- mv "$SDK_VERIFY_RUN/extensions" "$SDK_VERIFY_RUN/extensions.gone"
  control-sdk program S07/public --arg offline --check repo_present eq false --check main_default eq '["alpha", "skills/gamma"]'
  control-sdk exec -- mv "$SDK_VERIFY_RUN/extensions.gone" "$SDK_VERIFY_RUN/extensions"
  control-sdk program S07/public --arg env --env EXTENSIONS_REF=v1 --check ref eq v1 --check default_ref_load eq '["alpha"]'
  ```
- **Public skills from GitHub (`S07.public-skills`).** Network: `AgentContext(load_public_skills=True)` clones `OpenHands/extensions` into the run's skills cache.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --field run)
  control-sdk program S07/public --arg remote \
    --check public_count gt 10 --check has_github eq true --check cache_clone eq true \
    --check origin eq https://github.com/OpenHands/extensions
  ```
- **A real model uses an AgentSkills skill (`S07.available-skills-prompt`, `S07.invoke-skill-run`, `S07.invoke-skill-autoattach`).** Live: the task does not name the skill.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk program S07/live_invoke --check status eq finished --check invoked contains rot13 --check answer eq "uryyb jbeyq"
  ```
- **Example 3 (`S07.repo-skill-always-on`, `S07.keyword-trigger`, `S07.message-suffixes`, `S07.public-skills`).** Live; clones the public skills (network).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "flarglebargle" --expect-output "EXAMPLE_COST" -- python examples/01_standalone_sdk/03_activate_skill.py
  ```
- **AgentSkills example (`S07.agentskills-load`, `S07.resources`, `S07.agentskills-trigger`, `S07.invoke-skill-autoattach`).** Live; part 1 lists `rot13-encryption` and `code-style-guide` with their resources, part 2 asks to encrypt `hello world`.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env --expect-output "rot13-encryption" --expect-output "EXAMPLE_COST" -- python examples/05_skills_and_plugins/01_loading_agentskills/main.py
  ```
- **Installed-skills example (`S07.installed-lifecycle`).** Needs no key, but `sdk-map run` treats every example as model-backed.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --expect-output "DEMO 1: Installing local skills" -- python examples/05_skills_and_plugins/03_managing_installed_skills/main.py
  ```
- **Mixed marketplace example (`S07.marketplace-install`).** Network; `--install` writes to the run's install store (its `HOME` and `OH_PERSISTENCE_DIR` are private).
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --expect-output "greeting-helper" -- python examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py
  control-sdk exec --expect-output "greeting-helper" -- python examples/05_skills_and_plugins/04_mixed_marketplace_skills/main.py --install
  ```
- **The model picks `invoke_skill` (`S07.available-skills-prompt`, `S07.invoke-skill-run`).** Live, through the integration runner.
  ```sh
  export SDK_VERIFY_RUN=$(control-sdk new --llm deepseek-flash --field run)
  control-sdk exec --model-env -- python tests/integration/run_infer.py --llm-config '{"model": "deepseek/deepseek-flash"}' --eval-ids t09_invoke_skill
  ```

## Gotchas

- `load_project_skills=True` does nothing at `AgentContext` construction; project skills appear only after the conversation's first `send_message()` or `run()`. Reading `agent.agent_context.skills` before that shows no project skills.
- Without an explicit `AgentContext(load_project_skills=True)`, a standalone `Conversation` does not read `AGENTS.md` or `.agents/skills` at all.
- `load_user_skills` and `load_public_skills` default to `False` in the SDK; the example `03_activate_skill.py` turns public skills on and therefore needs network to clone `OpenHands/extensions`.
- `Skill.mcp_tools` is parsed and serialized, but `LocalConversation` does not merge it into `Agent.mcp_config`; only plugin `.mcp.json` servers (S08) and `Agent(mcp_config=...)` (S09) start MCP servers.
- `EXTENSIONS_REF` and `OH_PERSISTENCE_DIR` are read when `openhands.sdk.skills` is imported; setting them later in the same process has no effect on the cached constants.
- `examples/05_skills_and_plugins/03_managing_installed_skills/main.py` is not in `_TARGET_DIRECTORIES` of `tests/examples/test_examples.py`, so the `test-examples` label never runs it.
- `04_mixed_marketplace_skills/main.py --install` writes to the real user install store (no `installed_dir` override) and needs GitHub access; run it in a throwaway `HOME` or `OH_PERSISTENCE_DIR`.
- Inline `` !`cmd` `` execution runs shell commands with full process privileges whenever a skill is rendered by `invoke_skill`; only load trusted skills.
- The public-skills clone lives at the fixed path `<persistence>/cache/skills/public-skills` whatever `repo_url` is: after `load_public_skills(repo_url=<other repo>)`, a later default call (or `AgentContext(load_public_skills=True)`) in a new process updates that clone from its existing origin and returns the other repository's skills, not `OpenHands/extensions` (reproduced 2026-10-13 with `programs/S07/public.py`: `local` then `remote` in one run gives 3 skills and `origin` pointing at the local repo).
- Marketplace files in the public repository list skills as `plugins` entries (`load_marketplace_skill_names` reads `marketplace.plugins`); a `skills` list there is ignored.
- `tests/integration/run_infer.py` needs both `LLM_API_KEY` and `LLM_BASE_URL`; without either, the test instance raises `ValueError(... Skipping real LLM test.)` and is counted as an error, not a pass.
