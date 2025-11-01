# Publishing Guide

This document explains how to publish the OpenHands TypeScript Client to GitHub Packages.

## Overview

The package is published exclusively to **GitHub Packages** for GitHub-native integration.

## Automated Publishing (Recommended)

### Prerequisites

- **GitHub Token**: Automatically provided by GitHub Actions as `GITHUB_TOKEN`

### Publishing Process

1. **Create and push a version tag**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

2. **The GitHub Action will automatically**:
   - Run tests
   - Build the package
   - Update package.json version
   - Publish to GitHub Packages
   - Create a GitHub release with installation instructions

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

### From GitHub Packages

#### Option 1: Configure .npmrc (Recommended)
Add to your `.npmrc` file:
```
@openhands:registry=https://npm.pkg.github.com
```

Then install:
```bash
npm install @openhands/typescript-client
```

#### Option 2: Direct install with registry flag
```bash
npm install @openhands/typescript-client --registry=https://npm.pkg.github.com
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

- `.github/workflows/release.yml`: Main release workflow (GitHub Packages)
- `.github/workflows/publish-github-packages.yml`: GitHub Packages only workflow with manual trigger