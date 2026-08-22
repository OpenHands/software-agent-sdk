# Per-call `llm_profile` on the task tool — design notes

**Branch:** `feat/task-tool-llm-profile` (off `origin/main` 4fe565663)
**Status:** implemented, post-panel revision applied, tests green, uncommitted
as of 2026-08-16

> Line anchors in this doc (`file.py:NNN`) refer to the pre-rebase tree; the
> branch was rebased onto upstream/main 007721b3d on 2026-08-16 and upstream
> churn shifted them.

> **Post-panel revision (2026-08-16):** a 7-voice review panel returned
> "ship after fixes." Applied: (1) cipher-aware profile resolution via a new
> public `LocalConversation.load_profile_llm` (the per-call path previously
> loaded with no cipher — ciphertext api_key under cipher-at-rest); (2)
> underscore-private cross-package imports eliminated (`_format_profiles`
> promoted to public `format_llm_profiles`; manager no longer touches
> `registry._get_profile_store`); (3) a pinned `model:` now skips per-call
> resolution entirely, so an unknown `llm_profile` under a pin no longer
> errors; (4) `Task.llm_profile` persisted so a bare resume keeps the
> creation-time profile instead of silently reverting to the parent model;
> (5) the tool description omits the profiles section when no profiles are
> saved; (6) the task-test conftest clears the registry's lru_cached profile
> store on setup and teardown.
>
> **Round-2 (7-of-8 SHIP):** the one requested code fix — a pinned definition
> now stores the *effective* profile (`None`) on the task, not the raw ignored
> request, so a bare resume after a later pin→inherit flip can't try to load a
> typo'd name. Remaining items were doc/wording nits; the proposed
> `vision_inspect._format_profiles` dedupe was rejected (its empty-list message
> and ordering differ from `format_llm_profiles`).

## Problem

