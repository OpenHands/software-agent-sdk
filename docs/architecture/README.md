# Architecture constraints docs

This directory holds the **durable architecture contracts** for the monorepo.
It complements the AGENTS files instead of replacing them.

## What belongs in `AGENTS.md`

AGENTS files are the **operational layer** for contributors and software agents:

- how to build, test, lint, and validate changes
- package-specific compatibility rules and workflows
- review-time guardrails and common footguns
- pointers to the next-closest guidance file

AGENTS files should stay compact enough to load as working context.

## What belongs in `docs/architecture/`

These docs are the **design-constraint layer**:

- package responsibilities and boundaries
- invariants that should remain true across refactors
- cross-package composition rules
- first-level component maps with the contracts they are expected to uphold
- OCL-like statements when the constraint is simple enough to formalize

If a rule is too deep or too structural for an AGENTS file, it likely belongs here.

## How the AGENTS files compose

| File | Scope | Primary job | Relationship to these docs |
| --- | --- | --- | --- |
| [`AGENTS.md`](../../AGENTS.md) | Whole monorepo | Global workflow, repo memory, package map, compatibility policy | Entry point; links to the deeper docs in this directory |
| [`openhands-sdk/openhands/sdk/AGENTS.md`](../../openhands-sdk/openhands/sdk/AGENTS.md) | Core SDK package | Public SDK API rules, event compatibility, docs workflow | Pairs with [`sdk.md`](./sdk.md) for architectural invariants |
| [`openhands-sdk/openhands/sdk/subagent/AGENTS.md`](../../openhands-sdk/openhands/sdk/subagent/AGENTS.md) | Subagent loader | File-based agent precedence and schema invariants | A specialized deep-dive that supplements `sdk.md` |
| [`openhands-tools/openhands/tools/AGENTS.md`](../../openhands-tools/openhands/tools/AGENTS.md) | Runtime tools package | Tool packaging, public surface, test expectations | Pairs with [`runtime.md`](./runtime.md) |
| [`openhands-workspace/openhands/workspace/AGENTS.md`](../../openhands-workspace/openhands/workspace/AGENTS.md) | Deployable workspace backends | Workspace package API and backend change guardrails | Pairs with [`runtime.md`](./runtime.md) |
| [`openhands-agent-server/AGENTS.md`](../../openhands-agent-server/AGENTS.md) | Agent server | REST/API compatibility and async-safety rules | Pairs with [`runtime.md`](./runtime.md) |
| [`.github/run-eval/AGENTS.md`](../../.github/run-eval/AGENTS.md) | Eval model config | Evaluation model registry and test workflow | Summarized in [`runtime.md`](./runtime.md) |

## Recommended reading order

When changing code:

1. Read the repository root [`AGENTS.md`](../../AGENTS.md).
2. Read the closest package-level AGENTS file.
3. Read the relevant architecture doc in this directory.
4. Only then dive into source files and tests.

That ordering keeps operational guidance in AGENTS and structural guidance here.

## Documents in this directory

- [`sdk.md`](./sdk.md): package-level constraints and first-level component map for `openhands-sdk/openhands/sdk`
- [`runtime.md`](./runtime.md): package-level constraints and component maps for `openhands-tools`, `openhands-workspace`, `openhands-agent-server`, and eval config
