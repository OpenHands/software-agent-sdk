# Per-call `llm_profile` on the task tool

## Problem

The task tool can delegate work to a subagent, but an inherited subagent always
uses the parent conversation's LLM. A parent that wants a mixed-model workflow
must either switch its own model or rely on a static `model:` value in an agent
definition. Neither supports choosing a worker profile for an individual task
while the parent keeps its current model.

## User-facing behavior

`TaskAction` gains an optional `llm_profile` field naming a saved LLM profile.

The precedence is:

1. an agent definition with its own model profile;
2. the task call's `llm_profile`;
3. the parent model, inherited by default.

An agent definition's model is resolved before the per-call override. Therefore
an ignored override is never loaded or validated. For resumable tasks, a bare
resume retains the task's effective per-call profile; an explicit profile on a
resume replaces it. Resuming through an agent definition that supplies its own
model clears any previously stored per-call profile, so that ignored value
cannot affect a later resume.

Unknown profiles fail loudly and become an error `TaskObservation`. The task is
not partially registered and the worker never silently falls back to the parent
model.

## Shared profile-loading boundary

`LocalConversation.load_profile_llm()` is the common cipher-aware loading
primitive for conversation-owned profile operations. It selects the
conversation's default store or a caller-supplied profile directory, then calls
`LLMProfileStore.load(profile_name, cipher=self._cipher)`.

The operation only loads. It does not activate the returned LLM, add it to the
parent conversation's registry, or bind parent conversation context.

The existing higher-level methods add their own behavior:

- `switch_profile()` loads through the primitive, assigns the canonical
  `profile:<name>` usage ID, and activates the LLM;
- `get_or_create_profile_llm()` returns a registry hit or loads through the
  primitive, assigns the caller's usage ID, registers the LLM, and binds the
  conversation context;
- `TaskManager` loads through the primitive, resets metrics, and passes the LLM
  into the worker factory. The worker `LocalConversation` then owns registration
  and context binding.

Using `get_or_create_profile_llm()` directly for a worker would register an
otherwise unused template LLM in the parent conversation and give it the wrong
metrics/context owner. Adding a mode flag to that method would also make its
registry-oriented contract ambiguous.

`LLMProfileStore.load()` already performs persisted subscription restoration
through `LLM.from_persisted()`. The loading primitive therefore returns the
runtime subscription LLM while still avoiding parent registration and
activation.

The store's native error contract is preserved: a missing profile raises
`FileNotFoundError` with the available profile files, while invalid or corrupted
profiles raise `ValueError`. There is no separate list-before-load operation, so
validation and reading happen through the store's locked load path.

## Worker construction and metrics

The selected LLM is injected before `factory_func` runs. This matters because a
factory may derive a default condenser from its input LLM; swapping afterward
would leave the condenser on the parent model.

Subscription-backed LLMs are the exception: their worker condenser is disabled,
matching top-level agent creation and profile switching, because the separate
LLM completion used for summarization is unsupported on that path.

The loaded LLM gets a fresh metrics object before factory construction. It is
never registered in the parent's `llm_registry`. The worker conversation tracks
its own agent and condenser usage, and the existing task completion path copies
the worker's combined metrics into the parent under `task:<task-id>` exactly
once. The parent's active model is unchanged.

## Profile discovery and confidentiality

The task tool description lists saved profile names using the same public
`get_llm_profile_names()` and `format_llm_profiles()` helpers as `switch_llm`.
It exposes names only: profile model IDs, provider URLs, API keys, and persisted
JSON are not included. When no profiles exist, the section is omitted.

The list is a creation-time snapshot of the default profile store. A file-based
agent may specify a custom `profile_store_dir`, so its resolvable profile set can
differ from the advertised default list. This pre-existing multi-store discovery
limitation is not expanded into a new cross-store API in this change.

## Persistence and compatibility

`Task.llm_profile` stores the effective per-call profile for resume. An agent
definition that supplies its own model stores `None` because the request override
was ignored. Existing task calls omit the optional field and continue inheriting
the parent model.

The additive action field has the repository's normal version-skew caveat: an old
SDK that forbids unknown action fields cannot deserialize a new event containing
`llm_profile`.

## Verification matrix

Focused tests cover:

- two sequential tasks choosing different saved profiles;
- parent model unchanged and no worker profile entry in the parent registry;
- independent worker metrics and `task:<id>` merge-back;
- encrypted-secret loading through the conversation cipher;
- persisted subscription restoration;
- subscription workers do not receive an LLM-backed condenser;
- custom profile directories;
- native missing-profile errors with no partial task state;
- definition model precedence, including an invalid ignored override;
- bare-resume retention and explicit resume replacement;
- definition-owned models discard ignored profiles during resume;
- inherited behavior when no override is supplied;
- pre-factory injection and `stream=False` worker behavior;
- profile names present while model IDs, provider URLs, and API keys remain absent
  from the tool description;
- omission of the profile section when no profiles exist.

Current verification on the rebased branch:

- 235 task, subagent, profile-switch, and conversation-switch tests pass;
- pre-commit passes on every revised file, including Pyright;
- a live encrypted-profile delegation completed with a MiniMax M3 parent and a
  MiMo v2.5 Pro worker. The parent remained on MiniMax, its registry contained no
  worker profile entry, its only metrics key was `task:task_00000001`, and a bare
  resume retained the encrypted worker profile and MiMo model.

## Out of scope

- changing the `switch_llm` or subagent defaults;
- enabling parallel task execution by default;
- adding frontend worker-model reporting;
- changing definition-level profile loading;
- reconciling default-store advertising with every custom agent store;
- changing the conversation-spawn workflow.
