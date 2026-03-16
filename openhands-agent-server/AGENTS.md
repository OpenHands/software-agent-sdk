# openhands-agent-server

See the [project root AGENTS.md](../AGENTS.md) for repository-wide policies and workflows.

## Development

This package lives in the monorepo root. Typical commands (run from repo root):

- Install deps: `make build`
- Run agent-server tests: `uv run pytest tests/agent_server`

## PyInstaller data files

When adding non-Python files (JS, templates, etc.) loaded at runtime, add them to `openhands-agent-server/openhands/agent_server/agent-server.spec` using `collect_data_files`.


## REST API compatibility & deprecation policy

The agent-server **REST API** (the FastAPI OpenAPI surface under `/api/**`) is a
public API and must remain backward compatible across releases.

All REST contract breaks need a deprecation notice and a runway of
**5 minor releases** before removing the old contract or making an
incompatible replacement mandatory.

### Deprecating an endpoint

When deprecating a REST endpoint:

1. Mark the operation as deprecated in OpenAPI by passing `deprecated=True` to the
   FastAPI route decorator.
2. Add a docstring note that includes:
   - the version it was deprecated in
   - the version it is scheduled for removal in (default: **5 minor releases** later)
3. Do **not** use `openhands.sdk.utils.deprecation.deprecated` for FastAPI routes.
   That decorator affects Python warnings/docstrings, not OpenAPI, and may be a
   no-op before the declared deprecation version.

Example:

```py
@router.post("/foo", deprecated=True)
async def foo():
    """Do something.

    Deprecated since v1.2.3 and scheduled for removal in v1.7.0.
    """
```

That exact sentence shape is what the CI checks look for, so keep the wording
close to the example above.

### Deprecating a REST contract change

If an existing endpoint's request or response schema needs an incompatible change:

1. Do **not** replace the old contract in place without a migration path.
2. Add a deprecation notice for the old contract in the endpoint documentation and
   release notes, including the deprecated-in version and the removal target.
3. Keep the old contract available for **5 minor releases** while clients migrate.
   Prefer additive schema changes, parallel fields, or a versioned endpoint or
   versioned contract during the runway.
4. Only remove the old contract or make the incompatible shape mandatory after the
   runway has elapsed.

### Removing an endpoint or legacy contract

Removing an endpoint or a previously supported REST contract is a breaking change.

- Endpoints and legacy contracts must have a deprecation notice for **5 minor
  releases** before removal.
- Any release that introduces an allowed breaking REST API change should still be
  at least a **MINOR** SemVer bump, but the version bump does **not** replace the
  5-minor-release deprecation runway.

### CI enforcement

The workflow `Agent server REST API breakage checks` compares the current OpenAPI
schema against the previous `openhands-agent-server` release selected from PyPI,
but generates the baseline schema from the matching git tag under the current
workspace dependency set before diffing with [oasdiff](https://github.com/oasdiff/oasdiff).

It currently enforces:
- FastAPI route handlers must not use `openhands.sdk.utils.deprecation.deprecated`.
- Endpoints that document deprecation in their OpenAPI description must also set
  `deprecated: true`.
- Removed operations must already be marked `deprecated: true` in the previous
  release and must have reached the scheduled removal version documented in the
  baseline OpenAPI description.
- The recognized removal note uses the same wording as the deprecation checks,
  for example: `Deprecated since v1.14.0 and scheduled for removal in v1.19.0.`
- Other breaking REST contract changes fail the check; the replacement must ship
  additively or behind a versioned contract until the 5-minor-release runway has
  elapsed.
- The CI check enforces the deprecation runway, not release-wide SemVer policy.
  Whether a release also needs a MINOR bump still depends on the full scope of
  changes in that release.

Some contract-level migration-path details still rely on human review because
OpenAPI automation cannot fully infer every compatible rollout strategy.

WebSocket/SSE endpoints are not covered by this policy (OpenAPI only).
