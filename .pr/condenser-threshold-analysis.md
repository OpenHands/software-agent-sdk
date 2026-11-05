# Condenser token trigger analysis

## Observation

A long-running agent conversation on a local 27B deployment (Qwen3.8-27B Q4,
`max_input_tokens=233472`, server `n_ctx=262144`) reached a **193,553 token
prompt** with an `LLMSummarizingCondenser` configured with `max_tokens=235000`
and `max_size=240` events.

- The token trigger could not fire: the request is capped at the model's input
  budget (233,472), so `total_tokens > max_tokens` was unreachable.
- The event trigger did not fire either: 145 events had accrued since the last
  condensation, below `max_size=240`.
- Result: the conversation ran at ~83% of its input budget with no
  condensation, and the agent lost the current instruction, reverting to a
  topic from the previous day.

The upstream default has the same shape: `build_condenser` uses
`llm.effective_max_input_tokens` verbatim when `max_tokens` is unset (#3746).
That value is the largest prompt the model can ever receive, so the trigger is
at or below the cap and cannot fire before the request itself is rejected or
truncated.

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
