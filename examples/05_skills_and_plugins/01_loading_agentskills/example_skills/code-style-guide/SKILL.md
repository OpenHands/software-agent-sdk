---
name: code-style-guide
description: >
  Project coding standards and style guidelines. Always follow these
  conventions when writing or reviewing code.
license: MIT
---

# Code Style Guide

Follow these conventions for all code in this project.

## Python

- Use 4 spaces for indentation
- Maximum line length: 88 characters (Black default)
- Use type hints for function signatures
- Prefer f-strings over `.format()` or `%` formatting

## Naming Conventions

- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private members: `_leading_underscore`

## Documentation

- All public functions must have docstrings
- Use Google-style docstrings
- Include type information in docstrings when not using type hints
