<!-- Keep this PR as draft until it is ready for review. -->

<!-- AI/LLM agents:
Do not edit the HUMAN section.
-->

HUMAN:

<!--
Human author: please replace this comment with a short note (at least 20 visible
characters) before marking ready for review.
AI agents: you must not edit this section.
-->

---

AGENT:

## Why

Run OpenHands conversations in one owned, ephemeral local Smol microVM per
workspace through the existing Agent Server protocol.

## Summary

- Add optional `SmolWorkspace`, lazy exports and a locked Smol extra.
- Keep VM ownership, credentials, readiness and bounded rollback inside the
  provider; inherit commands, files, Git and remote conversation transport.
- Add virtualization-free contract tests, opt-in VM tests and examples.

## How to Test

See [.pr/smol-validation.md](.pr/smol-validation.md) for commands, exact runtime
versions, results and remaining acceptance checks. See the
[workspace guide](examples/02_remote_agent_server/smol/README.md) for setup.

Keep this PR in draft until a published Smol wheel includes the companion engine
and Python `publish_atomic` contributions, the dependency minimum is finalized,
and the image/conversation acceptance tests pass on every advertised platform.
The seven-day dependency age policy remains in place.

## Design Doc

[SmolWorkspace design](.pr/smol-design.html). When opening the PR, link this file
through htmlpreview using the contributor fork and branch.

## Type

- [x] Feature

## Notes

The Smol prerequisite spans `smol-machines/smolvm` (engine/control socket) and
`smol-machines/smol` (Python SDK/native binding). No PR has been opened or pushed.