Observed in OpenHands #proj-agent-canvas (2026-08-16, Rajiv Shah): prompting Agent
Canvas with a mixed-model workflow ("GPT-5.6 plans, DeepSeek V4 Pro does the
fixes") makes the main agent call `switch_llm` — switching *itself* to the second
model — instead of delegating to a subagent running that model. Two default
settings steer this: `enable_switch_llm_tool` defaults **on** (and its tool
description advertises every saved profile with "use this when another profile is
better suited for the next step"), while `enable_sub_agents` defaults **off**.

For sequential plan-then-fix work, self-switching is arguably fine. It breaks
down for the fan-out case: one conversation = one model at a time, so a planner
cannot hold its own model while N workers run on a cheaper one.

The only pre-existing mixed-model delegation path was authoring a subagent
definition `.md` with `model:` frontmatter — one file per agent-shape × model
combination, and nothing the parent can decide per-call from a prompt.

## What already existed (and is reused)

- `model:` frontmatter in subagent definitions resolves through
  `LLMProfileStore` (`~/.openhands/profiles/<name>.json`) inside the factory —
  `openhands-sdk/openhands/sdk/subagent/registry.py:218-228`.
- Built-in subagents (`code_explorer`, `bash_runner`, `web_researcher`,
  `default`) are all `model: inherit` — they take whatever LLM the factory is
  handed.
- `switch_llm` already lists saved profiles in its tool description
  (`get_llm_profile_names` / `format_llm_profiles` in
  `openhands-sdk/openhands/sdk/tool/builtins/switch_llm.py`).
- `LocalConversation` already owns a profile store + optional cipher
  (`local_conversation.py:413-414`) and decrypts on its own profile loads
  (`switch_profile`, `get_or_create_profile_llm`).

## The change

`TaskAction` gains one optional field, `llm_profile: str | None`
(`openhands-tools/openhands/tools/task/definition.py`). When set, the named
profile is loaded **through the parent conversation**
(`LocalConversation.load_profile_llm`, new public accessor — cipher-aware,
honors `definition.profile_store_dir`, no llm_registry registration, no
subscription transforms) and injected into the subagent factory **in place of**
the parent-LLM clone (`openhands-tools/openhands/tools/task/manager.py`,
`_get_sub_agent_from_factory`). Threading: `TaskExecutor.__call__` (impl.py) →
`TaskManager.start_task` → `_create_task` / `_resume_task` →
`_get_sub_agent_from_factory`.

The tool description gains a section listing saved profiles — only when at
least one profile exists (populated in `TaskToolSet.create` via the public
helpers imported from `switch_llm.py` — imported, not forked).

## Design decisions (the ones a reviewer should attack)

1. **Profile name, not model string.** The field can only name a file that
   exists in the profile store; it is validated against `store.list()` (inside
   `load_profile_llm`, listing from the same store it loads from) before
   `store.load()`. No path to arbitrary model strings. Strictly smaller
   capability than the default-on `switch_llm`, which moves the whole
   conversation.

2. **Pre-factory injection, not post-factory swap.** The factory derives the
   subagent's default condenser LLM from the LLM it is handed. Injecting before
   `factory_func` keeps the per-call override semantically identical to the
   `model:` pin path (worker condenses on its own model). A post-factory swap
   would leave the condenser on the parent model.

3. **Precedence: definition `model:` pin > per-call `llm_profile` > inherit.**
   A pinned definition skips per-call resolution entirely (checked before any
   store access), so the field is truly ignored under a pin — even an unknown
   name does not error, matching the field description. The *effective*
   profile is what gets stored on the task (`None` under a pin), so a bare
   resume can never try to load an ignored value if the definition is later
   flipped to `inherit`. All built-ins are `inherit`, so the feature works on
   built-ins out of the box.

4. **Unknown profile = loud error, never fallback.** `ValueError` raised before
   the task is registered in `self._tasks` (no half-created task leak);
   `TaskExecutor.__call__` converts it to an error `TaskObservation` naming the
   valid profiles — a retryable message to the parent model, not a crash. A
   silent inherit fallback would spend parent-model money while appearing to
   honor the override.

5. **Resume keeps the task's model.** The effective per-call profile is stored
   on `Task.llm_profile` at creation. A bare `resume=<id>` rebuilds the worker
   on that same profile; an explicit resume-time `llm_profile` switches the
   worker and becomes the task's new stored profile. (Pre-panel, a bare resume
   silently reverted to the parent model.)

6. **Field description is prompt engineering.** It disambiguates against
   `switch_llm` in the model's own reading ("This affects only the delegated
   subagent — your own model is unchanged (use the switch_llm tool to change
   your own model)") because the observed bug was self-switch instead of
   delegate.

7. **Profile resolution goes through the parent conversation, not the registry
   store.** `LocalConversation.load_profile_llm` decrypts secrets with the
   conversation's cipher (parity with `switch_profile`) and — deliberately —
   does NOT register the LLM in the parent's `llm_registry` or apply
   subscription transforms (that is what `get_or_create_profile_llm` does;
   using it would muddle per-task metrics attribution). A definition-level
   `profile_store_dir` is honored by constructing a store for that dir; the
   conversation cipher is passed at `load()` time regardless of which store is
   used (cipher is a deployment property, not a store property).

## Explicitly out of scope

- `tool_concurrency_limit` default (1) — parallel fan-out is a product decision.
- Built-in subagents' `model: inherit` — it is what makes them overridable.
- The older Delegate tool — separate schema, separate surface.
- UI surfaces (subagent definitions editor, profile picker at spawn).
- The `enable_switch_llm_tool`-on / `enable_sub_agents`-off default asymmetry —
  a settings/rollout conversation, not this diff.
- The definition-pin path's own cipher handling inside
  `registry.agent_definition_to_factory` (`store.load` without cipher,
  registry.py:228) — pre-existing upstream behavior, unchanged by this diff.
  Since this PR introduces the public cipher-aware accessor one floor up
  (`LocalConversation.load_profile_llm`), routing the pin path through it is
  now a small, obvious follow-up issue.
- Agent-server profile-store dir divergence (`OH_PERSISTENCE_DIR` vs SDK
  home-dir default) — pre-existing, affects `model:` pins equally today.
  (No agent-server changes: `TaskAction` is deserialized from the same
  dynamically-registered tool code server-side, so the new field flows through.)

## Metrics

Override LLM is freshly constructed by `load_profile_llm` + `reset_metrics()`
(parity with the clone path) and is never registered in the parent's
`llm_registry`. Parent attribution via `_update_parent_metrics`
(keyed `task:{id}`) is untouched; the parent's LLM object is never mutated.

## Tests (14 new; tests/tools/task 65 → 76; zero regressions)

943 passed across the commanded suites (`tests/tools/task` 76 +
`tests/tools/test_tool_name_consistency.py` 3 + `tests/sdk/subagent` +
`tests/sdk/tool/test_switch_llm.py` 125 combined + `tests/sdk/conversation`
739). The 14 new tests: 11 under `tests/tools/task/` (7 manager-level,
4 tool-set-level) + 3 in `tests/sdk/conversation/test_switch_model.py`.

- `tests/tools/task/test_task_manager.py::TestTaskManagerLLMProfile` — override
  applied + `stream is False`; unknown profile raises listing available
  profiles with no `_tasks` leak; pin fully ignores override (unknown name does
  not error, and the ignored value is not stored on the task); resume keeps
  original profile; explicit resume overrides and re-stores; inherit-created
  task keeps inheriting on bare resume.
- `tests/tools/task/test_task_tool_set.py` — sequential mixed-model pair
  (factories received exactly `["fast-model", "slow-model"]`); unknown profile →
  error observation naming the profile; description renders profile list when
  profiles exist; description omits the section when the store is empty.
- `tests/sdk/conversation/test_switch_model.py` — `load_profile_llm` decrypts
  with the conversation cipher and stays out of `llm_registry`; unknown profile
  lists available from the same store; `profile_store_dir` override resolves
  from the custom dir only.
- `tests/tools/task/conftest.py` — redirects the profile-store dir to tmp AND
  clears the registry's lru_cached store getter on setup/teardown (a cached
  store bound to a dead tmp dir must not leak across tests).
