---
name: compare-eval-runs
description: This skill should be used when the user asks to "compare eval runs", "compare evaluation results", "check eval regression", "compare benchmark results", "what changed in the eval", "diff eval runs", or mentions comparing SWE-bench, GAIA, or other benchmark evaluation results between runs. Provides workflow for finding, comparing, and reporting evaluation performance differences.
---

# Comparing Evaluation Runs

## Overview

OpenHands evaluations produce results stored on a CDN at `https://results.eval.all-hands.dev/`. Each run is identified by a path: `{benchmark}/{model_slug}/{github_run_id}/`. This skill enables comparison of two evaluation runs and posting the results as a GitHub PR comment.

## Quick Start

Run the bundled comparison script with auto-baseline detection:

```bash
python .agents/skills/compare-eval-runs/scripts/compare_eval_runs.py \
    "<benchmark>/<model_slug>/<run_id>/" \
    --auto-baseline
```

To post results as a PR comment:

```bash
python .agents/skills/compare-eval-runs/scripts/compare_eval_runs.py \
    "<benchmark>/<model_slug>/<run_id>/" \
    --auto-baseline \
    --post-comment --pr <PR_NUMBER> --repo OpenHands/software-agent-sdk
```

## Step-by-Step Workflow

### Step 1: Find the Current PR's Eval Run

Eval runs are triggered by adding labels like `run-eval-50` to a PR. The `all-hands-bot` posts a comment with results when complete.

**Option A — From bot comments on the PR:**

```bash
gh api repos/OpenHands/software-agent-sdk/issues/<PR_NUMBER>/comments \
    --jq '.[] | select(.user.login == "all-hands-bot") | .body' \
    | grep -o 'Evaluation:.*' | head -1
```

The evaluation name follows the format `{github_run_id}-{model_slug_short}` (e.g., `23775164157-claude-son`). Extract the `github_run_id` from this.

**Option B — From the "Evaluation Triggered" bot comment:**

```bash
gh api repos/OpenHands/software-agent-sdk/issues/<PR_NUMBER>/comments \
    --jq '.[] | select(.body | test("Evaluation Triggered")) | .body'
```

This contains the SDK commit SHA. Cross-reference with daily metadata to find the run ID.

**Option C — From daily metadata:**

```bash
curl -s "https://results.eval.all-hands.dev/metadata/$(date -u +%Y-%m-%d).txt"
```

Each line is a run path. Match by benchmark and model to find the run.

### Step 2: Identify the Run Path Components

A run path has three components:
- **benchmark**: `swebench`, `gaia`, `swtbench`, `commit0`, `swebenchmultimodal`, `terminalbench`
- **model_slug**: Derived from model name with `/:@.` replaced by `-` (e.g., `litellm_proxy-claude-sonnet-4-5-20250929`)
- **run_id**: The GitHub Actions workflow run ID from the `OpenHands/evaluation` repo

### Step 3: Verify Results Exist

```bash
curl -sI "https://results.eval.all-hands.dev/<benchmark>/<model_slug>/<run_id>/output.report.json" | head -1
```

A `200` status confirms the run completed and results are available.

### Step 4: Find a Baseline for Comparison

**Automatic**: The comparison script's `--auto-baseline` flag scans metadata files backward up to 14 days to find the most recent completed run with the same benchmark and model.

**Manual**: Inspect metadata files or other PR bot comments to identify a specific run:

```bash
# Check today's runs
curl -s "https://results.eval.all-hands.dev/metadata/$(date -u +%Y-%m-%d).txt" | grep "swebench/litellm_proxy-claude"

# Check yesterday's runs
curl -s "https://results.eval.all-hands.dev/metadata/$(date -u -d yesterday +%Y-%m-%d).txt" | grep "swebench/litellm_proxy-claude"
```

### Step 5: Run the Comparison

```bash
python .agents/skills/compare-eval-runs/scripts/compare_eval_runs.py \
    "swebench/litellm_proxy-claude-sonnet-4-5-20250929/23775164157/" \
    --baseline "swebench/litellm_proxy-claude-sonnet-4-5-20250929/23773892085/"
```

Or with auto-baseline and PR comment posting:

```bash
python .agents/skills/compare-eval-runs/scripts/compare_eval_runs.py \
    "swebench/litellm_proxy-claude-sonnet-4-5-20250929/23775164157/" \
    --auto-baseline \
    --post-comment --pr 2334 --repo OpenHands/software-agent-sdk
```

## Available Data Per Run

Each run stores files at `https://results.eval.all-hands.dev/{run_path}/`:

| File | Description |
|------|-------------|
| `metadata/params.json` | Run parameters: SDK commit, PR number, model, eval_limit, triggered_by |
| `output.report.json` | Aggregated results: resolved/submitted/total counts and instance IDs |
| `cost_report.jsonl` | Per-instance cost data |
| `results.tar.gz` | Full archive with all outputs |

## Dashboard

The eval monitor dashboard provides a visual view of runs:

```
https://openhands-eval-monitor.vercel.app/?run={benchmark}/{model_slug}/{run_id}/
```

## Interpreting Results

- **Success rate** = resolved / min(eval_limit, total_instances)
- A 50-instance sample has natural variance of ±2-4 resolved instances between runs
- Focus on **instance-level changes** (gained/lost) to understand regressions vs. noise
- If the same set of instances is resolved, the difference is likely noise

## Additional Resources

### Reference Files
- **`references/eval-infrastructure.md`** — Detailed documentation on the evaluation infrastructure, GCS paths, metadata format, and workflow triggers

### Scripts
- **`scripts/compare_eval_runs.py`** — Standalone comparison script with auto-baseline detection and GitHub comment posting
