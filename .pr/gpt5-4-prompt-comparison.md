# GPT-5.4 prompt comparison: OpenHands vs `openai/codex`

## Scope

This note compares the new OpenHands overlay prompt in `openhands-sdk/openhands/sdk/agent/prompts/system_prompt_gpt_5_4.j2` with the closest inspiration sources I checked in a fresh clone of `openai/codex`:

- `codex-rs/core/gpt_5_2_prompt.md`
- `codex-rs/core/gpt_5_codex_prompt.md`
- `codex-rs/core/prompt_with_apply_patch_instructions.md`

The key structural difference is that the OpenHands file is **not** a full replacement prompt. It starts with `{% include "system_prompt.j2" %}`, so it layers extra GPT-5.x guidance on top of the existing OpenHands system prompt rather than replacing it.

## Section-by-section mapping

| OpenHands section | Closest inspiration source | How it was adapted here |
| --- | --- | --- |
| `system_prompt.j2` include | No direct equivalent; Codex prompt is standalone | OpenHands keeps its existing repo/tool/security/runtime instructions and only overlays GPT-5.x-specific behavior. |
| `<WORKFLOW>` | `gpt_5_2_prompt.md` sections `Autonomy and Persistence` + `Task execution` | We keep the same bias toward end-to-end execution, defaulting to action instead of proposal, and using tools to recover missing context. The OpenHands version is much shorter and drops Codex-specific talk about approvals, persistence through tool failures, and streaming thoughts. |
| `model_specific/openai_gpt/gpt-5.j2` `## Style and presentation` | `gpt_5_2_prompt.md` `Personality`; `gpt_5_codex_prompt.md` `Presenting your work and final message` | The GPT-5-specific snippet now carries the compact answer-size, presentation, and same-machine/CLI wording. That keeps communication-style rules with the existing GPT-5 preamble guidance instead of spreading them across two prompt files. |
| `<EDITING_CONSTRAINTS>` | `gpt_5_codex_prompt.md` `Editing constraints`; `gpt_5_2_prompt.md` apply-patch notes | The ASCII/comment/apply-patch guidance is directly inspired by Codex, but adjusted to OpenHands semantics: `apply_patch` is described as the OpenHands tool with a single `patch` string argument instead of Codex CLI shell usage. |
| `<TASK_TRACKING>` | `gpt_5_2_prompt.md` / `prompt_with_apply_patch_instructions.md` `Planning` | `update_plan` is mapped onto the repository's `task_tracker` tool. The OpenHands version keeps the strong guidance around when to plan, avoiding single-step plans, and keeping status in sync, but omits the long high-quality/low-quality examples. |
| `<VALIDATION_LOOP>` | `gpt_5_2_prompt.md` `Validating your work` | Same intent, reduced to the minimum guidance that fits with the existing OpenHands testing workflow already present in the base prompt. |
| `<FRONTEND_TASKS>` | No close match in the inspected Codex prompts | This looks OpenHands-specific. It preserves local product expectations for frontend design quality rather than coming from the inspiration prompt. |

## What was intentionally *not* ported verbatim

### Already covered by the base OpenHands prompt

These ideas exist in the inspiration prompt, but OpenHands already covers them in `system_prompt.j2`, so the overlay does not need to duplicate them wholesale:

- AGENTS.md memory/instruction handling
- repo exploration before editing
- minimal, focused code changes
- test-before-finish behavior
- browser/service/security policies
- git safety rules

### Kept elsewhere in OpenHands, not in the new overlay

Some Codex guidance is already represented outside `system_prompt_gpt_5_4.j2`:

- GPT-5 preamble and style/presentation behavior lives in `openhands-sdk/openhands/sdk/agent/prompts/model_specific/openai_gpt/gpt-5.j2`
- model-family detection lives in `openhands-sdk/openhands/sdk/llm/utils/model_prompt_spec.py`
- GPT-5 preset tool wiring lives in `openhands-tools/openhands/tools/preset/gpt5.py`

### Deliberately omitted because it is Codex-CLI-specific

I did not find equivalents for these in the OpenHands overlay, which seems intentional:

- sandbox approval flow language
- streaming hidden/internal thinking language
- dirty worktree warnings as a dedicated section
- Codex shell-command examples for `apply_patch`
- the large final-answer formatting block from Codex
- the long planning examples

## Net assessment

The new file is best understood as a **selective transpilation** of Codex prompt ideas into OpenHands conventions:

1. keep the OpenHands base prompt as the source of truth for platform behavior;
2. import only the GPT-5.x workflow biases that seem useful;
3. translate Codex-specific tools (`update_plan`) into OpenHands tools (`task_tracker`);
4. avoid copying CLI-only assumptions when OpenHands can also run remotely.

The only factual mismatch I found was the unconditional same-machine statement, and that is the one place where I changed the template to be `cli_mode`-aware.
