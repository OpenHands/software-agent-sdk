# Publishing Guide

This document explains how to publish the OpenHands TypeScript Client.

## Overview

The package is published to **two registries** on every version tag:

| Registry | Workflow | Auth |
|----------|----------|------|
| **npm** (`registry.npmjs.org`) | `.github/workflows/npm-publish.yml` | OIDC trusted publishing |
| **GitHub Packages** (`npm.pkg.github.com`) | `.github/workflows/release.yml` | `GITHUB_TOKEN` |

## Automated Publishing (Recommended)

### Prerequisites

- **npm trusted publishing**: The `@openhands/typescript-client` package on npmjs.org must have the `OpenHands/typescript-client` repository configured as a trusted publisher (see [npm docs](https://docs.npmjs.com/trusted-publishers/)).
- **GitHub Token**: Automatically provided by GitHub Actions as `GITHUB_TOKEN` for GitHub Packages.

### Publishing Process

1. **Create and push a version tag**:

   ```bash
   git tag v1.23.3
   git push origin v1.23.3
   ```

2. **Two GitHub Actions run automatically**:
   - `npm-publish.yml`: Tests → builds → publishes to **npm** with provenance
   - `release.yml`: Tests → builds → publishes to **GitHub Packages** → creates a GitHub Release

Only these two workflows should run on version tags. The manual publish workflow (`.github/workflows/publish-github-packages.yml`) is for explicit `workflow_dispatch` recovery only and must not also run on tag pushes, otherwise it can race the release workflow and fail with a duplicate-version conflict.

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

- `.github/workflows/npm-publish.yml`: Tag-triggered npm (npmjs.org) publish workflow (OIDC trusted publishing)
- `.github/workflows/release.yml`: Tag-triggered GitHub Packages publish + GitHub Release workflow
- `.github/workflows/publish-github-packages.yml`: Manual GitHub Packages recovery workflow (`workflow_dispatch` only)
