# Contributing

Thank you for helping improve the OpenHands Software Agent SDK.

This repo is a foundation. We want the SDK to stay stable and extensible so that many
applications can build on it safely.

Downstream applications we actively keep in mind:
- [OpenHands-CLI](https://github.com/OpenHands/OpenHands-CLI) (client)
- [OpenHands app-server](https://github.com/OpenHands/OpenHands/blob/main/openhands/app_server/README.md) (client)
- [OpenHands Enterprise](https://github.com/OpenHands/OpenHands/blob/main/enterprise/README.md) (client)

The SDK itself has a Python interface. In addition, the
[agent-server](https://docs.openhands.dev/sdk/guides/agent-server/overview) is the
REST/WebSocket server component that exposes the SDK for remote execution and integrations.
Changes should keep both interfaces stable and consistent.

## A lesson we learned (why we care about architecture)

In earlier iterations, we repeatedly ran into a failure mode: needs from downstream applications
(or assumptions) would leak into core logic.

That kind of coupling can feel convenient in the moment, but it tends to create subtle
breakage elsewhere: different environments, different workspaces, different execution modes,
and different evaluation setups.

The architecture of OpenHands V0 was too monolithic to support multiple applications built into it,
as CLI, evaluation scripts, web server were, and built on it, as OpenHands Cloud was.

If you’re interested in the deeper background and lessons learned, see our write-up:
[OpenHands: An Open Platform for AI Software Developers as Generalist Agents](https://arxiv.org/abs/2511.03690)

This SDK exists (as a separate, rebuilt foundation) to avoid that failure mode.

## Principles we review PRs with

We welcome all contributions, big or small, to improve or extend the software agent SDK.

You may find that occasionally we are opinionated about several things:

- **OpenHands SDK is its own thing**: its downstream are client applications.
- **Prefer interfaces over special cases**: if a client needs something, add or improve a
  clean, reusable interface/extension point instead of adding a shortcut.
- **Extensibility over one-off patches**: design features so multiple clients can adopt them
  without rewriting core logic.
- **Avoid hidden assumptions**: don’t rely on particular env vars, workspace layouts, request
  contexts, or runtime quirks that only exist in one app.
  - Workspaces *do* encode environment specifics (local/Docker/remote), but keep those assumptions
    explicit (params + validation) and contained to the `workspace` layer.
- **No client-specific code paths**: avoid logic that only makes sense for one
  downstream app.
  - It’s fine to have multiple workspace implementations; it’s not fine for SDK core behavior to
    branch on whether the caller is CLI/app-server/SaaS. Prefer capabilities/config over app-identity.
- **Keep the agent loop stable**: treat stability as a feature; be cautious with control-flow
  changes and "small" behavior tweaks.
- **Compatibility is part of the API**: if something could break downstream clients, call it
  out explicitly and consider a migration path. We have a deprecation mechanism you may want to use.

If you’re not sure whether a change crosses these lines, please ask early. We’re happy to help think
through the shape of a clean interface.

## Practical pointers

This file is mostly about principles. For the mechanics, please see:
- [AGENTS.md](AGENTS.md) for AI agents
- [DEVELOPMENT.md](DEVELOPMENT.md) for humans
- [openhands-sdk/openhands/sdk/subagent/AGENTS.md](openhands-sdk/openhands/sdk/subagent/AGENTS.md) for file-based subagent discovery, precedence, and task handoff conventions
- `examples/01_standalone_sdk/41_task_tool_set.py` for delegating work to registered subagents with resume support
- `examples/01_standalone_sdk/42_file_based_subagents.py` for programmatic `AgentDefinition` registration

## Design doc for non-trivial PRs

For a non-trivial PR — a new or changed public API (Python or the agent-server REST/WebSocket
surface), a new subsystem, a behavior change in the agent loop, or a migration — a reviewer
often has to reconstruct the design from the diff alone. That is slow, and it is where the
compatibility risks we care about hide. You are encouraged (not required) to add a short design
doc so reviewers grasp the proposal at a glance.

The convention:

1. Write a **self-contained HTML** page (inline CSS/SVG, opens by double-click) that covers the
   code/API design and a **before/after** of your change, grounded to the actual code. Show the
   interface before and after when you touch one — signatures, schemas, or event shapes — and
   state the compatibility impact (additive, breaking, or behind a flag).
2. Commit it under the temporary **`.pr/`** directory, e.g. `.pr/design.html`. This directory is
   for PR-only artifacts and is **removed automatically when the PR is approved**
   (`.github/workflows/pr-artifacts.yml`), so it never lands in `main`.
3. Link it near the top of the PR description via htmlpreview, pointing at the fork and branch the
   PR is opened from so it renders before the PR is merged:

   ```
   https://htmlpreview.github.io/?https://github.com/<your-fork>/<repo>/blob/<your-branch>/.pr/design.html
   ```

Skip this for trivial PRs (a typo, a one-line guard, a dependency bump) — there, a design doc is
just noise. htmlpreview only works for public repos and self-contained pages.

The `pr-design-doc` skill in the [OpenHands extensions](https://github.com/OpenHands/extensions)
repo can generate the page for you.

## Questions / discussion

Join us on Slack: https://openhands.dev/joinslack
