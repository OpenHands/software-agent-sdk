# OpenAI-compatible gateway

This package contains the agent-server implementation for the OpenAI-compatible API surface under `/v1`.

- `router.py` defines the FastAPI routes and maps OpenAI-style bearer authentication to the existing session key mechanism.
- `models.py` contains the small server-side request models and aliases the reusable OpenAI response models.
- `service.py` translates Chat Completions and Responses requests into OpenHands
  conversations, waits for completion, and returns OpenAI-shaped responses.

`POST /v1/responses` starts a fresh conversation when `previous_response_id` is
absent. This is the default, stateless client flow and works with `store: false`.
Clients that want server-owned continuity can pass the opaque response ID from a
previous call; each response ID identifies one turn while resolving to the
underlying OpenHands conversation. Responses streaming and caller-executed tool
calls are intentionally outside the initial compatibility surface.

The gateway intentionally stays separate from the native agent-server routers so the OpenAI compatibility layer can evolve without mixing protocol translation code into the core REST API modules.
