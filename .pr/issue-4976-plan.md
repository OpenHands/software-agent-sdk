# Implementation plan: OpenHands SDK #4976

Status: implemented as one SDK PR; final verification recorded in `verification.md`.

Issue: https://github.com/OpenHands/software-agent-sdk/issues/4976

Planning checkout: `9bc452ebf`; implementation rebased by fast-forward onto
`3f47426e6`. Branch: `refactor/4976-typed-llm-boundaries`.
Delivery: one SDK PR covering all of #4976, developed in small, testable commits.
The stages below are implementation checkpoints within that PR.

Suggested commit sequence:

1. Typed stream/event handling with the #4772 fix and regression tests.
2. Typed tokenizer adapters, optional loading, and fallback tests.
3. Provider message normalization and direct authentication access, with tests.
4. Final integration evidence and reviewer documentation.

Update the dynamic-access baseline alongside each relevant implementation
commit. Reproduce failures locally first, then commit regression tests together
with the implementation that makes them pass.

## Objective and observable result

Replace scattered inspection of provider objects with explicit types and small
normalization functions at the points where external data enters the SDK.

Today, core logic repeatedly checks whether objects expose fields or methods.
Afterward, core logic receives values with known fields and types. Supported
calls still return the same `LLMResponse`, `Message`, and integer token counts.
The known streaming bug is corrected: a wrapper's stale `completed_response=None`
cannot erase a completed response yielded during iteration.

The scope covers streaming and tokenizer access in `llm.py`, message conversion
in `message.py` and `mixins/non_native_fc.py`, and the authentication capability
lookup in `auth/openai.py`. Telemetry normalization, retry metadata, provider
routing, and unrelated SDK cleanup are outside this issue.

## Findings that shape the design

- LiteLLM 1.93.0 declares `reasoning_content`, `thinking_blocks`, and
  `provider_specific_fields`, but implementation testing showed that its
  constructor deletes absent fields. A small metadata projection with defaults
  is necessary; do not duplicate the entire provider message model.
- The SDK already accepts ordinary iterators and third-party sync/async
  generators. Restricting streams to LiteLLM's concrete wrapper classes would
  break existing behavior.
- Async calls can receive synchronous streams. The current implementation
  drains those streams in an executor. Preserve that behavior during this work;
  changes to buffering, callback timing, or cancellation design deserve separate
  treatment.
- Responses output can contain typed OpenAI objects, generic LiteLLM objects,
  dictionaries, and nested attribute objects in the existing fixtures. A strict
  replacement must not reject inputs that currently work.
- `create_subscription_llm_from_config()` already accepts an `LLM`, whose
  `auth_type` is declared. Direct field access should be sufficient here.
- PR #4772 is closed and unmerged. Its author explicitly left the fix available
  for continuation. Reference and credit that work when incorporating its
  regression coverage and completion-selection semantics.
- The dynamic-access baseline checks `getattr`, `setattr`, and
  `obj.__dict__.get`; it does not check `hasattr`. Passing that check alone does
  not demonstrate that the issue is solved.

## Design rules

1. Prefer existing provider types and normal `isinstance` narrowing. Use small
   private protocols only where a real capability is not captured by those types.
2. A protocol or `cast()` is not runtime validation. Inspect supported values at
   the external boundary before promising a stronger internal type.
3. Normalize only data that needs normalization. Do not build a generic provider
   registry, a universal adapter framework, or a duplicate public message schema.
4. Preserve completion response identity, token callback behavior, message
   content, tool-call identifiers, reasoning metadata, and existing fallbacks.
5. Keep Transformers optional and lazily loaded. Avoid new required dependencies,
   package version changes, and lockfile churn.
6. Do not replace forbidden calls with reflection disguised through `Any`,
   `__dict__`, broad exception handling, or unchecked casts.

## Stage 1: Establish the behavior contract

Read the complete path from `responses()`/`aresponses()` through stream handling,
`_finalize_stream_response()`, and `Message.from_llm_responses_output()`. Also
inspect `_transport_call()` and `_atransport_call()` for Chat Completions.

Record which shapes and fallbacks are supported in the implementation and tests.
Use this inventory to decide where direct access is sufficient and where a
private adapter is needed. Check the linked issue and upstream changes again
before implementation so the work does not duplicate a newly landed fix.

