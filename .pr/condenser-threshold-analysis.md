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

## Replication attempts

The instruction was replayed from forks of the stored conversation, with the
same model (temperature 1.0), the same instruction text and the same workspace.

### Run A: 147,408 tokens

Fifteen actions, including the same `page.tpl.php` edit the original made
immediately before drifting. The plan was correct, every action stayed on the
portal task, and there was no stale-topic recall. Cold prefill: 141,495 of
147,408 tokens, 0 cache hits, about 12 minutes at roughly 215 tokens/second.
The drift did not reproduce, but this run stayed 36k tokens below the original
drift call.

### Run B: up to 166,506 tokens

Prompt sizes per call: 94,339 (57,344 cached), 116,348, 135,805, 145,427,
149,160, 154,750, 157,178, 159,356, 160,102, 161,331, 161,380, 161,825,
163,827, 166,506.

- **Stale-topic intrusion at about 154,700 tokens.** The model wrote a plan
  consisting of the *previous* workstream in the same conversation (the vault
  overlay: `SecretVault` class, `settings.inc.php` wiring, `vault-read.php`,
  deploy, the FL OpenBao AppRole step), all entries marked done, before
  resuming the current task.
- Execution stayed on task otherwise: marketing CSS/tailwind/wordmark study,
  `portal.css` review, `page.tpl.php` edits, a PHP render test, a browser
  preview, `php -l`, then two commits (Cache-Control and the visual restyle)
  and an attempted `git push`.
- Stopped at 166,506 tokens (91% of the original drift call) once the replay
  began pushing.

Conclusion: the drift is not deterministic. Stale-workstream intrusions appear
from about 155k tokens, while the hard derail (abandoning the task to answer a
day-old question) did not recur below 166.5k. Sampling variance at temperature
1.0 is the leading explanation, with context size a contributing factor. The
guardrail defect is independent of all of this: the configured limit could not
fire at any context size.

Operation notes from the replays:

- A forked conversation restores the agent LLM from `base_state.json`, which is
  the pristine profile LLM, not the model the conversation had been switched to
  at runtime. The replay required an explicit `switch_llm`.
- Cold prefill of a replayed long context costs minutes with zero cache reuse;
  the original conversation was fast because nearly all prompt tokens were
  cache reads.
- A replay of a long tool-using turn will edit files and commit. Run it with the
  git remote removed or renamed, and restore the working tree afterwards.

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