- Full `tests/tools`: terminal/tmux failures are pre-existing environment
  flakiness (clean tree fails the same directory as a superset).
- `pre-commit run --files <touched>`: all hooks pass (incl. pyright).

## Live verification (2026-08-16, real profiles on laptop: kimi/mimo/minimax)

Function-level: parent `minimax` (MiniMax-M3); `start_task(llm_profile="mimo")`
→ worker `mimo-v2.5-pro`, completed; no-override → worker == parent model;
unknown profile → clean `ValueError`, no task-state leak.

Agent-level (the real UX): live parent agent on MiniMax-M3, handed only
`TaskToolSet`, prompted Rajiv-style ("delegate to a general-purpose subagent …
run it on the mimo LLM profile, do not switch your own model"). Event stream
shows the parent **chose** to emit `"llm_profile": "mimo"`; worker ran on
`openai/mimo-v2.5-pro`; parent's model unchanged; synthesis returned to the
parent. Demo scripts: `/tmp/llm-profile-demo.py`, `/tmp/llm-profile-demo-agentic.py`.

## Known limitations

- Fan-out still sequential by default (`tool_concurrency_limit=1`).
- Profile list in the tool description is a creation-time snapshot, and it is
  built from the DEFAULT profile store; a definition with a custom
  `profile_store_dir` executes from that store, so the advertised list and the
  resolvable set can differ there.
- Old SDK versions reject new events containing the field (`extra="forbid"` —
  standard additive-field version-skew caveat; note in release notes).

## Maintainer-reception risk (stated honestly)

In the same Slack thread, Graham Neubig steered toward the conversation-spawn
path (the `agent-canvas-environment` skill, "delegate to a local conversation")
and said "we should make that easier." This PR is the *in-conversation* fix
instead. The answer to "why not the conversation path" is fan-out concurrency
and synthesis-in-parent — but a maintainer may still prefer the
conversation-level direction. This is the chief rejection risk and the main
thing the pre-public panel should attack.
