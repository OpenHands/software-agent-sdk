# SDK #4976 validation matrix

Status: executed locally on 2026-09-22. See `validation-matrix-results.md` for
the commands, results, skips, and corrected harness assumptions. Keep these
artifacts local until the implementation is ready for review.

## 1. Responses streaming: exhaustive supported-state product

Generate every meaningful combination: **8 × 6 × 3 × 5 = 720 successful
completion cases**, plus **8 × 2 × 5 = 80 completion-error cases**. That is
**800 core cases**. The output axis does not apply when no valid final response
exists; multiplying it into the error cases would create 160 duplicate cases
with no distinct expected result.
Give each case a stable ID such as `RS/E2/C3/O1/D4`. Use a fresh LLM, stream,
callback recorder, and final response for each case; set retries to zero so a
failure cannot be hidden by another attempt. Use distinct response IDs and text
for yielded, initial-wrapper, and final-wrapper completions.

| Axis | States | Expected contract |
| --- | --- | --- |
| `E`: caller, provider iterator, callback | `E1` sync call + sync iterator + sync callback; `E2` sync call + sync iterator + no callback; `E3` async call + async iterator + sync callback; `E4` async call + async iterator + async callback; `E5` async call + async iterator + no callback; `E6` async call + sync iterator + sync callback; `E7` async call + sync iterator + async callback; `E8` async call + sync iterator + no callback | All eight are supported. For `E2/E5/E8`, force an endpoint that requires streaming so the SDK actually drains the stream without a callback. The async/sync-iterator path keeps its executor behavior. |
| `C`: completion provenance | `C1` yielded only, wrapper attribute absent; `C2` yielded with wrapper attribute `None`; `C3` yielded A, wrapper becomes valid B after iteration; `C4` no yield, wrapper becomes valid B after iteration; `C5` initial valid A, no yield, wrapper clears to `None`; `C6` initial valid A, yielded B, wrapper clears to `None`; `C7` no completion anywhere; `C8` yielded A, wrapper becomes an invalid non-null value | Selected raw response is A for `C1/C2/C5`, B for `C3/C4/C6`; `C7` raises `LLMNoResponseError`; `C8` raises the existing invalid-completion error. `C3` proves wrapper precedence; `C2/C5` prove stale `None` cannot erase a valid response. |
| `O`: selected final output (`C1–C6` only) | `O1` final output populated; `O2` final output empty plus `output_item.done`; `O3` final output empty and no item-done event | `O1` preserves final output; `O2` reconstructs it from item-done events, including without callbacks; `O3` stays empty. For `C7/C8`, assert the completion error and check any preceding callbacks without inventing a final-output state. |
| `D`: event emitted before completion | `D1` text delta; `D2` refusal delta; `D3` reasoning-summary delta; `D4` unknown event object; `D5` `None` event | `D1–D3` emit the exact text, in order, with the original `item_id` when a callback is present. `D4/D5` emit nothing. No callback means no token chunks for any state. |

For every successful cell, assert the exact final `Message`, raw-response
identity, no duplicate callbacks, stable chunk IDs, output order, and telemetry
response ID. For error cells, assert the error type and that prior callbacks are
not replayed. `C8` is a valid yielded event followed by a malformed wrapper:
the malformed non-null wrapper must not be silently accepted.

Additional boundary cases outside the product:

| ID | State | Expected result |
| --- | --- | --- |
| `RS-X1` | Wrapper exposes invalid completion before iteration | Existing invalid-completion error before any event is consumed. |
| `RS-X2` | Provider returns a non-iterable while `stream=True` | Clear stream-type error; no callback. |
| `RS-X3` | Provider returns a complete non-streaming response despite `stream=True` | Complete response returned; no callback deltas. |
| `RS-X4` | Multiple deltas for one item, then another item | First item's chunk IDs match each other; second item's ID differs; text order preserved. |
| `RS-X5` | Two `output_item.done` events with empty final output | Reconstructed output contains both items in order. |
| `RS-X6` | Stream raises mid-iteration | Original failure propagates; no fabricated completion. |
| `RS-X7` | Callback raises | Callback failure propagates; stream is not reported as a successful response. |
| `RS-X8` | Async caller cancelled during an async stream | Caller receives cancellation; no completed result. |
| `RS-X9` | Async caller cancelled while its sync iterator drains in an executor | Caller receives cancellation; document the existing executor-drain behavior without asserting that the worker thread is killed. |

