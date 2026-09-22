---
name: custom-codereview-guide
description: Repository-specific review guidance for OpenHands/software-agent-sdk.
triggers:
- /codereview
---

# OpenHands/software-agent-sdk review guide

Apply this guide with the general code-review skill and the closest `AGENTS.md`
for every changed package. This file identifies the repository-specific checks
that should affect the review decision; it does not replace those sources.

## Review decision

Use **APPROVE** when the current PR head has no material correctness, security,
compatibility, or acceptance-criterion defect. Use **COMMENT** when there is a
material finding or when this guide explicitly requires human review. Do not use
**REQUEST_CHANGES**.

A material finding must identify the affected path, the concrete failure, and
the evidence in the current diff or repository. Suggestions, questions, naming
preferences, optional refactors, and requests for more tests without an
unverified behavior do not justify withholding approval. Do not leave them as
review comments.

Before deciding, read the linked issue and all existing PR reviews and comments,
then verify that each unresolved concern and acceptance criterion is addressed
by the current head. Check current-head CI; a prior run is not evidence for a
changed head.

## Repository ownership and data flow

This repository owns the Python SDK and Agent Server: agent and tool behavior,
conversations, workspaces, events, and the canonical REST/WebSocket API.
`clients/typescript/` mirrors that API for browser clients. Agent Canvas UI and
frontend state belong in `OpenHands/OpenHands`; reusable extensions belong in
`OpenHands/extensions`; automation scheduling, dispatch, and sandbox lifecycle
belong in `OpenHands/automation`.

The normal flow is SDK or Agent Server -> OpenAPI -> TypeScript client -> Canvas.
Backend behavior and endpoints must be defined in Python before being exposed by
generated or handwritten clients. If a change belongs to another repository,
recommend moving it rather than adding a second implementation here.

For a change that crosses layers, trace the value or operation through every
affected public entry point. Check factories, constructors, registries,
serialization, REST/WebSocket transport, the TypeScript client, and resume or
fork paths as applicable. A field added to one model or a method added to one
client is incomplete if another supported path drops, renames, or ignores it.

## Blocking architecture checkpoints

### Public and persisted compatibility

Treat changes to public Python symbols, REST/WebSocket contracts, defaults,
serialized events, persisted settings, and stored conversations as compatibility
changes. Follow the deprecation and versioning policies under
[`AGENTS.md` API compatibility pointers](../../AGENTS.md#api-compatibility-pointers)
and the relevant package `AGENTS.md`.

Verify old and new representations through the real load, migrate, and
round-trip path. Event fields handled by `extra="forbid"` need the repository's
permanent deprecated-field handling before old events can load. Persisted
settings changes must use the existing schema-version migration and golden
fixture machinery. Do not accept an ad hoc compatibility shim when an existing
deprecation or migration mechanism owns the transition.

### Conversation and resource lifecycle

Trace create, run, interrupt, close, resume, fork, and failure paths affected by
the change. Verify that:

- cancellation or stop operations terminate the underlying task or descendant
  process rather than only changing status;
- event loops, background tasks, connections, plugins, and subprocess trees are
  closed by their owner on success, failure, and cancellation;
- partial persistent state is rolled back when a later startup step fails;
- shared mutable conversation state uses its owning synchronization mechanism;
  `LocalConversation` access to `self._state` must hold `with self._state:`; and
- resumed or forked conversations preserve the same observable configuration
  and behavior as newly created conversations.

Do not require a speculative cleanup abstraction. Identify a resource that can
actually outlive its owner or a state transition that produces a wrong result.

### Secrets and authentication

Trace credentials through input, validation, serialization, persistence,
logging, and delivery to the consumer. Secret-bearing Pydantic fields must use
the helpers in `openhands.sdk.utils.pydantic_secrets`; do not accept custom
redaction sentinels or one-off serializers. Verify that secrets cannot appear in
logs, exceptions, command strings, persisted plaintext, or ordinary model dumps,
and that resume and plugin-loading paths apply the same policy as creation.

### Production runtime parity

Code that imports optional modules, installs dependencies, launches programs,
uses filesystem paths, or shells out must work in every affected supported
runtime, including the packaged Agent Server, Docker images, and relevant host
platforms. Check that the dependency or executable is present in the production
artifact and that paths and process cleanup do not rely on the developer
environment. A unit test with mocks is not sufficient evidence for a changed
packaging or installation path.

### Agent behavior and evaluation

For changes that can plausibly alter agent or benchmark behavior, assess whether
the linked issue, PR acceptance criteria, or required checks call for a specific
evaluation. This includes prompts, tool descriptions or execution, model
capability routing, the agent loop, planning, memory, condensation, terminal
I/O, and evaluation harnesses.

Missing optional eval evidence is not a code defect and does not by itself
justify a COMMENT decision. When the current head has no material finding,
APPROVE it and identify the eval risk in the review so the subsequently
requested human maintainer can choose the appropriate lightweight evaluation.
Use COMMENT when required eval evidence is missing or failing, or when available
evaluation evidence demonstrates a regression; name the concrete requirement or
failure.

For provider/model registries, preserve declared order and capability semantics;
do not infer behavior from unordered collections or provider-name heuristics when
the feature registry already owns it.

## Triggered checks

### Release PRs

For a release PR, inspect the latest PR-specific results and comments for **Run
tests**, **Run Examples Scripts**, and **Run Integration Tests**. Confirm that
each ran on the current head and that its comment agrees with its check result.
If any is missing, skipped, stale, ambiguous, or failing, COMMENT with the exact
missing validation and defer approval to a maintainer.

Package version changes belong in dedicated release PRs. Do not approve an
unrelated PR that changes a distributable package version.

### Dependency updates

The root `pyproject.toml` has a seven-day `tool.uv.exclude-newer` supply-chain
guardrail, but Dependabot can bypass it for this workspace. For a dependency
update, inspect the changed distribution's package-index upload time (the
`uv.lock` `upload-time` when available). Do not approve an artifact uploaded less
than seven days ago; state its package, version, and upload time and ask a
maintainer whether to wait or override the guardrail.

### Newly provisioned eval models

For a PR limited to adding a new eval model configuration, a live proxy response
of `Invalid model name` may reflect out-of-band provisioning lag. Do not treat
that response alone as a defect. Accept a successful integration-runner check,
a completed eval-monitor run, or explicit author confirmation that the model is
reachable. Parameter conflicts, invalid model configuration, regressions in
existing models, and test failures remain material findings.

### Runnable example directories

A runnable example directory under `examples/` uses `main.py`, is included in
`tests/examples/test_examples.py` when the examples workflow should run it, and
prints `EXAMPLE_COST: ...`. Do not apply this convention to support scripts that
are intentionally invoked by workflows.

## Avoid review noise

Do not comment on:

- minor style, naming, formatting, or optional refactors;
- clear code merely to request a comment or docstring;
- extra tests unless a concrete behavior or regression is unverified;
- good behavior that needs no change; or
- `.pr/` artifacts, which the repository cleanup workflow owns.

Do not assume that test-only, documentation, configuration, CI, or dependency
PRs are safe by category. Apply the relevant checkpoint, then approve promptly
when no material issue remains.
