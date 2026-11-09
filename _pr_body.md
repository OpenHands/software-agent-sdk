The glob Python fallback got brace expansion in #6615; grep's `include` never did, so the same search returned files through ripgrep and nothing through the fallback.

`GrepAction.include` is documented with brace examples:

```python
    include: str | None = Field(
        default=None,
        description=(
            "Optional file pattern to filter which files to search "
            '(e.g., "*.js", "*.{ts,tsx}")'
        ),
    )
```

`_path_matches_filters` matched the pattern with `fnmatch.fnmatch(filename, include_pattern)`, which has no brace support, so `*.{ts,tsx}` matched no filename at all. On a machine with ripgrep the search works; on one without it the same call returns an empty result, which reads as "no matches" rather than "the filter cannot match anything".

Measured before the change, Python backend, three files containing the pattern:

```text
include='*.ts'         -> 1 match(es): ['a.ts']
include='*.{ts,tsx}'   -> 0 match(es): []
include='*.{js,tsx}'   -> 0 match(es): []
```

and after:

```text
include='*.ts'         -> 1 match(es): ['a.ts']
include='*.{ts,tsx}'   -> 2 match(es): ['a.ts', 'b.tsx']
include='*.{js,tsx}'   -> 2 match(es): ['b.tsx', 'c.js']
```

## What changed

- `expand_braces()` moves from `openhands/tools/glob/impl.py` to the shared `openhands/tools/utils/` package, so both tools use one implementation instead of two. `glob` keeps its private name as an alias, which leaves its existing tests untouched.
- `grep`'s `_path_matches_filters` expands the pattern before matching, so `include` behaves the same on both backends.
- Regression test `test_python_backend_expands_brace_include_patterns` forces the Python backend (both availability probes return `False`) and asserts `*.{ts,tsx}` finds `a.ts` and `b.tsx` and not `c.js`.

`tests/tools/grep` + `tests/tools/glob`: 83 passed, 32 skipped. `ruff check` and `ruff format` clean on the four touched files.

The unbalanced-brace case still leaves the pattern alone (`"{a"` matching nothing) — the same behaviour ripgrep has, so there is nothing to align there.
