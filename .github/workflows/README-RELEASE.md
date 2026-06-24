# Release Automation Workflows

This document describes the automated release process for the OpenHands Agent
Server TypeScript Client. Releases are driven by
[release-please](https://github.com/googleapis/release-please) via OpenHands'
centralized reusable workflows in
[`OpenHands/release-actions`](https://github.com/OpenHands/release-actions).

## Overview

The version is **derived from Conventional-Commit PR titles** — humans never
pick a version, push tags, or draft releases by hand:

1. **pr.yml** — on every PR, lints the title for Conventional Commits
   (`feat:`, `fix:`, …) and applies a matching `type:` label. Calls
   `release-actions/.github/workflows/pr-title.yml@main`.
2. **release.yml** — on every push to `main`, runs release-please, which keeps a
   **release PR** open. That PR accumulates the next version (derived from the
   merged PR titles) and bumps `package.json` + `package-lock.json`. Merging it
   creates the GitHub Release + tag `vX.Y.Z`. Calls
   `release-actions/.github/workflows/release-please.yml@main`.
3. **npm-publish.yml** — on `release: published`, publishes to **npmjs.org**
   (OIDC trusted publishing, with provenance).
4. **publish-github-packages.yml** — on `release: published`, publishes to
   **GitHub Packages**.

```
PR (conventional title) ──▶ pr.yml lints + labels
        │ merge
        ▼
push to main ──▶ release.yml (release-please) ──▶ maintains "release PR"
                                                          │ merge
                                                          ▼
                                            GitHub Release + tag vX.Y.Z
                                                          │ release: published
                                          ┌───────────────┴───────────────┐
                                          ▼                               ▼
                                   npm-publish.yml            publish-github-packages.yml
                                   (npmjs.org)                (GitHub Packages)
```

## Prerequisites (one-time)

- **`RELEASE_APP_ID` / `RELEASE_APP_PRIVATE_KEY`** — the release GitHub App
  credentials. These are configured as **organization secrets** and inherited by
  every repo in the org (`secrets: inherit` in `release.yml` passes them
  through). There is nothing to create here. release-please authors its PR, tag,
  and release with this App token so that the `release: published` event
  actually fires the publish jobs (a release created by `GITHUB_TOKEN` would
  not).
- **Squash-merge configured with `PR_TITLE`** — release-please reads the squash
  commit, which must be the (conventional) PR title. Apply once:
  ```sh
  gh api -X PATCH repos/OpenHands/typescript-client \
    -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=COMMIT_MESSAGES \
    -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
    -F delete_branch_on_merge=true
  ```
- **npm trusted publishing** — `@openhands/typescript-client` on npmjs.org must
  list `OpenHands/typescript-client` as a trusted publisher (already configured
  for `npm-publish.yml`).
- `GITHUB_TOKEN` (automatic) covers GitHub Packages publishing.

## How to Create a New Release

1. **Merge PRs as usual.** Each PR's title must be Conventional Commits — `pr.yml`
   enforces it and labels the PR. `fix:` → patch, `feat:` → minor, `feat!:` /
   `BREAKING CHANGE` → major.
2. **Merge the release PR.** release-please keeps a PR titled
   `chore(main): release X.Y.Z` up to date as commits land. When you're ready to
   ship, merge it. That bumps `package.json`/`package-lock.json`, tags `vX.Y.Z`,
   and creates the GitHub Release (notes grouped by the `type:` labels per
   `.github/release.yml`).
3. **Everything else is automatic.** The published release triggers
   `npm-publish.yml` (npmjs.org) and `publish-github-packages.yml`
   (GitHub Packages) in parallel.

## State files

| File | Purpose |
|---|---|
| `release-please-config.json` | release-please config — `release-type: node` (bumps `package.json`/`package-lock.json`); `include-component-in-tag: false` keeps tags as `vX.Y.Z` |
| `.release-please-manifest.json` | last released version, seeded `{ ".": "1.26.0" }` (matches npm `latest` / the newest `vX.Y.Z` tag) |
| `.github/release.yml` | release-notes categories, grouped by `type:` labels |

## Downstream consumers

Downstream consumers are **not** bumped automatically by this repo. `agent-canvas`
(the one exact-pin consumer) and others update on their own schedule; floating
(`"*"`) and `git+https` consumers always track latest / HEAD.

## Manual / Recovery

- **Re-publish to npm**: Actions → **Publish to npm** → run with the `version`
  input.
- **Re-publish to GitHub Packages**: Actions → **Publish to GitHub Packages** →
  run with the `version` input.
- **Force a release outside the normal flow**: publish a GitHub Release manually
  (the publish jobs trigger on `release: published`), or push a `vX.Y.Z` tag and
  create the release from it.

## Troubleshooting

- **No release PR appears** — confirm the push landed on `main`, that
  `release.yml` ran, and that `RELEASE_APP_ID`/`RELEASE_APP_PRIVATE_KEY` resolve
  (the run fails by design if the App token can't be minted).
- **Release PR opens but publish jobs don't fire** — the release must be created
  by the App token, not `GITHUB_TOKEN`; check that `release.yml` keeps
  `secrets: inherit`.
- **PR title check fails** — the title must be Conventional Commits; see the
  `pr.yml` / `pr-title.yml` allowed types.
- **npm publish failed** — confirm trusted publishing is configured and the
  version does not already exist on npm.

## Tracking the upstream agent-server / SDK version

Separate from cutting a release, the pinned `software-agent-sdk` / agent-server
image (`config.agentServerImage` in `package.json`, plus the
`integration-tests.yml`, `AGENTS.md`, and `README.md` mirrors) is kept current
by the **SDK's own release automation**: when `software-agent-sdk` publishes a
new release, it opens a `bump-agent-server-X.Y.Z` PR here (once the matching
agent-server image is published to GHCR). That PR's CI + integration tests
validate the client against the new server. This is independent of the npm
package version and publishes nothing.

## Workflow Files

- `.github/workflows/pr.yml` — conventional PR-title lint + label (reusable)
- `.github/workflows/release.yml` — release-please release line (reusable)
- `.github/workflows/npm-publish.yml` — npmjs.org publish on `release: published`
- `.github/workflows/publish-github-packages.yml` — GitHub Packages publish on `release: published` (and manual recovery)
