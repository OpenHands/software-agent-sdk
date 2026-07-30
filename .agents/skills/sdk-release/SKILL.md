---
name: sdk-release
description: >-
  This skill should be used when the user asks to "release the SDK",
  "prepare a release", "publish a new version", "cut a release",
  "do a release", or mentions the SDK release checklist or release process.
  Guides through the full software-agent-sdk release workflow
  from version bump to PyPI publication, emphasizing human checkpoints.
---

# SDK Release Guide

This skill walks through the software-agent-sdk release process step by step.

The SDK releases via the shared **[OpenHands/release-actions](https://github.com/OpenHands/release-actions)**
(release-please) automation. You no longer pick a version or create a release
branch by hand — release-please keeps a **draft** release PR open, derives the
version from Conventional Commit PR titles, and the deliberate release-cut signal
is marking that PR **Ready for review**. With `freeze-on-ready: true`, that signal
also *freezes* the release onto a `release/<major.minor>` branch so nothing that
lands on `main` afterwards can slip into the release.

> **🚨 CRITICAL**: NEVER mark a release PR **Ready for review** and NEVER merge it
> or publish a GitHub release without the human's explicit approval. Marking ready
> is the release-cut/freeze signal; merging is irreversible (it tags and publishes
> to PyPI). Release is the last line of human defense. Always present the current
> status and ask for confirmation before either action.

## Mental model — two PRs

1. **`chore(main): release X.Y.Z`** — the standing *draft* PR release-please keeps
   open on `main`. This is the **preview** of the next release. You never merge it.
   Marking it Ready is the freeze signal, not the merge.
2. **`chore(release/X.Y): release X.Y.Z`** — the *frozen* release PR release-please
   opens on the `release/X.Y` branch after the freeze. **This is the one you test
   and merge to publish.**

## Phase 1: Review the standing draft release PR

There is nothing to trigger. Find the draft release PR release-please already
maintains and review what it selected:

```bash
gh pr list --repo OpenHands/software-agent-sdk \
  --search 'chore(main): release in:title' --state open \
  --json number,title,url,isDraft
```

Review, before anything else:
- the computed version bump (from merged Conventional Commit titles since the last
  release) — confirm it matches expectations (`feat!:`/`BREAKING CHANGE` → major,
  `feat:` → minor, `fix:` → patch);
- the generated release notes and the **Release checklist** in the PR body
  (deprecation deadlines, `release-note-required` entries, etc.);
- that all four package `pyproject.toml` versions move in lockstep.

### ⏸ Checkpoint — Confirm the version and notes look right

Do not proceed to the freeze until the version and release notes are correct. If
the version is wrong, it almost always means a PR title wasn't Conventional — fix
the history (or a follow-up commit) rather than hand-editing the version.

## Phase 2: Freeze the release (mark the draft PR Ready)

> **🚨 STOP — Ask the human before marking Ready.** This is the release-cut.

Marking the `chore(main): release X.Y.Z` PR **Ready for review** triggers
`release-ready.yml`, which (with `freeze-on-ready: true`):

1. cuts `release/<major.minor>` at the PR's base SHA using the release App token
   (so release-please picks the branch up), and
2. returns the `chore(main)` PR **to draft** so it can't be merged from moving
   `main`; it stays as the next-release preview.

release-please then opens the **frozen** `chore(release/X.Y): release X.Y.Z` PR on
the `release/X.Y` branch. From here on, only the frozen PR matters; leave the
`chore(main)` preview alone.

```bash
# After freeze, locate the frozen release PR
gh pr list --repo OpenHands/software-agent-sdk \
  --search 'chore(release/ release in:title' --state open \
  --json number,title,headRefName,url
```

> If you marked the draft ready and it flipped back to draft with a "Release
> frozen" comment, that is expected — the release moved to the `release/**` PR.

## Phase 3: Run tests on the frozen PR (mark it Ready)

> **🚨 STOP — Ask the human before marking the frozen PR Ready.**

Mark the frozen `chore(release/X.Y)` PR **Ready for review**. Because its base is a
`release/**` branch, the freeze step skips itself and the normal path runs:
`release: ready` plus the SDK's four test-trigger labels are applied and the suites
run. **All four must pass.**

| Label | Suite | What it covers |
|-------|-------|----------------|
| `integration-test` | Integration tests | End-to-end agent scenarios |
| `behavior-test` | Behavior tests | Agent behavioral guardrails |
| `test-examples` | Example tests | All runnable examples in `examples/` |
| `security-scan` | Release security scan | Approval drift and dependency diff checks |

Monitor status:

```bash
gh pr checks <FROZEN_PR_NUMBER> --repo OpenHands/software-agent-sdk
```

### ⏸ Checkpoint — Human judgment on failures

Decide with the team whether each failure is **blocking**, **known/pre-existing**,
or **flaky**. Fix blocking failures on `main` and **backport/cherry-pick onto
`release/X.Y`** (never commit release-only fixes that never reach `main`);
release-please will update the frozen PR. Re-run flaky jobs:

```bash
gh run list --repo OpenHands/software-agent-sdk --branch "release/<major.minor>" --limit 5
gh run rerun <RUN_ID> --repo OpenHands/software-agent-sdk --failed
```

## Phase 4: Run evaluation (optional but recommended)

Trigger an eval against the frozen branch to catch regressions. See the `run-eval`
skill for full details.

```bash
curl -X POST \
  -H "Authorization: token $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "https://api.github.com/repos/OpenHands/software-agent-sdk/actions/workflows/run-eval.yml/dispatches" \
  -d '{
    "ref": "main",
    "inputs": {
      "benchmark": "swebench",
      "sdk_ref": "release/<major.minor>",
      "eval_limit": "50",
      "reason": "Pre-release eval for v<version>",
      "allow_unreleased_branches": "true"
    }
  }'
```

### ⏸ Checkpoint — Evaluate results

Compare against the previous release. Significant score drops should block.

## Phase 5: Merge the frozen release PR

> **🚨 STOP — Do NOT merge without explicit human approval.**
> Merge the **frozen** `chore(release/X.Y)` PR — never the `chore(main)` preview.
> Merging is irreversible: it tags `vX.Y.Z` and triggers the full publish pipeline.

Once the human approves:

```bash
gh pr merge <FROZEN_PR_NUMBER> --repo OpenHands/software-agent-sdk --merge
```

## Phase 6: Automated release pipeline (no action needed)

Merging the frozen PR makes release-please tag and publish, then:

1. release-please creates the GitHub release with tag `v<version>` and notes.
2. **`pypi-release.yml`** triggers on `release: published` and publishes all four
   packages to PyPI:
   - `openhands-sdk`
   - `openhands-tools`
   - `openhands-workspace`
   - `openhands-agent-server`
3. **`release-binaries.yml`** and the downstream version-bump automation run from
   the same `release: published` event.

### ⏸ Checkpoint — Verify PyPI publication

```bash
for pkg in openhands-sdk openhands-tools openhands-workspace openhands-agent-server; do
  curl -s -o /dev/null -w "$pkg: %{http_code}\n" \
    "https://pypi.org/pypi/$pkg/<version>/json"
done
```

All should return `200`.

## Phase 7: Merge back and announce

1. **Merge `release/X.Y` back into `main`** so `main` learns the released
   version/manifest and the `chore(main)` preview advances. Until you do,
   release-please keeps re-proposing the same version on `main`.
2. Compose a Slack message for the human to post, including downstream version
   bump PR links:

```
🚀 *SDK v<version> published to PyPI!*

Version bump PRs:
• <https://github.com/OpenHands/OpenHands/pulls?q=is%3Apr+bump-sdk-<version>|OpenHands>
• <https://github.com/OpenHands/openhands-cli/pulls?q=is%3Apr+bump-sdk-<version>|OpenHands-CLI>

Release: <https://github.com/OpenHands/software-agent-sdk/releases/tag/v<version>|v<version>>
```

See `references/post-release-checklist.md` for details on reviewing downstream PRs.

## Quick Reference — Full Checklist

- [ ] Find and review the standing `chore(main): release X.Y.Z` draft PR
- [ ] Confirm the computed version and release notes are correct
- [ ] **🚨 Get human approval**, then mark the draft PR **Ready** (freeze signal)
- [ ] Locate the frozen `chore(release/X.Y): release X.Y.Z` PR; leave the `chore(main)` preview alone
- [ ] **🚨 Get human approval**, then mark the frozen PR **Ready** (starts tests)
- [ ] Integration tests pass
- [ ] Behavior tests pass
- [ ] Example tests pass
- [ ] Security scan passes
- [ ] Fix blocking failures on `main` and backport to `release/X.Y`
- [ ] (Optional) Evaluation run shows no regressions
- [ ] **🚨 Get human approval**, then merge the **frozen** release PR
- [ ] _(Automated)_ GitHub release created with notes
- [ ] _(Automated)_ Packages published to PyPI
- [ ] _(Automated)_ Downstream version bump PRs created
- [ ] Verify packages appear on PyPI
- [ ] Merge `release/X.Y` back into `main`
- [ ] Send Slack message with downstream version bump PR links
