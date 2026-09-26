# Release Automation Workflows

This document describes the automated release workflows for the OpenHands Software Agent SDK.

## Overview

The release process uses `create-release.yml` as its sole orchestrator:

1. **prepare-release.yml** prepares a release PR with synchronized package versions.
2. Merging the release PR runs **create-release.yml**, which creates the GitHub release and explicitly dispatches every publisher against its immutable tag.
3. The dispatched workflows publish Python packages, the TypeScript client, agent-server images, and release binaries.

Publisher workflows do not listen for GitHub release events. This avoids relying on events created with `GITHUB_TOKEN`, which GitHub does not use to trigger downstream workflows.

## How to Create a New Release

### Step 1: Trigger the Prepare Release Workflow

1. Go to the [Actions tab](https://github.com/OpenHands/software-agent-sdk/actions)
2. Select **"Prepare Release"** workflow from the left sidebar
3. Click **"Run workflow"** button
4. Enter the version number (e.g., `1.2.3`) - must be in format `X.Y.Z`
5. Click **"Run workflow"**

The workflow will automatically:
- ✅ Create a new branch named `rel-X.Y.Z`
- ✅ Update all package versions using `make set-package-version`
- ✅ Commit the changes
- ✅ Push the branch
- ✅ Create a PR with labels `integration-tests` and `test-examples`

### Step 2: Review the PR

The created PR will include a checklist. Complete the following:

- [ ] Fix any deprecation deadlines if they exist
- [ ] Verify integration tests pass (triggered by `integration-tests` label)
- [ ] Verify example checks pass (triggered by `test-examples` label)
- [ ] Confirm any merged `release-note-required` PRs are accurately called out in the final release notes
- [ ] Review and approve the PR

### Step 3: Merge the Release PR

Merging the release PR runs **create-release.yml**, which:

- creates tag and GitHub release `vX.Y.Z` at the merge commit;
- explicitly dispatches PyPI publication with version `X.Y.Z`;
- explicitly dispatches TypeScript publication to npm and GitHub Packages with version `X.Y.Z`;
- explicitly dispatches versioned agent-server image builds against tag `vX.Y.Z`;
- explicitly dispatches release binaries against tag `vX.Y.Z`.

Each publisher validates that its checked-out package version matches the requested version before publishing. Monitor the dispatched workflows in the [Actions tab](https://github.com/OpenHands/software-agent-sdk/actions).

### Step 4: Release Binaries + Docker Smoke Test (Automated)

**release-binaries.yml** is explicitly dispatched for releases. It also runs on every push to `main` as ongoing smoke coverage. It:

- ✅ Builds the agent-server PyInstaller binary on a 5-runner matrix
  (linux x86_64/arm64, macOS x86_64/arm64, windows x86_64) and smoke-tests each
- ✅ Exports and validates the deterministic public Agent Server contract as
  `openapi.json`, with `info.version` matching the release version
- ✅ Generates a combined `SHA256SUMS` and attaches the binaries and
  `openapi.json` to the GitHub release on release/manual runs
- ✅ Verifies that the multi-arch Docker manifest
  `ghcr.io/openhands/agent-server:<image-tag>-<variant>` published by
  `server.yml` covers both `linux/amd64` and `linux/arm64` for every variant
  (`python`, `java`, `golang`)
- ✅ Pulls each variant on each architecture with `--platform=linux/<arch>`,
  boots the container, and asserts `/health` responds

On `push` events, `<image-tag>` is the 7-character commit SHA and binaries plus
`openapi.json` remain as workflow artifacts only. On release/manual runs,
`<image-tag>` is the release version and the binaries plus `openapi.json` are
uploaded to the GitHub release.

#### Build time / runner expectations

| Stage | Runtime (typical) | Runners |
|---|---|---|
| Binary builds (5-way matrix, parallel) | ~10–15 min on Linux, ~12–18 min on macOS | `ubuntu-24.04`, `ubuntu-24.04-arm`, `macos-15-intel`, `macos-14`, `windows-2022` |
| `publish-binaries` (download + checksum + upload) | ~1–2 min | `ubuntu-24.04` |
| `docker-smoke-test` (6-way matrix, parallel) | Up to 45 min (mostly polling for the docker images) | `ubuntu-24.04` for amd64, `ubuntu-24.04-arm` for arm64 |

#### QEMU / buildx requirements

The smoke test does **not** require QEMU: each (variant, arch) job runs on a
runner whose architecture matches `--platform=linux/<arch>`, so containers run
natively. We do still set up Docker Buildx so we can call
`docker buildx imagetools inspect` on the multi-arch manifest list.

The wait window for the multi-arch manifest is 45 min — long enough to absorb
the full `server.yml` matrix runtime (~25–30 min for `build-and-push-image` +
`merge-manifests`) when this workflow races the corresponding `server.yml` run
for a release tag or main-branch push.

If the matching manifest is already in GHCR, the wait step exits immediately.

### Step 5: Version Bump PRs (Automated)

After successful PyPI publication, the workflow will automatically create PRs to update SDK versions in downstream repositories:

- **[OpenHands-CLI](https://github.com/OpenHands/openhands-cli)** - Updates `openhands-sdk` and `openhands-tools` versions
- **[automation](https://github.com/OpenHands/automation)** - Updates `openhands-sdk` and `openhands-workspace` versions. Opened with a `fix:` title so the repo's release-please cuts a patch release, publishing an `openhands-automation` build pinned to this SDK (which the agent-canvas `sdk-version-sync` check requires).
- **TypeScript client (`clients/typescript`)** - Opens a PR in this repository after both the exact GHCR image and release `openapi.json` are available, updates `config.agentServerImage`, regenerates the checked-in transport types, and includes an API-change summary.

These PRs will:
- Be created automatically with branch name `bump-sdk-X.Y.Z` (`bump-agent-server-X.Y.Z` for typescript-client)
- Include links back to the SDK release
- Include generated Agent Server contract changes for the exact released
  version rather than only changing the image tag
- Need to be reviewed and merged by maintainers

### Step 6: Post-Release Tasks

- [ ] Merge the release PR to main
- [ ] Review and merge the auto-created version bump PRs in OpenHands-CLI, automation, and the TypeScript client (merging the automation PR triggers its release-please release PR; merge that too to publish the pinned `openhands-automation`)
- [ ] Announce the release

## Manual Publication Recovery

To retry a registry publisher, run the latest workflow from `main` and pass the release version without the leading `v`. The workflow checks out the corresponding immutable `vX.Y.Z` tag and validates its package version before publishing. For example, recover `v1.45.0` with version input `1.45.0`; never publish package contents from a moving branch.

The independently retryable publisher workflows are:

- **Publish all OpenHands packages (uv)** for PyPI;
- **Publish TypeScript client to npm**;
- **Publish TypeScript client to GitHub Packages**;
- **Agent Server** for versioned container images;
- **Publish agent-server release artifacts** for binaries and `openapi.json`.

## Workflow Files

- `.github/workflows/prepare-release.yml` - Automated release preparation
- `.github/workflows/create-release.yml` - GitHub release creation and sole publisher orchestrator
- `.github/workflows/pypi-release.yml` - Dispatch-only PyPI package publication
- `.github/workflows/typescript-client-npm-publish.yml` - Dispatch-only npm publication
- `.github/workflows/typescript-client-github-packages-publish.yml` - Dispatch-only GitHub Packages publication
- `.github/workflows/server.yml` - Agent-server builds for PRs, main, and explicit release dispatches
- `.github/workflows/release-binaries.yml` - Multi-arch binary publishing and Docker manifest smoke tests on main and explicit release dispatches

## Troubleshooting

### Version Format Error

If you get a version format error, ensure you're using the format `X.Y.Z` (e.g., `1.2.3`), not `vX.Y.Z`.

### PR Creation Failed

If the PR creation fails, check:
- The branch doesn't already exist
- You have proper permissions
- The `GITHUB_TOKEN` has sufficient permissions

### PyPI Publication Failed

If PyPI publication fails:
- Check that the `PYPI_TOKEN_OPENHANDS` secret is properly configured
- Verify the version doesn't already exist on PyPI
- Check the workflow logs for specific error messages

### Release Binaries Failed

If `release-binaries.yml` fails:
- **Binary build failure**: re-run the failed matrix job; PyInstaller flakes are
  rare but possible. If it persists, the issue is likely in `agent-server.spec`.
- **`docker-smoke-test` timed out waiting for the manifest**: `server.yml` did
  not publish multi-arch images for the matching release tag or commit SHA.
  Check that workflow's corresponding run and re-trigger if needed.
- **`/health` never responded**: open the failing job; the cleanup trap dumps
  the last 100 lines of `docker logs` for the container.
- Release/manual runs can be re-run against an existing tag via
  `workflow_dispatch` with the `release_tag` input (e.g. `v1.20.1`);
  `gh release upload --clobber` makes this safe.

## Previous Manual Process

For reference, the previous manual release checklist was:

- [ ] Checkout SDK repo, use `make set-package-version version=x.x.x` to set the version
- [ ] Push to a branch like `rel-x.x.x` and start a PR
- [ ] Fix any "deprecation deadlines" if they exist
- [ ] Tag "integration-tests" and make sure integration test all pass
- [ ] Tag "test-examples" and make sure example checks all pass
- [ ] Draft a new release
- [ ] Use workflow to publish to PyPI on tag `v1.X.X`

Most of these steps are now automated!
