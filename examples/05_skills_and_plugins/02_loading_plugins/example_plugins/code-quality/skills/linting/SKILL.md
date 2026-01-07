---
name: python-linting
description: >
  This skill helps lint Python code using ruff.
  Use when the user asks to "lint", "check code quality", or "fix style issues".
license: MIT
compatibility: Requires Python and ruff
metadata:
  author: openhands
  version: "1.0"
triggers:
  - lint
  - linting
  - code quality
  - style check
  - ruff
---

# Python Linting Skill

This skill provides instructions for linting Python code using ruff.

## How to Lint

Run ruff to check for issues:

```bash
ruff check .
```

To automatically fix issues:

```bash
ruff check --fix .
```

## Common Options

- `--select E,W` - Only check for errors and warnings
- `--ignore E501` - Ignore line length errors
- `--fix` - Automatically fix fixable issues

## Example Output

```
example.py:1:1: F401 [*] `os` imported but unused
example.py:5:5: E302 Expected 2 blank lines, found 1
Found 2 errors (1 fixable).
```
