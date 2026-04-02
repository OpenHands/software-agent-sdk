# Evaluation Infrastructure Reference

## Architecture Overview

The evaluation pipeline spans three repositories:

1. **OpenHands/software-agent-sdk** — Triggers evaluations via `run-eval.yml` workflow
2. **OpenHands/evaluation** — Orchestrates the eval job via `eval-job.yml` workflow
3. **OpenHands/benchmarks** — Contains benchmark runners (inference + evaluation)

## Trigger Flow

### PR Label Trigger

1. A label (`run-eval-1`, `run-eval-50`, `run-eval-200`, `run-eval-500`) is added to a PR
2. `software-agent-sdk/.github/workflows/run-eval.yml` fires
3. It resolves model configs from `.github/run-eval/resolve_model_config.py`
4. Dispatches `eval-job.yml` in `OpenHands/evaluation` with:
   - `sdk_commit`: The PR's head SHA
   - `sdk_workflow_run_id`: The `run-eval.yml` workflow run ID
   - `eval_limit`: Extracted from label name
   - `models_json`: Resolved model configurations
   - `pr_number`: The PR number (for result posting)
5. Posts an "Evaluation Triggered" comment on the PR

### Release Trigger

Runs automatically on `release` events with `eval_limit=50`.

### Manual Trigger

Via `workflow_dispatch` on `run-eval.yml` with explicit parameters.

## Results Storage (GCS)

Results are stored in Google Cloud Storage bucket `openhands-evaluation-results`
and served via CDN at `https://results.eval.all-hands.dev/`.

### Run Path Format

```
{benchmark}/{model_slug}/{github_run_id}/
```

- **benchmark**: `swebench`, `gaia`, `swtbench`, `commit0`, `swebenchmultimodal`, `terminalbench`
- **model_slug**: Model name with `/:@.` replaced by `-`
  - Example: `litellm_proxy/claude-sonnet-4-5-20250929` → `litellm_proxy-claude-sonnet-4-5-20250929`
- **github_run_id**: The GitHub Actions run ID from the `OpenHands/evaluation` repo

### Files Per Run

```
{run_path}/
├── metadata/
│   └── params.json          # Job parameters (uploaded at job start)
├── output.report.json       # Aggregated evaluation results
├── cost_report.jsonl        # Per-instance cost data
└── results.tar.gz           # Full archive
```

### params.json Schema

```json
{
    "timestamp": "2026-03-31T00:54:15Z",
    "sdk_commit": "42852dc2260a461536acc186cd918ad5a58910dd",
    "sdk_workflow_run_id": "23775150328",
    "eval_limit": 50,
    "benchmark": "swebench",
    "model_name": "litellm_proxy/claude-sonnet-4-5-20250929",
    "model_id": "claude-sonnet-4-5-20250929",
    "model_display_name": "Claude Sonnet 4.5",
    "unique_eval_name": "23775164157-claude-son",
    "commit": "42852dc2260a461536acc186cd918ad5a58910dd",
    "pr_number": "2334",
    "triggered_by": "enyst",
    "tool_preset": "default",
    "agent_type": "default",
    "github_run_id": "23775164157"
}
```

### output.report.json Schema

```json
{
    "total_instances": 500,
    "submitted_instances": 50,
    "completed_instances": 50,
    "resolved_instances": 35,
    "unresolved_instances": 15,
    "empty_patch_instances": 0,
    "error_instances": 0,
    "completed_ids": ["instance_id_1", "..."],
    "resolved_ids": ["instance_id_1", "..."],
    "unresolved_ids": ["instance_id_1", "..."],
    "empty_patch_ids": [],
    "error_ids": []
}
```

## Daily Metadata

All runs registered on a given day are listed in:

```
https://results.eval.all-hands.dev/metadata/YYYY-MM-DD.txt
```

Each line is a run path. Example:

```
swebench/litellm_proxy-claude-sonnet-4-5-20250929/23773892085/
swebench/litellm_proxy-gemini-3-flash-preview/23774756886/
gaia/litellm_proxy-claude-sonnet-4-5-20250929/23775142614/
```

Metadata files are updated atomically with generation preconditions and
have `Cache-Control: no-cache` set.

## Dashboard

The eval monitor dashboard at `https://openhands-eval-monitor.vercel.app/`
provides a visual view of runs. Construct URLs as:

```
https://openhands-eval-monitor.vercel.app/?run={benchmark}/{model_slug}/{run_id}/
```

## Bot Comments

When an eval completes, `all-hands-bot` posts a comment on the PR (if `pr_number` was provided) with:

- Evaluation name (e.g., `23775164157-claude-son`)
- Model name
- Results summary (total, submitted, resolved, unresolved, empty patch, error counts)
- Success rate
- Archive link

## Model Slug Computation

The model slug is derived from the LLM config's `model` field:

```python
model = config["model"]  # e.g., "litellm_proxy/claude-sonnet-4-5-20250929"
for ch in "/:@.":
    model = model.replace(ch, "-")
# Result: "litellm_proxy-claude-sonnet-4-5-20250929"
```

## Available Models

Models are defined in `software-agent-sdk/.github/run-eval/resolve_model_config.py`.
Each model has an `id`, `display_name`, and `llm_config` with the model path and parameters.

## Variance Between Runs

For 50-instance SWE-bench evaluations:
- Natural variance is typically ±2-4 resolved instances between identical configurations
- Focus on instance-level changes (which specific instances gained/lost) to distinguish real regressions from noise
- If the resolved instance set is identical, the runs are equivalent
