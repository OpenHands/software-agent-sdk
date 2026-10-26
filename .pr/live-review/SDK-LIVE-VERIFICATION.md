# Live Agent Server budget verification

Verified on 2026-10-26 UTC. Passed against SDK source `d352755baaef9db42d2f46dc7d99b163362f11bb`, using a new isolated Agent Server 1.58.2 and real `deepseek/deepseek-flash` calls. No provider stub was used. The server was initially launched at `7af842900b480f4028cb785a47c1abc68049b2c5`; the intervening fix only changes Python client handling of the create response. Both actual client checks use the final source. Initial and final doctor checks passed; the owned server was stopped.

| Scope | Result |
| --- | --- |
| F04 request contract | Positive cap echoed; zero, negative, Infinity and NaN rejected with 422; null and omission return null. |
| F05 below cap | Cap $0.05; finished at $0.0003864. |
| F05 overshoot | Cap $0.00000001; one real `think` call costs $0.00023424, then `MaxBudgetReached` and `error` before another model call. |
| F05 finished overshoot | A real immediate `finish` costs $0.0003645 above the same tiny cap and remains `finished`. |
| F06 live cost | REST, live WebSocket `stats` updates, and persisted state have the same accumulated costs and token counts. |
| F10/F31 fork and restart | Both the normal and exhausted conversations retain their caps in forks and after a graceful server restart. |
| F33 Python SDK | Actual factory/create, real run, attach, fork with retained stats, explicit request creation, and idempotent request creation preserve server cap values. Real successful run cost: $0.000457308. |
| F34 TypeScript client | Rebuilt client passes manager create/run, stats read, load/fork, start override, and explicit null override. Real run cost: $0.00020364. |

The [generated evidence table](SDK-EVIDENCE-TABLE.md) records each feature’s entry point, expected result, actual result, and published receipt.

The immutable-source hashes and compact extracted REST/WebSocket/client receipts are in `sdk-*-receipts.json`. `sdk-live-receipt.json` records the total reported conversation spend, including preliminary recipe runs and excluding duplicated fork metrics. Total reported conversation spend was **$0.003003408**. The profile validation endpoint also made one tiny real call but does not report its cost. `sdk-doctor.json` and `sdk-stop.json` record the final health and cleanup results.

The budget is conversation cumulative despite its existing name. The checks deliberately allow a completed call/step to overshoot; they do not enforce a pre-call hard ceiling. The cap comes from ConversationInfo (and the Python conversation property), while dynamic spend comes from `stats.usage_to_metrics`.

## Reproduce

From the SDK checkout, provision the existing key privately as `DEEPSEEK_API_KEY`. Never put it in command arguments or publish the private run directory.

```sh
C=.agents/skills/verify-agent-server/scripts/control-agent-server
R=$($C launch --new --name budget-review --print-run)
$C doctor --run "$R"
$C llm preset deepseek --run "$R"
python3 .pr/live-review/budget-rest.py "$R"
(cd clients/typescript && npm run build --silent)
$C exec --run "$R" --timeout 90 --expect-output PYTHON_BUDGET_OK --save F33.budget/client -- .venv/bin/python .pr/live-review/budget-python.py "$R/fixtures"
$C exec --run "$R" --timeout 90 --expect-output TYPESCRIPT_BUDGET_OK --save F34.budget/client -- node .pr/live-review/budget-typescript.mjs "$R/fixtures"
$C conversation start --run "$R" --tools none --no-autotitle --body-json '{"max_budget_per_run":0.00000001,"max_iterations":3}' --llm-json '{"max_output_tokens":128,"num_retries":0}' --prompt 'Call finish immediately with message BUDGET_FINISHED_OVER_OK. Do nothing else.' --wait --timeout 60 --save F05.budget/finished-over
$C doctor --run "$R"
$C stop --run "$R"
```

These are focused checks of the affected feature-map entries, not a full server audit. Existing feature-map recipes remain unchanged. Preliminary evidence-script assertions were corrected to compare cost/token values across serialization shapes, read cap metadata from REST, and close the Python workspace HTTP client explicitly; these were recipe issues, not product failures.
