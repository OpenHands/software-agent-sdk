# OpenAI-compatible gateway

This package contains the agent-server implementation for the OpenAI-compatible API surface under `/v1`.

- `router.py` defines the FastAPI routes and maps OpenAI-style bearer authentication to the existing session key mechanism.
- `models.py` contains the small server-side request models and aliases the reusable OpenAI response models.
- `service.py` translates OpenAI chat completion requests into OpenHands conversations, waits for completion, and returns OpenAI-shaped responses.

The gateway intentionally stays separate from the native agent-server routers so the OpenAI compatibility layer can evolve without mixing protocol translation code into the core REST API modules.
