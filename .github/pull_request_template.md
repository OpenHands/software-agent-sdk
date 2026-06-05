HUMAN:
Adds error_id to logs for improved observability and debugging of 500 errors.

- [x] A human has tested these changes.

---

## Why

When the agent-server returns a 500, for example the intermittent `POST /api/conversations` failures reported in OpenHands/software-agent-sdk#3515, callers currently receive an opaque `{\"detail\": \"Internal Server Error\"}` response.

The server already logs the full traceback from `_unhandled_exception_handler`, but the response has no shared identifier that lets a caller or support engineer tie that specific 500 back to the matching server-side log line. That is especially painful for ephemeral runtimes, where logs may be short-lived.

This PR is intentionally scoped to observability. It does **not** fix the underlying conversation-start failure; it makes the next occurrence diagnosable.

Refs OpenHands/software-agent-sdk#3515.

## Summary

- Generate a per-request `error_id` with `uuid4().hex` in the unhandled-exception handler.
- Include `error_id` in the 500 response body and in the corresponding server log line for both plain exceptions and `ExceptionGroup` handling.
- Add regression coverage that verifies 500 responses include an `error_id` and that each request receives a unique value.

## Scope

OpenHands/software-agent-sdk#3517 owns the webhook event serialization fix. This PR has been cleaned up to remain independent of that work and should only diff:

- `openhands-agent-server/openhands/agent_server/api.py`
- `tests/agent_server/test_unhandled_exception_error_id.py`

## Issue Number

OpenHands/software-agent-sdk#3515

## How to Test

```bash
uv run pytest tests/agent_server/test_unhandled_exception_error_id.py
```

Validated on this branch with:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run pytest tests/agent_server/test_unhandled_exception_error_id.py
UV_CACHE_DIR=/private/tmp/uv-cache uv run pre-commit run --files openhands-agent-server/openhands/agent_server/api.py tests/agent_server/test_unhandled_exception_error_id.py
```

## Type

- [x] Feature

## Notes

- `error_id` is a random opaque identifier; no request data is embedded in it.
- The existing traceback logging is preserved. The new identifier only correlates the client-visible 500 response with that traceback.

---
_This was created by an AI assistant._

<!-- AGENT_SERVER_IMAGES_START -->
---
**Agent Server images for this PR**

• **GHCR package:** https://github.com/OpenHands/agent-sdk/pkgs/container/agent-server

**Variants & Base Images**
| Variant | Architectures | Base Image | Docs / Tags |
|---|---|---|---|
| java | amd64, arm64 | `eclipse-temurin:17-jdk` | [Link](https://hub.docker.com/_/eclipse-temurin:17-jdk) |
| python | amd64, arm64 | `nikolaik/python-nodejs:python3.13-nodejs22-slim` | [Link](https://hub.docker.com/_/nikolaik/python-nodejs:python3.13-nodejs22-slim) |
| golang | amd64, arm64 | `golang:1.21-bookworm` | [Link](https://hub.docker.com/_/golang:1.21-bookworm) |


**Pull (multi-arch manifest)**
```bash
# Each variant is a multi-arch manifest supporting both amd64 and arm64
docker pull ghcr.io/openhands/agent-server:6aa97c0-python
```

**Run**
```bash
docker run -it --rm \
  -p 8000:8000 \
  --name agent-server-6aa97c0-python \
  ghcr.io/openhands/agent-server:6aa97c0-python
```

**All tags pushed for this build**
```
ghcr.io/openhands/agent-server:6aa97c0-golang-amd64
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-golang-amd64
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-golang-amd64
ghcr.io/openhands/agent-server:6aa97c0-golang_tag_1.21-bookworm-amd64
ghcr.io/openhands/agent-server:6aa97c0-golang-arm64
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-golang-arm64
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-golang-arm64
ghcr.io/openhands/agent-server:6aa97c0-golang_tag_1.21-bookworm-arm64
ghcr.io/openhands/agent-server:6aa97c0-java-amd64
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-java-amd64
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-java-amd64
ghcr.io/openhands/agent-server:6aa97c0-eclipse-temurin_tag_17-jdk-amd64
ghcr.io/openhands/agent-server:6aa97c0-java-arm64
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-java-arm64
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-java-arm64
ghcr.io/openhands/agent-server:6aa97c0-eclipse-temurin_tag_17-jdk-arm64
ghcr.io/openhands/agent-server:6aa97c0-python-amd64
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-python-amd64
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-python-amd64
ghcr.io/openhands/agent-server:6aa97c0-nikolaik_s_python-nodejs_tag_python3.13-nodejs22-slim-amd64
ghcr.io/openhands/agent-server:6aa97c0-python-arm64
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-python-arm64
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-python-arm64
ghcr.io/openhands/agent-server:6aa97c0-nikolaik_s_python-nodejs_tag_python3.13-nodejs22-slim-arm64
ghcr.io/openhands/agent-server:6aa97c0-golang
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-golang
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-golang
ghcr.io/openhands/agent-server:6aa97c0-golang_tag_1.21-bookworm
ghcr.io/openhands/agent-server:6aa97c0-java
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-java
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-java
ghcr.io/openhands/agent-server:6aa97c0-eclipse-temurin_tag_17-jdk
ghcr.io/openhands/agent-server:6aa97c0-python
ghcr.io/openhands/agent-server:6aa97c0b3a964f61ff7a47edb90ddec29d90755b-python
ghcr.io/openhands/agent-server:chore-agent-server-500-error-id-python
ghcr.io/openhands/agent-server:6aa97c0-nikolaik_s_python-nodejs_tag_python3.13-nodejs22-slim
```

**About Multi-Architecture Support**
- Each variant tag (e.g., `6aa97c0-python`) is a **multi-arch manifest** supporting both **amd64** and **arm64**
- Docker automatically pulls the correct architecture for your platform
- Individual architecture tags (e.g., `6aa97c0-python-amd64`) are also available if needed
<!-- AGENT_SERVER_IMAGES_END -->
