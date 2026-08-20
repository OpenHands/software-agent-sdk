# GPT-5-nano integration results

Tested PR head `cf0fd2e11e3e26ded2ed1eb31fc9178c567d6ba6` on 2026-08-18 with `litellm_proxy/openai/gpt-5-nano`, `reasoning_effort=high`, and `https://llm-proxy.eval.all-hands.dev`.

No `b*` behavior tests were run.

## Result summary

| Test | Result |
| --- | --- |
| Focused unit suite, `tests/sdk/event/test_events_to_messages.py` | 23 passed |
| New reasoning-item regression against `origin/main` | Failed as expected: the combined message had `responses_reasoning_item=None` |
| `t*` integration suite | 8 passed, 1 failed |
| `c*` condenser suite | 2 passed, 2 failed, 1 skipped |
| Isolated rerun of `c02` and `c05` | `c05` passed; `c02` failed again |
| Purpose-built parallel-tool replay probe | Passed |

## Integration suite details

### `t*`

Passed: `t01`, `t02`, `t03`, `t04`, `t06`, `t07`, `t08`, and `t09`.

`t05_simple_browsing` failed twice, including an isolated retry. Chromium launched and the agent navigated the test site, but GPT-5-nano stopped after saying it would fetch the answer instead of reporting it. This is model behavior and does not exercise action batching.

### `c*`

- `c01_thinking_block_condenser`: skipped as designed because GPT-5-nano produces Responses API reasoning items rather than Anthropic thinking blocks.
- `c03_delayed_condensation`: passed with five condensations.
- `c04_token_condenser`: passed.
- `c05_size_condenser`: failed initially because the model emitted only one tool call and stopped before enough events existed; it passed on an isolated rerun, confirming model-dependent flakiness.
- `c02_hard_context_reset`: failed twice because GPT-5-nano answered calculation requests directly instead of creating enough tool-loop events for the second condensation to become a normal condensation.

The `c02` and initial `c05` failures did not produce parallel sibling `ActionEvent`s, so they did not exercise the code changed by this PR.

## Direct parallel-tool replay validation

A first live attempt asked GPT-5-nano to issue two calls to the same terminal tool in parallel. The model instead emitted them in two separate LLM responses, confirming that a generic integration task does not reliably cover this regression.

A second probe exposed two distinct independent tools, `get_alpha` and `get_beta`, and required both before a final response. GPT-5-nano then produced:

1. two `ActionEvent`s with the same `llm_response_id`;
2. a Responses API reasoning item only on the first action;
3. a recombined assistant message containing both tool calls in order;
4. a reasoning item exactly equal to the first action's item; and
5. a successful follow-up Responses API turn ending with `alpha beta verified`.

This exercises the PR's changed path end to end: the reasoning item is retained on the recombined message and accepted when the tool-call batch is sent back to GPT-5-nano.

## Commands

```bash
uv run --frozen pytest tests/sdk/event/test_events_to_messages.py

LLM_API_KEY=... \
LLM_BASE_URL=https://llm-proxy.eval.all-hands.dev \
IN_DOCKER=true \
uv run --frozen python tests/integration/run_infer.py \
  --llm-config '{"model":"litellm_proxy/openai/gpt-5-nano","reasoning_effort":"high"}' \
  --num-workers 4 \
  --test-type integration

LLM_API_KEY=... \
LLM_BASE_URL=https://llm-proxy.eval.all-hands.dev \
IN_DOCKER=true \
uv run --frozen python tests/integration/run_infer.py \
  --llm-config '{"model":"litellm_proxy/openai/gpt-5-nano","reasoning_effort":"high"}' \
  --num-workers 4 \
  --test-type condenser
```

## Assessment

The focused regression and the live parallel-tool replay both validate the fix. The remaining integration failures are explained by GPT-5-nano task compliance and did not execute the changed parallel-action reconstruction path.
