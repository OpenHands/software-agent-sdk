# SDK #4976 validation results

Original execution on 2026-09-22 from branch
`refactor/4976-typed-llm-boundaries`.

## Generic stream-event fix and rerun — 2026-10-04

Python 3.12+ runtime Protocol checks missed output-item fields stored in
LiteLLM Pydantic extras. The stream adapter now projects generic LiteLLM
events into declared fields, preserving the original output item.

The permanent reconstruction test covers three event representations across
sync, async, and async-with-sync-stream requests. Before the fix, all six
generic-event cases failed; afterward all nine cases passed. The existing
matrix now uses real `GenericEvent` objects for its 240 reconstruction rows.

Fresh results:

- Strengthened deterministic matrix: **1,285 passed in 9.02s**.
- Responses tests plus matrix: **1,334 passed**.
- Real local HTTP/SSE checks: **12/12 passed**.
- Token counting with and without Transformers: **2/2 passed**.
- All-file pre-commit, dynamic-access baseline gate, and diff whitespace:
  **passed**.
- Packaged Agent Server: **rebuilt with the fix**; the rebuilt binary returned
  **HTTP 200** from `/health` and was stopped by the smoke harness.
- Full SDK suite: **6,467 passed, 1 failed, 9 skipped, 12 xfailed** in 312.82s.

The failure is the same unrelated model-feature registry test described below;
the full suite is not all-green. Live provider calls remain unvalidated because
no provider credentials were configured.

## Compatibility correction and rerun — 2026-10-03

A differential review subsequently found three compatibility regressions that
the original matrix did not cover. The original "0 defects" result below is
historical, not a claim that the initial patch covered every accepted input.

Minimal corrections:

- Ignore unknown Responses content kinds before validating text; preserve
  the existing handling of falsy text.
- Project only metadata consumed by each Chat conversion path, keeping
  passthrough provider metadata opaque.
- Stringify generic Responses item IDs while preserving validation of the
  canonical tool-call ID.

Eight permanent regression cases were added: four ignored/falsy text variants,
two metadata conversion paths, and two numeric item-ID/call-ID combinations.
Valid shapes remain covered by the existing matrix rather than duplicating
their Cartesian products in the permanent tests.

Current results:

- Full deterministic matrix: **1,285 passed in 5.81s**.
- Matrix plus affected test files: **1,354 passed**.
- Real local HTTP/SSE matrix: **12/12 passed**.
- Transformers absent and installed: **2/2 passed**; local token count **2**.
- All-file pre-commit and dynamic-access baseline gate: **passed**.
- Packaged Agent Server: **rebuilt successfully** with `make build-server`;
  the rebuilt binary returned **HTTP 200** from `/health` and was stopped by
  the smoke harness.
- Live provider rows: **skipped**; no OpenAI, Anthropic, or Gemini API key was
  configured.
- Full SDK suite: **6,460 passed, 1 failed, 9 skipped, 12 xfailed** in 267.38s.

The one full-suite failure is
`test_reasoning_effort_overrides_are_not_redundant`: LiteLLM now reports native
support for `gpt-5.2-codex`, making an existing registry override redundant.
It reproduces in isolation. Both the test and `utils/model_features.py` are
identical to `upstream/main`; no model-feature changes were made by this fix.
This full-suite run is therefore not recorded as all-green.

The opaque-metadata regression intentionally exercises a list value accepted
by LiteLLM at runtime despite its dictionary annotation. LiteLLM emits a
serialization warning for that value; conversion succeeds and preserves it.

## Outcome

- Implementation defects found: **0**
- Deterministic matrix: **1,285 passed**
- Complete SDK regression suite: **6,453 passed, 9 skipped, 12 xfailed**
- Local real HTTP/SSE matrix: **12/12 passed**
- Optional Transformers environments: **2/2 passed**
- Source imports and packaged Agent Server `/health`: **passed**
- All-file pre-commit: **passed**
- Live provider calls: **skipped** because no OpenAI, Anthropic, or Gemini API
  credential was present

## Deterministic matrix breakdown

