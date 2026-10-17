# Live DeepSeek verification of tool-call batch preservation

Three contributor-supplied candidate reruns returned **HTTP 200 from DeepSeek**.
Each next request keeps all scripted calls in one assistant message, retains its
120-character `reasoning_content`, and follows it with one actual result or
validation error per call. Baseline records return HTTP 400 for the same cases.
These checks validate acceptance of the next request, not full task completion.

## Results

| Case | Scripted response | Baseline | Candidate | Records |
| --- | --- | --- | --- | --- |
| 1 | Invalid task status, then a valid file view | HTTP 400: missing reasoning content on the split-off message | HTTP 200 | [base](live_deepseek/mergebase-case1.json) / [candidate](live_deepseek/candidate-case1.json) |
| 2 | Malformed file-editor JSON, then a valid task plan | HTTP 400: missing reasoning content on the split-off message | HTTP 200 | [base](live_deepseek/mergebase-case2.json) / [candidate](live_deepseek/candidate-case2.json) |
| 3 | Valid file view A, invalid task update B, valid task view C | HTTP 400: insufficient tool messages following the split batch | HTTP 200 | [base](live_deepseek/mergebase-case3.json) / [candidate](live_deepseek/candidate-case3.json) |

The supplied reruns use `openai/deepseek/deepseek-v4.1-flash` via OpenRouter,
with the provider pinned to DeepSeek, fallbacks disabled, thinking enabled at
low effort, and a 512-token response limit. The HTTP responses identify
`DeepSeek` as the provider. Each run records two LLM calls, of which one is a
real API request. The records were inspected; no new API calls were made while
preparing these review artifacts.

## Comparison with the baseline

The raw SDK message lists and actual HTTP bodies both contain one complete
assistant tool-call batch. In each case, the following match the baseline after
replacing the temporary workspace root with `<workspace>`:

- HTTP request parameters, including model, provider selection, and reasoning.
- System-prompt length and SHA-256, normalized tool-schema SHA-256, and user message.
- Every tool-call object, including ID, name, and arguments.
- Every tool-result message, its contents, and its relative order.

Combining the baseline's split assistant tool calls produces the candidate's
entire normalized message list exactly. Result order is unchanged: invalid
call then valid call in cases 1/2; B, A, C in case 3. The first assistant message
already carries the reasoning; grouping removes the second message that lacked it.

The recorded event sequence still has the validation error **before** the last
ActionEvent, while the outgoing message includes all calls together. This is
consistent with the projection fix preserving original dispatch/callback order.
See each record's `baseline_comparison` for the verified invariants.

The tools hash uses `sha256(json.dumps(normalized_tools, sort_keys=True).encode())`
with Python's default separators. The system hash covers the original system
content; that content has no temporary workspace path.

## Run outcome and provenance

All three HTTP responses request one additional tool call. The SDK executes it,
then records `ConversationErrorEvent` and `ConversationExecutionStatus.ERROR`;
`run_result` is `returned`. This matches the script's configured
`max_iteration_per_run=2`: the SDK sets ERROR at the iteration cap when the agent
has not finished. See
[the run-limit handler](https://github.com/OpenHands/software-agent-sdk/blob/3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14/openhands-sdk/openhands/sdk/conversation/impl/local_conversation.py#L2021).

The raw recorder stores event types, not the final error event's code/detail.
The iteration-limit explanation is inferred from the script configuration,
returned tool calls, event sequence, and SDK handler. The direct observation is
HTTP acceptance; this evidence does not claim a FINISHED conversation.

Baseline revision: `3311ba9eec5044f40ab5d0b3d7eddc9f7e1e2d14`.
The contributor supplied the candidate files as reruns of the local candidate.
Their label is `pr-rerun`; the raw recorder does not capture a Git SHA or source
hash, so these logs alone cannot independently prove the exact tested revision.
Each curated file records the source filename and SHA-256 for traceability.
[source-manifest.json](source-manifest.json) separately identifies the source
snapshot used for local regression and offline reproduction checks.

Candidate environment recorded in the files: Python 3.13.12, SDK/tools 1.49.6,
LiteLLM 1.93.0, Pydantic 2.12.5. Baseline records report Python 3.13.14 with the
same package versions; the matching normalized inputs above were checked despite
that Python-version difference. These are synthetic reproductions, not the
original production conversation.

## Reproduce

After `make build`, set `OPENROUTER_API_KEY` in your local environment and run
from the checkout containing the candidate source:

```bash
OPENHANDS_SUPPRESS_BANNER=1 LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync python .pr/live_deepseek.py \
  --case 1 --label candidate --out /tmp/deepseek-case1.raw.json
```

Repeat for `--case 2` and `--case 3`, using distinct filenames. The label is
metadata; it does not select an implementation. To compare with the baseline,
copy the script outside the checkout and run it from a separate checkout of the
pinned baseline with dependencies installed.

[live_deepseek.py](live_deepseek.py) uses `Conversation.run()` with real
`file_editor` and `task_tracker` execution in a temporary workspace. The first
completion is scripted; the next uses the real API. Each case makes one billable
API request with the configured two-iteration limit.

Inspect the recorded **HTTP status**: the recorder can exit zero after capturing
an HTTP error. Check the grouping, reasoning, and actual results as well.

## Publication and scope

Candidate raw files were left unchanged. The public copies omit provider request
IDs, session/cache identifiers, response fingerprints, full response reasoning,
and headers; replace local workspace paths with `<workspace>`; summarize the
system prompt by length/hash; and summarize tools by their normalized hash.
The response copies retain provider, model, finish reason, and returned tool calls.
No API-key or Authorization value was found in the supplied raw files. Their
session identifiers and local paths were still removed before publication.

Tool-call IDs are retained to show call/result relationships. First-response IDs
are synthetic constants; returned tool-call IDs are identifiers, not credentials.
Fresh raw output and console logs still require separate review before sharing.

Live evidence covers DeepSeek through OpenRouter Chat Completions. Other live
providers, live Responses, and native Responses-origin runs remain untested.
See [validation.md](validation.md) for the completed local checks and their scope.
