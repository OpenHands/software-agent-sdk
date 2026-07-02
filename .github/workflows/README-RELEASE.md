# Release Automation Workflows

This document describes the automated release process for the OpenHands Software
Agent SDK. Releases are driven by [release-please](https://github.com/googleapis/release-please)
via the shared [OpenHands/release-actions](https://github.com/OpenHands/release-actions)
reusable workflows.

## Overview

| Workflow | Trigger | Purpose |
|---|---|---|
| **release.yml** | push to `main` / `release/**` | Calls `release-actions`' `release-please.yml`. Maintains a **draft** `chore(main): release X.Y.Z` PR that bumps every package version and aggregates the changelog. Merging it tags `vX.Y.Z` and publishes the GitHub release. |
| **pr.yml** | `pull_request_target` | Calls `release-actions`' `pr-title.yml`. Lints PR titles for Conventional Commits and applies a `type: <type>` label used for release-note grouping. |
| **release-ready.yml** | `pull_request_target: ready_for_review` | Calls `release-actions`' `release-ready.yml`. When the draft release PR is marked ready, labels it `release: ready` + `behavior-test`, `integration-test`, `test-examples`, and notifies Slack. |
| **pypi-release.yml** | `release: published` | Builds and publishes all packages to PyPI. Unchanged. |
| **release-binaries.yml** | `release: published` + push to `main` | Builds/smoke-tests multi-arch agent-server binaries and attaches them to the release. Unchanged. |
| **version-bump-prs.yml** | dispatched by `pypi-release.yml` | Opens downstream version-bump PRs in `OpenHands` and `openhands-cli`. Unchanged. |

## Versioning policy

The next version is derived from Conventional Commit PR titles since the last
release, using release-please's `default` strategy:

- `fix:` → **patch** (e.g. `1.30.0` → `1.30.1`)
- `feat:` → **minor** (e.g. `1.30.0` → `1.31.0`)

Per the SDK policy ([AGENTS.md](../../AGENTS.md): *"breaking SDK API changes
require at least a MINOR SemVer bump"*, with a 5-minor-release deprecation
runway), the SDK stays on the `1.x` line — **breaking changes ship as a `feat:`
minor bump, not a major.** Breaking API changes go through the deprecation +
`release-note-required` process, not a Conventional Commit breaking marker.

> ⚠️ Do **not** use the breaking markers `feat!:` / `fix!:` / `BREAKING CHANGE:`.
> release-please's `default` strategy escalates those to a **major** (`2.0.0`)
> bump, which would violate the stay-on-`1.x` policy. The release PR is a draft
> that is reviewed before cutting, so an accidental major would surface in the
> `chore(main): release X.Y.Z` title and can be corrected before it ships.
>
> (`versioning: default` is deliberate: it preserves `fix:` → patch releases.
> The alternative, `always-bump-minor`, would guarantee no major bump but would
> also turn every `fix:` into a minor — dropping patch releases.)

## How to create a release

### 1. Land work with Conventional Commit titles

Every PR merged to `main` must have a [Conventional Commit](https://www.conventionalcommits.org)
title (`feat: …`, `fix: …`, `docs: …`, etc.). `pr.yml` lints this and labels the
PR with its type. PRs are **squash-merged**, so the PR title becomes the commit
release-please reads.

### 2. Review the draft release PR

On every push to `main`, release-please opens/updates a **draft** PR titled
`chore(main): release X.Y.Z`. It:

- bumps the version in `version.txt` and all four `pyproject.toml` files
  (`openhands-sdk`, `openhands-tools`, `openhands-workspace`,
  `openhands-agent-server`) — kept in lockstep;
- aggregates the changelog into the PR body (which becomes the release notes).

Review it and fix any deprecation deadlines if they exist.

### 3. Mark it "Ready for review" to cut the release

Clicking **Ready for review** is the deliberate release-cut signal. It fires
`release-ready.yml`, which labels the PR `release: ready` plus the three
test-trigger labels and posts to Slack:

- `integration-test` and `behavior-test` → `integration-runner.yml`
- `test-examples` → `run-examples.yml`

Confirm those suites pass, and confirm any merged `release-note-required` PRs are
accurately called out in the notes.

### 4. Merge to publish

Merging the release PR:

1. tags the merge commit `vX.Y.Z` and publishes the GitHub release (release notes
   from the PR body);
2. fires **pypi-release.yml** (publish to PyPI) and **release-binaries.yml**
   (attach binaries), both on `release: published`;
3. back-labels every PR included in the release with `released: vX.Y.Z`.

Because release-please acts as a GitHub App (org secrets `RELEASE_APP_ID` /
`RELEASE_APP_PRIVATE_KEY`, already configured org-wide), these downstream
`release: published` and `labeled` events actually fire — the default
`GITHUB_TOKEN` would suppress them.

### 5. Post-release

- Review and merge the auto-created version-bump PRs in `OpenHands` and
  `openhands-cli`.
- Run evaluation on OpenHands Index (manual).
- Announce the release.

## Hotfixes and freezes

release.yml also runs on `release/**` branches, so a maintenance/freeze release
is driven by release-please on that branch exactly like `main`. See the
[release-actions README](https://github.com/OpenHands/release-actions#hotfixing-a-shipped-version)
for the hotfix and freeze flows.

## One-time repository settings

These are configured once on the repo (not committed):

```sh
# Squash-merge so the PR title becomes the commit release-please reads.
gh api -X PATCH repos/OpenHands/software-agent-sdk \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=COMMIT_MESSAGES \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true
```

## Troubleshooting

### The draft release PR bumps the wrong files / no bump

release-please propagates the version to each `pyproject.toml` via the
`x-release-please-version` annotation comment on the `version = "…"` line and the
`extra-files` list in `release-please-config.json`. If a package stops bumping,
check that its annotation comment is intact.

### PyPI publication failed

- Check that `PYPI_TOKEN_OPENHANDS` is configured.
- Verify the version doesn't already exist on PyPI.

### Release binaries failed

See the per-stage guidance in `release-binaries.yml`; release/manual runs can be
re-run against an existing tag via `workflow_dispatch` with the `release_tag`
input.
