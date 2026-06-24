# Publishing Guide

This document explains how to publish the OpenHands TypeScript Client.

## Overview

Releases are driven by
[release-please](https://github.com/googleapis/release-please): the version is
derived from **Conventional-Commit PR titles**, and merging the release PR
release-please maintains cuts the GitHub Release. The package is then published
to **two registries**:

| Registry | Workflow | Auth |
|----------|----------|------|
| **npm** (`registry.npmjs.org`) | `.github/workflows/npm-publish.yml` | OIDC trusted publishing |
| **GitHub Packages** (`npm.pkg.github.com`) | `.github/workflows/publish-github-packages.yml` | `GITHUB_TOKEN` |

The full process, prerequisites, and the centralized reusable workflows
([`OpenHands/release-actions`](https://github.com/OpenHands/release-actions)) are
documented in [`.github/workflows/README-RELEASE.md`](.github/workflows/README-RELEASE.md).

## Automated Publishing (Recommended)

### Prerequisites

- **npm trusted publishing**: The `@openhands/typescript-client` package on npmjs.org must have the `OpenHands/typescript-client` repository configured as a trusted publisher (see [npm docs](https://docs.npmjs.com/trusted-publishers/)).
- **`RELEASE_APP_ID` / `RELEASE_APP_PRIVATE_KEY`**: The release GitHub App credentials — configured as **organization secrets** and inherited org-wide, so there is nothing to create. release-please authors the PR/tag/release with this App token so the `release: published` publish jobs fire.
- **Squash-merge with `PR_TITLE`**: a one-time repo setting so release-please reads each PR title as the commit. See README-RELEASE.md for the `gh api` command.
- **GitHub Token**: Automatically provided by GitHub Actions as `GITHUB_TOKEN` for GitHub Packages.

### Publishing Process

1. **Merge PRs with conventional titles** (`feat:`, `fix:`, …). `pr.yml` lints the title and labels the PR; the labels drive the next version and the release notes.

2. **Merge the release PR.** release-please keeps a `chore(main): release X.Y.Z` PR up to date; merging it bumps `package.json`/`package-lock.json`, tags `vX.Y.Z`, and creates the GitHub Release.

3. **On publish, the automation runs** (triggered by `release: published`):
   - `npm-publish.yml`: publishes to **npm** with provenance.
   - `publish-github-packages.yml`: publishes to **GitHub Packages**.

Both publish workflows can also be run manually with a `version` input for recovery if a publish job fails after the GitHub Release is created.

## Manual Publishing

### Setup

1. **Configure npm for GitHub Packages**:
   ```bash
   npm login --registry=https://npm.pkg.github.com
   # Username: your-github-username
   # Password: your-github-token
   ```

### Publishing Steps

1. **Update version**:

   ```bash
   npm version patch  # or minor, major
   ```

2. **Build the package**:

   ```bash
   npm run build
   ```

3. **Run tests**:

   ```bash
   npm test
   ```

4. **Publish to GitHub Packages**:
   ```bash
   npm publish --registry=https://npm.pkg.github.com --access public
   ```

## Installation Instructions for Users

### From npm (Recommended)

```bash
npm install @openhands/typescript-client
```

### From GitHub Packages

Add to your `.npmrc` file:

```
@openhands:registry=https://npm.pkg.github.com
```

Then install:

```bash
npm install @openhands/typescript-client
```

## Troubleshooting

### Authentication Issues

- **GitHub Packages**: Ensure the `GITHUB_TOKEN` has `packages:write` permission

### Version Conflicts

If you encounter version conflicts, ensure:

- The version in `package.json` matches the git tag
- The version doesn't already exist in the registry

### Build Failures

Common issues:

- TypeScript compilation errors: Fix in source code
- Test failures: Ensure all tests pass before publishing
- Missing dependencies: Run `npm ci` to install exact versions

## Workflow Files

- `.github/workflows/pr.yml`: Lints PR titles (Conventional Commits) and applies `type:` labels. Calls `release-actions/.github/workflows/pr-title.yml@main`.
- `.github/workflows/release.yml`: Runs release-please on push to `main` — maintains the release PR and, on its merge, creates the GitHub Release `vX.Y.Z`. Calls `release-actions/.github/workflows/release-please.yml@main`.
- `.github/workflows/npm-publish.yml`: `release: published` / manual npmjs.org publish workflow (OIDC trusted publishing).
- `.github/workflows/publish-github-packages.yml`: `release: published` / manual GitHub Packages publish workflow.