Port or adapt #4772's sync/async regression cases, with attribution. Demonstrate
that the stale-`None` case fails before the change. Use `num_retries=0` in the
minimal reproductions so retry delays do not obscure the failure.

Exit condition: a concrete list of preserved behaviors and a deterministic
reproduction of the completion bug, without external model calls.

## Stage 2: Type streams and events

Expected files: `llm/llm.py`, a focused private helper under `llm/` if needed,
and the existing streaming tests under `tests/sdk/llm/`.

- Express supported sync/async stream contracts with typed iterables. Model
  optional wrapper completion state separately so plain generators still work.
- Narrow response event variants using the installed provider event types.
  Preserve generic event shapes already supported at the input boundary.
- Centralize completion selection so sync and async paths apply the same rule:
  a non-null completion exposed by the wrapper after draining takes precedence;
  otherwise retain the valid event observed while draining. Preserve existing
  initial wrapper completion support. If no completion exists, raise the existing
  `LLMNoResponseError`.
- Preserve callback dispatch, output-item collection, and the fallback that fills
  an empty final response from collected output items.
- Preserve each output item's ID on delta chunks; changing it can cause downstream
  streaming code to mistake a chunk for a retry.
- Apply typed iterable selection to the Chat Completions transport paths too,
  including an async call receiving a sync generator.
- Preserve exception and cancellation propagation and existing resource ownership.

Exit condition: the #4772 regressions pass; sync/async generator compatibility,
callbacks, output reconstruction, and non-streaming responses still work.

## Stage 3: Type tokenizers and optional loading

Expected files: tokenizer helpers extracted from `llm/llm.py` into a focused
private module as useful; `tests/sdk/llm/test_llm.py` and focused new cases.

- Describe the minimal capabilities actually consumed: chat-template application
  and encoding. Represent the custom tokenizer configuration accurately too.
- Isolate optional Transformers import/loading and tokenizer selection from the
  core LLM class. Missing module, missing capability, and load failures retain
  the existing fallback behavior.
- Normalize the supported output shapes into a token count: token ID sequences,
  nested sequences, mappings containing `input_ids`, encoding objects, tensor-like
  shapes, and strings requiring encoding. Check the current precedence between
  overlapping shapes before implementing it.
- Preserve the chat-template arguments, inclusion of tool schemas, and message
  conversion used for token counting. A template failure should still fall back
  to LiteLLM counting.
- Use real installed tokenizer types where appropriate and narrow capability
  protocols for optional types. Do not require Transformers or a tensor library
  merely to import the SDK.

Exit condition: representative shapes produce the same counts and optional
capability failures produce the same fallback result.

## Stage 4: Normalize provider messages and simplify authentication

Expected files: `llm/message.py`, `llm/mixins/non_native_fc.py`,
`llm/auth/openai.py`, and their corresponding existing tests.

- Normalize LiteLLM's optionally deleted metadata fields before direct access.
  Convert thinking blocks at one defined boundary before core message processing.
- Share only the normalization that is actually needed by both message conversion
  and non-native function calling. Preserve reasoning and provider metadata
  through tool-call conversion, including absent and empty-field semantics.
- Replace the generic `_get()` helper in Responses output parsing with typed
  normalization of supported message, function-call, and reasoning items. Cover
  both dictionaries and object representations, including nested items.
- Preserve text assembly, tool-call IDs, Responses item IDs, reasoning summaries,
  encrypted reasoning, signatures/redacted blocks, and existing handling of
  unrelated output items. Do not introduce stricter public validation as an
  accidental side effect of this refactor.
- Replace the `auth_type` probe with direct typed access after checking callers.
  Keep subscription refresh, credential handling, and API-key behavior intact.

Exit condition: the same supported provider inputs yield equivalent SDK messages
and authentication decisions; persisted-message compatibility tests still pass.

## Stage 5: Verification and baseline cleanup

Use focused behavior tests as each stage is completed. Assert returned content,
counts, callback events, and exceptions rather than implementation details.

