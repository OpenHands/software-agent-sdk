---
name: run-eval
description: Trigger and monitor evaluation runs for benchmarks like SWE-bench, GAIA, and others. Use when running evaluations via GitHub Actions or monitoring eval progress through Datadog and kubectl.
triggers:
- run eval
- trigger eval
- evaluation run
- swebench eval
---

# Running Evaluations

## Trigger via GitHub API

```bash
curl -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/workflows/run-eval.yml/dispatches" \
  -d '{
    "ref": "main",
    "inputs": {
      "benchmark": "swebench",
      "sdk_ref": "main",
      "eval_limit": "50",
      "model_ids": "claude-sonnet-4-5-20250929",
      "reason": "Description of eval run",
      "benchmarks_branch": "main"
    }
  }'
```

**Key parameters:**
- `benchmark`: `swebench`, `swebenchmultimodal`, `gaia`, `swtbench`, `commit0`, `multiswebench`
- `eval_limit`: `1`, `50`, `100`, `200`, `500`
- `model_ids`: See `.github/run-eval/resolve_model_config.py` for available models
- `benchmarks_branch`: Use feature branch from the benchmarks repo to test benchmark changes before merging

**Note:** When running a full eval, you must select an `eval_limit` that is greater than or equal to the actual number of instances in the benchmark. If you specify a smaller limit, only that many instances will be evaluated (partial eval).

## Monitoring

**Datadog script** (requires `OpenHands/evaluation` repo; DD_API_KEY, DD_APP_KEY, and DD_SITE environment variables are set):
```bash
DD_API_KEY=$DD_API_KEY DD_APP_KEY=$DD_APP_KEY DD_SITE=$DD_SITE \
  python scripts/analyze_evals.py --job-prefix <EVAL_RUN_ID> --time-range 60
# EVAL_RUN_ID format: typically the workflow run ID from GitHub Actions
```

**kubectl** (for users with cluster access - the agent does not have kubectl access):
```bash
kubectl logs -f job/eval-eval-<RUN_ID>-<MODEL_SLUG> -n evaluation-jobs
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `503 Service Unavailable` | Infrastructure overloaded | Ask user to stop some evaluation runs |
| `429 Too Many Requests` | Rate limiting | Wait or reduce concurrency |
| `failed after 3 retries` | Instance failures | Check Datadog logs for root cause |

## Limits

- Max 256 parallel runtimes (jobs will queue if this limit is exceeded)
- Full evals typically take 1-3 hours depending on benchmark size
