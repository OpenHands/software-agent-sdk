# Release Automation Workflows

This document describes the automated release workflows for the OpenHands Software Agent SDK.

## Overview

The release process has been automated with two GitHub Actions workflows:

1. **prepare-release.yml** - Prepares a release PR with version updates
2. **pypi-release.yml** - Automatically publishes packages to PyPI when a release is created

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
- [ ] Review and approve the PR

### Step 3: Create the GitHub Release

1. Go to [Releases](https://github.com/OpenHands/software-agent-sdk/releases/new)
2. Click **"Draft a new release"**
3. Configure the release:
   - **Tag**: `vX.Y.Z` (must match the version)
   - **Branch**: `rel-X.Y.Z` (the branch created by the workflow)
   - **Previous tag**: Select the previous release version
4. Click **"Generate release notes"** to auto-generate the changelog
5. Review and edit the release notes as needed
6. Click **"Publish release"**

### Step 4: PyPI Publication (Automated)

Once the release is published, the **pypi-release.yml** workflow will automatically:
- ✅ Build all packages (openhands-sdk, openhands-tools, openhands-workspace, openhands-agent-server)
- ✅ Publish them to PyPI

You can monitor the progress in the [Actions tab](https://github.com/OpenHands/software-agent-sdk/actions/workflows/pypi-release.yml).

### Step 5: Version Bump PRs (Automated)

After successful PyPI publication, the workflow will automatically create PRs to update SDK versions in downstream repositories:

- **[OpenHands](https://github.com/All-Hands-AI/OpenHands)** - Updates `openhands-sdk`, `openhands-tools`, and `openhands-agent-server` versions
- **[OpenHands-CLI](https://github.com/All-Hands-AI/openhands-cli)** - Updates `openhands-sdk` and `openhands-tools` versions

These PRs will:
- Be created automatically with branch name `bump-sdk-X.Y.Z`
- Include links back to the SDK release
- Need to be reviewed and merged by the respective repository maintainers

### Step 6: Post-Release Tasks

- [ ] Merge the release PR to main
- [ ] Review and merge the auto-created version bump PRs in OpenHands and OpenHands-CLI
- [ ] Run evaluation on OpenHands Index (manual step)
- [ ] Announce the release

## Manual PyPI Release (If Needed)

If you need to manually trigger the PyPI release workflow:

1. Go to the [Actions tab](https://github.com/OpenHands/software-agent-sdk/actions)
2. Select **"Publish all OpenHands packages (uv)"** workflow
3. Click **"Run workflow"**
4. Select the branch/tag you want to publish from
5. Click **"Run workflow"**

## Workflow Files

- `.github/workflows/prepare-release.yml` - Automated release preparation
- `.github/workflows/pypi-release.yml` - PyPI package publication

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

## Previous Manual Process

For reference, the previous manual release checklist was:

- [ ] Checkout SDK repo, use `make set-package-version version=x.x.x` to set the version
- [ ] Push to a branch like `rel-x.x.x` and start a PR
- [ ] Fix any "deprecation deadlines" if they exist
- [ ] Tag "integration-tests" and make sure integration test all pass
- [ ] Tag "test-examples" and make sure example checks all pass
- [ ] Draft a new release
- [ ] Use workflow to publish to PyPI on tag `v1.X.X`
- [ ] Evaluation on OpenHands Index

Most of these steps are now automated!