## 2. Request routing and Chat Completions

Check request-level behavior separately from the forced-stream product. Cross
each row with sync and async callers; use both Chat Completions and Responses
where the row applies. Do not apply the Responses-only `requires_streaming`
behavior to Chat Completions.

| ID | Request state | Expected transport and result |
| --- | --- | --- |
| `RT1` | Streaming requested, callback present | Provider receives `stream=True`; callback receives deltas. |
| `RT2` | Streaming requested, callback absent, normal endpoint | Provider receives a non-streaming request; full response returned. |
| `RT3` | Responses streaming requested, callback absent, endpoint requires streaming | Provider receives `stream=True`; stream drained; full response returned without deltas. |
| `RT4` | Responses streaming requested, provider returns a complete response | Full response returned and no callback invoked. Chat's transport does not have this same early-return path. |
| `RT5` | Streaming not requested, callback supplied | Non-streaming request; callback not invoked. |
| `RT6` | Streaming not requested, provider unexpectedly returns an iterator | Existing invalid-response error. |

For Chat Completions streaming, cross these supported transport/callback cells
with three chunk sequences: content only, tool-call chunks only, and mixed
content/tool-call chunks. Transport/callback cells are sync+sync callback,
async+async iterator+sync callback, async+async iterator+async callback,
async+sync iterator+sync callback, and async+sync iterator+async callback:
**5 × 3 = 15 cases**. Assert both callback chunks and the assembled final
`ModelResponse`/SDK `Message`. Add provider and callback exceptions once per
transport shape, plus async cancellation for both async iterator shapes.

## 3. Responses output normalization

Exercise `Message.from_llm_responses_output()` independently of streaming.
Use typed OpenAI objects, generic LiteLLM objects, dictionaries, and nested
attribute objects wherever that representation is constructible. Do not count
impossible vendor-model constructions as SDK failures.

| Branch | Cartesian axes | Expected result |
| --- | --- | --- |
| Text message | 4 representations × 5 content states: absent, empty, one `output_text`, multiple `output_text`, mixed text/refusal/unknown parts | Concatenate only non-empty `output_text` in order, join with newlines, trim final text. Missing/unknown parts contribute no text. |
| Function call | Typed OpenAI/LiteLLM plus generic/mapping/attribute shapes × ID states: both IDs, `call_id` only, item `id` only, neither × name/arguments present or omitted | `call_id` is canonical when available; preserve item `id` as `responses_item_id`. Typed missing IDs/name retain their stricter existing error; generic missing fields retain current defaults. Compare serialized replay IDs. |
| Reasoning | 4 representations × summary absent/empty/multiple × content absent/present × encrypted content absent/present × status absent/present = **96 combinations** | Preserve ID, summary order, content, encrypted payload, and status; absent content becomes `None`. |
| Mixed output | Message + function call + reasoning in several orders, with unknown items interleaved | Text and tool-call order stable; unknown kinds ignored; final reasoning item wins if more than one appears. Persisted-message JSON round trip preserves replay fields. |

Include nested objects and dictionaries inside each representation, not just at
the top level. Add one malformed-known-kind case per branch to record the
current validation error; never conflate it with an ignored unknown kind.

## 4. Chat message metadata and non-native tool calls

Generate the **4 × 5 × 3 = 60** Chat message combinations below. Use real
LiteLLM `Message` instances so absent optional fields exercise its actual
field-deletion behavior.

