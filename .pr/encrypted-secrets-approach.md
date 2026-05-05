# Alternate Approach: Encrypted Secrets in Transit

This document outlines an alternate approach to the settings persistence feature
that provides better security for frontend clients while maintaining full
functionality for backend SDK clients.

## Problem Statement

The settings API may be called by two types of clients with different trust levels:

| Client Type | Example | Trust Level | Secret Handling |
|-------------|---------|-------------|-----------------|
| **Backend** | `RemoteWorkspace` SDK client | Trusted (server-to-server) | Plaintext OK |
| **Frontend** | Browser UI | Untrusted | Must NOT see plaintext |

## Key Design Decisions

1. **No optional agent in StartConversationRequest** - `agent` is required
2. **No server-side merging** - what the client sends is what gets used
3. **Two exposure modes** - `encrypted` (safe for FE) and `plaintext` (backend only)
4. **`secrets_encrypted` flag** - tells server to decrypt before use

## Proposed Solution: Two Exposure Modes

### Header Semantics

```
X-Expose-Secrets: encrypted   → Returns cipher-encrypted values (safe for FE)
X-Expose-Secrets: plaintext   → Returns raw secret values (backend only)
(no header)                   → Returns redacted "**********"
```

### Client Usage Patterns

#### Frontend (Browser)

```javascript
// Get settings for display - sees "**********" for secrets
GET /api/settings

// FE needs to start conversation with encrypted secrets:
GET /api/settings  [X-Expose-Secrets: encrypted]
// Response: { "agent_settings": { "llm": { "api_key": "gAAAAABl..." } } }

// Build agent config from response (with encrypted api_key)
// Start conversation with secrets_encrypted=true
POST /api/conversations 
{ 
  "agent": { "llm": { "model": "...", "api_key": "gAAAAABl..." }, ... },
  "workspace": { "kind": "LocalWorkspace", "working_dir": "/workspace" },
  "secrets_encrypted": true
}
// Server decrypts api_key before building the agent
```

#### RemoteWorkspace (Backend SDK)

```python
def get_llm(self, **llm_kwargs) -> LLM:
    response = self.client.get(
        _SETTINGS_API_BASE, 
        headers={"X-Expose-Secrets": "plaintext"}  # Backend-only mode
    )
    # Gets raw api_key, constructs LLM locally
    api_key = response.json()["agent_settings"]["llm"]["api_key"]
    return LLM(api_key=api_key, ...)
```

### StartConversationRequest

```python
class StartConversationRequest:
    agent: Agent  # REQUIRED - no optional, no merging
    workspace: LocalWorkspace  # REQUIRED
    secrets_encrypted: bool = False  # If True, server decrypts secrets
```

When `secrets_encrypted=True`:
1. Server recognizes that secret values in the agent are cipher-encrypted
2. Server decrypts them using its cipher before building the agent
3. This allows secure round-tripping of settings through untrusted clients

## Implementation Changes

| Component | Change Required |
|-----------|-----------------|
| **Settings Router (`settings_router.py`)** | Parse header value as `encrypted` / `plaintext` / absent |
| **Serialization Context** | `expose_secrets="encrypted"` → use cipher; `expose_secrets="plaintext"` → expose raw |
| **`pydantic_secrets.serialize_secret()`** | Handle new `"encrypted"` context value |
| **StartConversationRequest** | Add `secrets_encrypted: bool = False` field (agent stays required) |
| **Conversation Service** | If `secrets_encrypted=True`, decrypt agent secrets before building |
| **RemoteWorkspace.get_llm()** | Use header `X-Expose-Secrets: plaintext` |

## Security Properties

1. **Defense in depth**: Even if frontend accidentally uses expose header, it gets
   encrypted values, not plaintext
2. **Cipher stays server-side**: Only the agent-server has the cipher key
3. **Explicit trust levels**: Header value clearly indicates intended use case
4. **No server-side merging**: Simpler, more predictable behavior

## Flow Diagrams

### Frontend Flow (Encrypted)
```
FE → GET /api/settings [X-Expose-Secrets: encrypted]
   ← { llm: { api_key: "gAAAAA..." } }  (encrypted)

FE → POST /api/conversations { agent: {..., api_key: "gAAAAA..."}, secrets_encrypted: true }
   Server decrypts api_key before use
   ← ConversationInfo
```

### Backend Flow (Plaintext)
```
SDK → GET /api/settings [X-Expose-Secrets: plaintext]
    ← { llm: { api_key: "sk-..." } }  (plaintext)

SDK → Construct LLM locally
SDK → POST /api/conversations { agent: {...}, secrets_encrypted: false }
    ← ConversationInfo
```

## Open Questions

1. **What to do with `X-Expose-Secrets: true`?**
   - Current implementation: Treat as `encrypted` (safe default)

2. **Should `secrets_encrypted` be auto-detected?**
   - Could detect cipher-encrypted strings by format (Fernet tokens start with `gAAAAA`)
   - Explicit flag is clearer and more reliable
   - Current implementation: Explicit flag only
