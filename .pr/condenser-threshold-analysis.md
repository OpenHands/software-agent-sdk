# Condenser token trigger analysis

## Observation

A long-running agent conversation on a local 27B deployment (Qwen3.8-27B Q4,
`max_input_tokens=233472`, server `n_ctx=262144`) used an
`LLMSummarizingCondenser` configured with `max_tokens=235000` and
`max_size=240` events.

Prompt size per model call over the affected turn: 173,019, 178,512, 182,268,
**183,553** (the call that lost the task), 190,042, 188,775, 193,553.

- The token trigger could not fire: the request is capped at the model's input
  budget (233,472), so `total_tokens > 235,000` was unreachable at any point.
- The event trigger did not fire either: fewer than `max_size=240` events had
  accrued since the last condensation.
- Result: the turn ran to 183,553 tokens with no condensation and the agent
  abandoned the current instruction for a topic from the previous day.

The upstream default has the same shape: `build_condenser` uses
`llm.effective_max_input_tokens` verbatim when `max_tokens` is unset (#3746).
That value is the largest prompt the model can ever receive, so a trigger at
or above it cannot fire before the request itself is rejected or truncated.

## Replication attempt

The drifting turn was replayed from a fork of the stored conversation, with the
same model (temperature 1.0), the same instruction and the same workspace:

- Fork context: 147,408 prompt tokens, prefill 141,495, cache hits 0.
- 15 actions, through the same `page.tpl.php` edit the original made
  immediately before drifting. The plan was correct and every action stayed on
  the portal task; no recall of the stale topic, no topic change.
- The replay's context was therefore smaller than the 183,553 at the original
  drift call, and the drift did not reproduce.

Conclusion: the drift is not deterministic at this context size; sampling
variance at temperature 1.0 remains the leading explanation. The guardrail
defect is independent of that: the configured limit could not fire at any
context size.

Operational notes from the replay:

- A forked conversation restores the agent LLM from `base_state.json`, which is
  the pristine profile LLM (mimo), not the model the conversation was switched
  to at runtime (Qwen). The replay required an explicit `switch_llm`.
- Cold prefill of a 147k-token replayed context took about 12 minutes at roughly
  215 tokens/second with zero prompt-cache reuse.

## Change

1. `condenser_input_ratio(model)` in `llm/utils/model_features.py`: an ordered,
   case-insensitive substring rule table (`last match wins`) with a default of
   `0.70`. Models that degrade earlier than their trained window can be listed
   (Qwen3.8-27B at `0.685`).
2. `build_condenser` derives `int(budget * ratio)` when `max_tokens` is unset,
   and clamps an explicit value to the same ceiling. An explicit
   `max_tokens=None` keeps event-count-only condensation.

## Notes

- No persisted settings shape changes, so no schema migration is needed.
- The ratio is a policy default; a deployment that wants a different fraction
  can set `max_tokens` explicitly below the derived ceiling.
- Verified locally against the v1.59.0 tag with the estate's pinned dependency
  set (see How to Test); CI runs the same tests on `main`.
