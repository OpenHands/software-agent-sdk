---
name: run-eval
type: knowledge
version: 1.0.0
agent: CodeActAgent
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
- `benchmarks_branch`: Use feature branch to test benchmarks changes before merging

**Alternative:** Add labels `run-eval-1`, `run-eval-50`, `run-eval-200`, `run-eval-500` to PRs.

## Monitoring

**Datadog script** (in `OpenHands/evaluation` repo):
```bash
DD_API_KEY=$DD_API_KEY DD_APP_KEY=$DD_APP_KEY DD_SITE=$DD_SITE \
  python scripts/analyze_evals.py --job-prefix <EVAL_RUN_ID> --time-range 60
```

**kubectl** (requires cluster access):
```bash
kubectl logs -f job/eval-eval-<RUN_ID>-<MODEL_SLUG> -n evaluation-jobs
```

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `503 Service Unavailable` | Infrastructure overloaded | Reduce concurrent evals to 2-3 |
| `429 Too Many Requests` | Rate limiting | Wait or reduce concurrency |
| `failed after 3 retries` | Instance failures | Check Datadog logs for root cause |

## Limits

- Max 256 parallel runtimes
- Full evals take 1-3 hours
- swebenchmultimodal: 102 instances