| Axis | States |
| --- | --- |
| Reasoning content | Absent attribute, explicit `None`, empty string, non-empty string |
| Thinking blocks | Absent, empty, signed thinking, redacted thinking, signed+redacted mixed |
| Tool calls | None, valid function call, only non-function calls |

Expected: reasoning and signatures survive supported conversions; redacted
data survives; missing and empty values follow existing serialization; valid
function calls retain IDs and arguments; only non-function calls produce the
existing error. Run serialization/replay checks for each successful state.

For non-native function calling, cross reasoning state (the same four) with
provider-specific fields (absent, empty, non-empty) and conversion outcome
(tool markup found or not): **4 × 3 × 2 = 24 cases**. Assert the converted
response, canonical tool call, preserved non-empty metadata, and absence of
fields that were absent or empty before conversion.

## 5. Tokenizer and optional Transformers

Generate **14 output shapes × 2 tokenizer sources × 2 tool states × 3 message
shapes = 168 cases** through public `LLM.get_token_count()`.

| Axis | States |
| --- | --- |
| Output shape | Flat token IDs; empty IDs; nested IDs; non-empty tensor-like `shape`; `.ids`; mapping `input_ids`; `.get("input_ids")`; non-empty `.encodings`; sequence of encoding objects; rendered string then `.encode`; object with both `shape` and `ids` (shape precedence); `.get()` returning `None` with usable encodings; unsupported object; rendered string without an encoder |
| Tokenizer source | Direct loaded chat-template tokenizer; tokenizer nested under the custom-tokenizer mapping |
| Tool state | No tools; one tool schema |
| Message state | Plain text; multi-part text; assistant tool call with JSON-string arguments |

For supported output, assert the exact integer count, template arguments
(`tokenize=True`, `add_generation_prompt=True`, tools when present), and
normalized message/tool arguments. For unsupported output or template failure,
assert fallback to LiteLLM's count; if that counter also fails, assert the
existing zero-count behavior. Include a tokenizer load matrix: Transformers
absent; import fails; `AutoTokenizer` absent; factory lacks `from_pretrained`;
load fails; loaded object lacks `apply_chat_template`; successful local tokenizer.
Repeat the last two environment states with a real installed tokenizer package
and without it, without downloading a model.

## 6. Authentication and persistence

Cross the three valid auth configurations (API key, already-restored
subscription, subscription needing restoration) with credential refresh
(available, unavailable): **3 × 2 = 6 cases**. API-key and already-restored
states must not refresh; the latter stays the same object. Restoration uses the
existing credentials and preserves model/usage settings; missing credentials
yields the current explicit error. Run a serialized-config round trip for the
two subscription states. As one separate defensive case, inject an unsupported
vendor into an already-constructed config and expect the explicit vendor error;
normal public `LLM` validation rejects that value earlier.

## 7. End-to-end evidence, after unit matrices

| Layer | Planned matrix | Assertions |
| --- | --- | --- |
| Local LiteLLM HTTP/SSE | Responses: 2 callers × (non-streaming + 3 final-output stream variants) = 8; Chat: 2 callers × (non-streaming + streaming) = 4; **12 cases** | Real parser/SDK path; exact final text, callback text, usage, stable IDs, output reconstruction. No provider mocking. |
| Optional dependency | Transformers absent and installed | SDK imports in both environments; local `AutoTokenizer` yields exact expected count. |
| Packaging | Source install and packaged Agent Server; relevant supported OS runners | Imports of the three new private modules, binary startup, `/health`, clean shutdown. |
| Live provider, if credentials are available | One Chat and one Responses call; streaming and non-streaming | Confirms provider compatibility and response shape. Keep this separate from deterministic acceptance tests. |

## Decision rule

When executed later, first fix failures in the 800-case Responses product or
cross-boundary message/tokenizer matrices. A green aggregate test count is not
enough: every selected response source, replay ID, callback sequence, and token
count must match its cell's expected result. Record pass/fail/skip and the
reason for any infeasible vendor-shape combination; do not silently prune cells.
