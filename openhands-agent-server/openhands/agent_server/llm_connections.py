"""Provider Connection endpoints (scaffold, draft).

Introduces a first-class Provider Connection object: connect a vendor once
with one key, pick from its model catalog, auto-create LLM profiles that
reference the connection's key by name (not inline).

Tracking: OpenHands/OpenHands#15492, Linear OSS-5295.
Scope: software-agent-sdk PR1 of the provider-connections plan.

TODO (implementation):
  - Reuse SecretsService to store the connection key as a named secret.
  - GET  /api/llm/connections          -> list connections (masked key)
  - POST /api/llm/connections          -> create: {provider, key, label?}
  - GET  /api/llm/connections/{id}     -> connection + selectable models
  - PATCH /api/llm/connections/{id}    -> rotate key / rename
  - DELETE /api/llm/connections/{id}   -> disconnect (+ optional profile cleanup)
  - POST /api/llm/connections/{id}/validate -> test key, return catalog from
        /api/llm/models?provider={vendor}

Design decisions (see Notion shaping doc + PR body):
  - key is stored per-connection (not per-provider) so a second key for the
    same provider is additive later (multiple-keys-per-provider is deferred).
  - Cloud path (callCloudProxy) must never return the key.
  - No background refresh job in this PR; pull-on-enter / validate covers
    "see new models as soon as supported via API".

This file is intentionally a stub; real router additions land in llm_router.py
and a new llm_connections.py module.
"""
