# GPT-5 preset prompt redundancy analysis

## What I analyzed

The effective GPT-5 preset system prompt is the composition of:

1. `openhands-sdk/openhands/sdk/agent/prompts/system_prompt.j2`
2. `openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5.j2`
3. `openhands-sdk/openhands/sdk/agent/prompts/system_prompt_gpt_5_4.j2`

I then moved the communication-style guidance out of `system_prompt_gpt_5_4.j2` and into the GPT-5-specific snippet, so the relevant question is whether the **combined rendered prompt** still repeats ideas already present in the base OpenHands prompt and the GPT-5 model-specific snippet.

## Redundancy findings

### 1. Workflow / persistence guidance overlaps with the base prompt

**Where it overlaps**

- Base prompt: `system_prompt.j2` already has `PROBLEM_SOLVING_WORKFLOW` with exploration, analysis, testing, implementation, and verification.
- Overlay prompt: `system_prompt_gpt_5_4.j2` adds `<WORKFLOW>` plus `<VALIDATION_LOOP>`.

**What repeats**

- keep going through implementation instead of stopping at analysis
- retrieve missing context instead of guessing
- validate before finalizing
- start with the smallest relevant test/check
- avoid fixing unrelated failures

**Assessment**

This is real overlap, but mostly **emphasis overlap**, not contradiction. The overlay sharpens behavior in the same direction as the base prompt rather than sending conflicting instructions.

**Would I remove it now?**

Probably not in this PR. If we delete too much from the overlay, we lose the very GPT-5.x behavior tuning this PR is trying to add.

## 2. Concision and response-shape guidance is now consolidated in the GPT-5 model-specific prompt

**What changed**

- `model_specific/openai_gpt/gpt-5.j2` now carries the compact-answer and presentation rules.
- `system_prompt_gpt_5_4.j2` no longer has separate `<OUTPUT_VERBOSITY_SPEC>` or `<PRESENTING_WORK>` blocks.

**Assessment**

This removes the most obvious style redundancy I found in the first pass. The communication-style rules now live beside the existing GPT-5-specific preamble guidance, which is a cleaner factoring.

**Residual overlap**

There is still some thematic overlap with the base prompt's general advice on concise, useful answers, but it is now much smaller and localized to one GPT-5-specific snippet.

## 3. Editing guidance overlaps with existing OpenHands repository guidance

**Where it overlaps**

- Base prompt already says to edit files directly, keep changes minimal, avoid redundant comments, and explore before editing.
- Overlay prompt adds ASCII/comment/apply-patch/re-read guidance in `<EDITING_CONSTRAINTS>`.

**What repeats**

- keep edits minimal and focused
- avoid unnecessary comments
- use efficient editing approaches

**What is actually new**

- explicit ASCII-default rule
- explicit `apply_patch` preference
- explicit "don't immediately re-read after apply_patch" reminder

**Assessment**

This section is only partially redundant. It still carries useful GPT-5/Codex-specific editing behavior that is not stated as explicitly in the base prompt.

## 4. The old same-machine assumption was more than redundant: it was sometimes wrong

**Where it came from**

- Codex prompt assumes a local CLI running on the user's machine.
- OpenHands GPT-5 preset can run in CLI mode or in remote/server-backed setups.

**Assessment**

This was the only item I would classify as a correctness issue rather than mere repetition.

**Action taken**

I changed the line so it is only asserted when `cli_mode=True`; otherwise the prompt falls back to the generic rule not to dump large file contents.

## 5. Task tracking is not meaningfully redundant in the rendered prompt

At first glance the new `<TASK_TRACKING>` block looks repetitive because the repo already talks a lot about planning in system/developer guidance. But inside the actual rendered OpenHands base prompt, there is no equivalent `task_tracker` section. In the final composed prompt, this is mostly net-new guidance.

## Overall conclusion

### What seems worth keeping

- `<TASK_TRACKING>`: new and useful
- most of `<EDITING_CONSTRAINTS>`: partly new
- the cli-mode-safe version of `<PRESENTING_WORK>`: useful
- the stronger end-to-end execution bias in `<WORKFLOW>`: likely the main point of the PR

### What feels somewhat repetitive but acceptable

- `<OUTPUT_VERBOSITY_SPEC>` vs GPT-5 model-specific concision guidance
- `<VALIDATION_LOOP>` vs base `PROBLEM_SOLVING_WORKFLOW`
- parts of `<WORKFLOW>` vs base execution/testing instructions

### My recommendation

For this PR, I would keep the structure mostly as-is and only fix factual mismatches. That keeps the behavioral intent intact while avoiding a larger prompt refactor mid-review.

If we want a follow-up cleanup, the best target is **prompt factoring**, not deleting guidance blindly:

1. move shared GPT-5 communication-style rules into one reusable partial;
2. keep `system_prompt_gpt_5_4.j2` focused on truly incremental behavior;
3. optionally merge validation wording with the existing base workflow to reduce token count.

So: **yes, there is some redundancy; no, I do not think the prompt is currently contradictory; and the only clear bug was the unconditional same-machine claim, which is now fixed.**
