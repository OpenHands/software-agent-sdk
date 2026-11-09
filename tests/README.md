---
title: OpenHands Agent SDK Tests
description: Test suite structure and execution strategy for the OpenHands Agent SDK. Includes unit tests, integration tests, and CI configuration.
---

# OpenHands Agent SDK Tests

This directory contains the test suite for the OpenHands Agent SDK.

## Test Structure

```
tests/
├── agent_server/  # Agent Server unit tests
├── cross/         # Cross-package tests
├── examples/      # Runs the scripts under examples/
├── integration/   # Integration tests
├── sdk/           # SDK unit tests
├── tools/         # Tools unit tests
└── workspace/     # Workspace unit tests
```

## Test Categories

### Integration Tests (`integration`)

End-to-end tests that cover large parts of the code base and are generally slower than other tests.
**CI Execution:** Code changes do not trigger these tests, and they do not run on a schedule. They run when a
maintainer adds the `integration-test`, `behavior-test`, `condenser-test` or `api-compliance-test` label to a
PR, or through a manual dispatch of `integration-runner.yml`.

### Unit Tests (`agent_server`, `cross`, `sdk`, `tools`, `workspace`)

Component-specific tests that prevent regressions in core functionality.

**CI Execution:** The CI runs each suite when its package, its tests, or a package it depends on changes:
- **SDK Tests** (`sdk/`): `openhands-sdk/**` or `tests/sdk/**`
- **Tools Tests** (`tools/`): `openhands-sdk/**`, `openhands-tools/**` or `tests/tools/**`
- **Agent Server Tests** (`agent_server/`): `openhands-sdk/**`, `openhands-tools/**`, `openhands-agent-server/**` or `tests/agent_server/**`
- **Workspace Tests** (`workspace/`): any `openhands-*/**` package or `tests/workspace/**`
- **Cross Tests** (`cross/`): any `openhands-*/**` package, any file under `tests/`, `.github/scripts/remove_pr_artifacts.py`, `.github/workflows/prepare-release.yml` or `.github/workflows/precommit.yml`

Every suite also runs when `pyproject.toml`, `uv.lock` or `.github/workflows/tests.yml` changes.