| Area | Essential cases |
| --- | --- |
| Completion selection | Wrapper field missing, stale `None`, non-null wrapper completion, and no completion anywhere; sync and async paths |
| Stream behavior | Plain iterable, sync generator returned to async caller, async generator, stable chunk IDs, empty final output reconstruction, callback/no-callback paths |
| Messages | Typed/generic/dictionary inputs, nested parts, tool-call IDs, reasoning fields, thinking signatures/redacted blocks, serialization round trips |
| Tokenizers | Representative supported result shapes, missing Transformers, unavailable chat-template capability, loading failure, template failure, tools in template input |
| Authentication | API-key configuration and subscription configuration, including existing credential restoration behavior |

Keep tests proportional: extend existing parameterized fixtures instead of
creating a large cross-product of identical tests.

After each code change, update the baseline for removed calls and run the
required checks on the changed files. The script can discover all SDK files:

```bash
uv run python scripts/check_forbidden_dynamic_attributes.py --update-baseline
uv run python scripts/check_forbidden_dynamic_attributes.py --baseline-ref upstream/main
uv run pre-commit run --files <changed-files>
```

Inspect the baseline diff: it must contain removals only, and those removals
must correspond to this issue's changes. Audit the touched paths for residual
`hasattr` and reflection workarounds as well.

At completion, run the LLM suite and checker regressions, then the SDK suite and
repository checks required for the final patch:

```bash
uv run pytest tests/sdk/llm/ tests/cross/test_check_forbidden_dynamic_attributes.py
uv run pytest tests/sdk/
uv run pre-commit run --all-files
```

Provide one reproducible integration smoke run through the real SDK and installed
LiteLLM HTTP parsing/streaming code against a local deterministic HTTP/SSE server.
Exercise sync and async requests and verify emitted text plus final response.
This provides evidence beyond replacing provider calls with mocks, without a
paid model or credentials. Describe it accurately as a local transport check;
it does not validate a live provider service.

Validate optional imports in a real environment without Transformers and, in a
separate disposable environment, with Transformers using a small local tokenizer
fixture. If import/loading changes affect the packaged Agent Server, verify that
artifact as required by the repository's runtime-parity guidance. Record exact
commands, results, and remaining limitations.

## Stage 6: Make the contribution reviewable

- Use one SDK PR for the complete issue, following the commit sequence above.
  Each implementation commit should include its focused tests and baseline
  changes so reviewers can inspect the work incrementally.
- Include attribution to #4772. Explain the stale-`None` case and the preserved
  wrapper precedence in the PR description.
- Add `.pr/design.html` showing the actual before/after interfaces and the test
  evidence. Keep documentation tied to the final implementation.
- Plan a companion `OpenHands/docs` PR documenting the relevant supported SDK
  behavior. The SDK's `AGENTS.md` requests a corresponding documentation PR;
  keeping helpers private does not itself waive that instruction.
- Follow the repository PR template, link `Fixes #4976` only when the full issue
  is addressed, and keep the PR draft until ready. The human author supplies the
  `HUMAN:` section; AI assistance and evidence belong in the `AGENT:` section.
- Review the final diff for public API/schema changes, dependency churn,
  unrelated edits, swallowed errors, and reflection moved into another file.

## Baseline verification already performed

The following command passed on the unchanged checkout during planning:

```bash
uv run pytest \
  tests/sdk/llm/test_responses_parsing_and_kwargs.py \
  tests/sdk/llm/test_llm.py \
  tests/sdk/llm/test_subscription_mode.py \
  tests/cross/test_check_forbidden_dynamic_attributes.py -q
```

Result: **137 passed, 1 warning, 4.75 seconds**. The warning comes from a test
using an unmapped model for cost calculation. This is a targeted baseline, not
a full-suite pass or evidence that the new implementation works.

## Completion criteria

- All acceptance criteria on #4976 are covered by implementation and evidence.
- Core logic uses accurate typed fields/contracts for the scoped boundaries.
- The stale-wrapper bug is fixed without dropping third-party stream support.
- Existing supported messages, token counts, and fallbacks remain compatible.
- The dynamic-access baseline shrinks, with no new reflection allowances.
- Focused tests, the broader required checks, and integration smoke evidence pass.
- The final SDK contribution and required documentation are ready for review.
