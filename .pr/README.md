# PR evidence: GPT-5 `update_plan` live run

This directory contains reviewer-facing artifacts for PR #2712.

## What was validated

I required a real end-to-end run of the new GPT-5 `update_plan` example rather than relying only on unit tests.

The validated example is:

- `examples/04_llm_specific_tools/03_gpt5_update_plan_preset.py`

## Live test setup

The example was executed against direct OpenAI using the GPT-5 preset:

```bash
OPENHANDS_SUPPRESS_BANNER=1 \
OPENAI_API_KEY="$OPENAI_API_KEY" \
uv run --project /workspace/project/software-agent-sdk \
  python /workspace/project/software-agent-sdk/examples/04_llm_specific_tools/03_gpt5_update_plan_preset.py
```

The run used a temporary workspace so the repository worktree stayed clean.

## Expected behavior

The reviewer comment asked for evidence that this PR really fixes the GPT-5 preset/tooling path, not just the static schema.

For that reason, the live run needed to show all of the following:

1. the GPT-5 preset loads `update_plan`
2. the agent actually calls `update_plan`
3. the example completes successfully and edits the workspace as instructed
4. `task_tracker` does not appear on the GPT-5 preset tool surface

## Results

The live run succeeded.

Key evidence captured in the artifacts:

- the loaded tool surface includes `terminal`, `apply_patch`, and `update_plan`
- `task_tracker` does not appear in the loaded tool list or tool-call log
- the agent called `update_plan` twice during the run
- the example produced `GPT5_PLAN_DEMO.txt` in the temporary workspace
- the run printed `EXAMPLE_COST: 0.05832974999999999`

The generated demo file contained:

```text
Checklist of changes for this run:
- Defined a three-step plan via update_plan.
- Created or updated GPT5_PLAN_DEMO.txt with this checklist.
```

## Files in this directory

- `gpt5_update_plan_live_test.txt`
  - raw stdout/stderr from the successful example run
- `gpt5_update_plan_live_test_telemetry.json`
  - structured summary of the run: workspace path, tool mentions, loaded tool line, demo file contents, and cost line

## Notes

- The live run used direct OpenAI because that path worked in this environment without needing the All-Hands proxy.
- In this specific run, the file creation step was executed via `terminal` rather than `apply_patch`. That is acceptable for this PR because the evidence being requested was specifically about the GPT-5 preset exposing and using `update_plan` instead of `task_tracker`.
