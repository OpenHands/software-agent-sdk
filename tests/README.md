---
title: OpenHands Agent SDK Tests
description: Test suite structure and execution strategy for the OpenHands Agent SDK. Includes unit tests, integration tests, and CI configuration.
---

# OpenHands Agent SDK Tests

This directory contains the test suite for the OpenHands Agent SDK.

## Test Structure

```
tests/
├── cross/         # Cross-package tests
├── integration/   # Integration tests
├── sdk/           # SDK unit tests
└── tools/         # Tools unit tests
```

## Test Categories

### Integration Tests (`integration`)

End-to-end tests that cover large parts of the code base and are generally slower than other tests.
**CI Execution:** The CI runs those tests nightly. Code changes do not trigger those tests to run.

### Unit Tests (`cross`, `sdk`, `tools`)

Component-specific tests that prevent regressions in core functionality.

**CI Execution:** The CI runs these tests intelligently based on code changes:
- **SDK Tests** (`sdk/`): Run when changes are detected in `openhands-sdk/**` or `tests/sdk/**`
- **Tools Tests** (`tools/`): Run when changes are detected in `openhands-tools/**` or `tests/tools/**`
- **Cross Tests** (`cross/`): Run when changes are detected in any source code or test files
