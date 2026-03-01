# openhands-agent-server

## Development

This package lives in the monorepo root. Typical commands (run from repo root):

- Install deps: `make build`
- Run agent-server tests: `uv run pytest tests/agent_server`

## REST API compatibility & deprecation policy

The agent-server **REST API** (the FastAPI OpenAPI surface under `/api/**`) is a
public API and must remain backward compatible across releases.

### Deprecating an endpoint

When deprecating a REST endpoint:

1. Mark the operation as deprecated in OpenAPI by passing `deprecated=True` to the
   FastAPI route decorator.
2. Add a docstring note that includes:
   - the version it was deprecated in
   - the version it is scheduled for removal in (default: **3 minor releases** later)

Example:

```py
@router.post("/foo", deprecated=True)
async def foo():
    """Do something.

    Deprecated since v1.2.3 and scheduled for removal in v1.5.0.
    """
```

### Removing an endpoint

Removing an endpoint is a breaking change.

- Endpoints must be deprecated for **at least one release** before removal.
- Any breaking REST API change requires at least a **MINOR** SemVer bump.

### CI enforcement

The workflow `Agent server REST API breakage checks` compares the current OpenAPI
schema against the previous `openhands-agent-server` release on PyPI using [oasdiff](https://github.com/oasdiff/oasdiff).

It currently enforces:
- No removal of operations (path + method) unless they were already marked
  `deprecated: true` in the previous release.
- Breaking changes require a MINOR (or MAJOR) version bump.

WebSocket/SSE endpoints are not covered by this policy (OpenAPI only).