Command:

```text
uv run pytest .pr/test_validation_matrix.py -q -o addopts='--tb=short'
```

Result: **1,285 passed in 5.55s**.

| Area | Cases | Result |
| --- | ---: | --- |
| Responses core stream product | 800 | Passed |
| Responses boundary and cancellation rows | 11 | Passed |
| Chat stream/callback/sequence product | 15 | Passed |
| Chat provider/callback failures and cancellation | 10 | Passed |
| Explicit request routing | 16 | Passed |
| Reasoning normalization | 96 | Passed |
| Function-output normalization | 40 | Passed |
| Chat metadata | 60 | Passed |
| Tokenizer output/source/tool/message product | 168 | Passed |
| Text-output normalization | 20 | Passed |
| Mixed-output ordering and JSON round trip | 18 | Passed |
| Non-native function-call metadata | 24 | Passed |
| Authentication and defensive vendor handling | 7 | Passed |

The Responses boundary rows cover invalid and missing completions, malformed
stream containers, complete-response fallback, stable item IDs, output-item
ordering, provider and callback exceptions, async-stream cancellation, and
cancellation while a sync iterator drains in an executor.

## Real transport matrix

Command:

```text
uv run python .pr/local_transport_smoke.py
```

Result: **12/12 passed** through a real local HTTP server and LiteLLM's JSON/SSE
parsers:

- Responses, sync and async: non-streaming
- Responses, sync and async: populated final stream output
- Responses, sync and async: empty final output reconstructed from
  `output_item.done`
- Responses, sync and async: empty final output with no item-done event
- Chat Completions, sync and async: non-streaming
- Chat Completions, sync and async: streaming

## Optional dependency and packaging

Commands:

```text
uv run python .pr/tokenizer_smoke.py
uv run --isolated --frozen --with transformers python .pr/tokenizer_smoke.py
uv run python .pr/server_binary_smoke.py
```

Results:

- SDK imported and counted tokens with Transformers absent.
- An isolated Transformers 5.17.0 environment loaded a network-free local
  tokenizer and returned the exact expected count of 2.
- The new `_response_stream`, `_tokenizer`, and `_message_normalization`
  modules imported from the source install.
- The packaged one-file Agent Server started, returned HTTP 200 from `/health`,
  and shut down. The smoke timeout was raised from 25s to 75s because a cold
  macOS PyInstaller extraction can take about 45s before app startup.

## Project regression checks

Focused LLM/message/auth suite:

```text
uv run pytest -q -o addopts='--tb=short' \
  tests/sdk/llm/test_llm.py \
  tests/sdk/llm/test_llm_completion.py \
  tests/sdk/llm/test_message.py \
  tests/sdk/llm/test_message_from_chat_and_helpers.py \
  tests/sdk/llm/test_message_serialization.py \
  tests/sdk/llm/test_responses_parsing_and_kwargs.py \
  tests/sdk/llm/test_responses_serialization.py \
  tests/sdk/llm/auth/test_openai.py
```

Result: **246 passed**.

Complete SDK suite:

```text
uv run pytest tests/sdk -q -o addopts='--tb=short'
```

Result: **6,453 passed, 9 skipped, 12 xfailed, 65 warnings in 269.13s**.
The warnings are pre-existing suite warnings about optional network marks,
unmapped test-model pricing, and async cleanup; no test failed.

Quality gates:

```text
uv run pre-commit run --all-files
```

All configured checks passed: formatting, Ruff, pycodestyle, Pyright, forbidden
dynamic-attribute access, import boundaries, and Tool registration.

## Harness corrections made during execution

Three validation assumptions were corrected without changing product code:

1. Explicit empty reasoning content is preserved as `""`; it is not normalized
   to `None`.
2. Two response message items remain ordered in raw output and normalize to one
   SDK text block joined by a newline.
3. The packaged-server cold-start allowance needed to exceed PyInstaller's
   extraction time on macOS.

After those expectation/harness fixes, every deterministic and local transport
case passed. No production implementation change was required by the matrix.
