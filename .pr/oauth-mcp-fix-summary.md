# Fix for OAuth MCP Server Token Refresh Issues

## Problem Summary

OAuth-based MCP server connections were failing to refresh expired tokens, causing authentication errors when the access token expired. This affected OAuth providers like Atlassian Rovo and GitLab, which require pre-registered redirect URIs.

## Root Causes

### 1. ✅ Token Expiry Already Fixed in FastMCP 3.2.0

The first reported issue (token expiry not being loaded on init) was **already fixed** in FastMCP 3.2.0. The `_initialize()` method in FastMCP's `OAuth` class properly loads the `token_expiry_time` from storage:

```python
async def _initialize(self) -> None:
    await super()._initialize()
    # ... client info setup ...
    if self.context.current_tokens and self.context.current_tokens.expires_in:
        stored_expiry = await self.token_storage_adapter.get_token_expiry()
        if stored_expiry is not None:
            self.context.token_expiry_time = stored_expiry
        else:
            self.context.update_token_expiry(self.context.current_tokens)
```

This means the token refresh mechanism should work correctly when the expiry time is properly stored and loaded.

### 2. ⚠️ Random Callback Port - FIXED

The critical issue was that OAuth providers requiring pre-registered redirect URIs (like Atlassian Rovo and GitLab) were receiving random callback ports on each authorization attempt. This caused "invalid callback URL" errors because the port didn't match the pre-registered redirect URI.

**Problem:** FastMCP's `OAuth` class defaults to a random available port if `callback_port` is not specified:
```python
self.redirect_port = self._callback_port or find_available_port()
```

**Solution:** Added a `callback_port` field to `MCPOAuthAuthentication` with a default value of `8765`, ensuring consistent redirect URIs across sessions.

## Changes Made

### 1. Added `callback_port` to `MCPOAuthAuthentication`

**File:** `openhands-sdk/openhands/sdk/mcp/config.py`

```python
class MCPOAuthAuthentication(_MCPBaseModel):
    # ... existing fields ...
    callback_port: int | None = Field(
        default=8765,
        description=(
            "Fixed port for OAuth callback server. OAuth providers that require "
            "pre-registered redirect URIs (e.g., Atlassian Rovo, GitLab) need a "
            "consistent callback URL. Defaults to 8765. Set to null for a random port."
        ),
    )
```

### 2. Updated OAuth Factory to Pass callback_port

**File:** `openhands-sdk/openhands/sdk/mcp/utils.py`

```python
def _oauth_auth_from_authentication_config(
    authentication: MCPOAuthAuthentication | None,
    *,
    mcp_oauth_token_storage: AsyncKeyValue | None = None,
) -> OAuth | None:
    # ... validation ...
    return OAuth(
        scopes=authentication.scopes,
        client_name=authentication.client_name or "FastMCP Client",
        token_storage=mcp_oauth_token_storage,
        additional_client_metadata=additional_client_metadata or None,
        callback_port=authentication.callback_port,  # ← NEW
        client_metadata_url=authentication.client_metadata_url,
        client_id=authentication.client_id,
        client_secret=authentication.client_secret.get_secret_value()
        if authentication.client_secret is not None
        else None,
    )
```

### 3. Added Test Coverage

**File:** `tests/agent_server/test_mcp_oauth_store.py`

Added `test_oauth_callback_port_configuration()` to verify:
- Explicit `callback_port` values are passed through correctly
- Default port (8765) is used when not specified
- `null` callback_port results in a random port (original behavior)

## How to Configure OAuth MCP Servers

### Atlassian Rovo Example

```json
{
  "atlassian-rovo": {
    "url": "https://api.atlassian.com/mcp",
    "transport": "http",
    "auth": {
      "strategy": "oauth2",
      "authentication": {
        "type": "oauth",
        "client_id": "your-client-id",
        "client_secret": "your-client-secret",
        "scopes": ["read:jira-work", "read:confluence-content.all"],
        "callback_port": 8765
      }
    }
  }
}
```

**Important:** When registering your OAuth application with Atlassian, use the redirect URI:
```
http://localhost:8765/callback
```

### GitLab Example

```json
{
  "gitlab": {
    "url": "https://gitlab.com/mcp",
    "transport": "http",
    "auth": {
      "strategy": "oauth2",
      "authentication": {
        "type": "oauth",
        "client_id": "your-gitlab-app-id",
        "client_secret": "your-gitlab-secret",
        "scopes": ["api", "read_user"],
        "callback_port": 8765
      }
    }
  }
}
```

**Important:** When registering your OAuth application with GitLab, use the redirect URI:
```
http://localhost:8765/callback
```

### Custom Port

If port 8765 is already in use, you can specify a different port:

```json
{
  "authentication": {
    "type": "oauth",
    "callback_port": 9876,
    ...
  }
}
```

Then register your OAuth application with the redirect URI:
```
http://localhost:9876/callback
```

### Random Port (Not Recommended for Production)

For development/testing only, you can use `null` to get a random port:

```json
{
  "authentication": {
    "type": "oauth",
    "callback_port": null,
    ...
  }
}
```

⚠️ **Warning:** This will not work with OAuth providers that require pre-registered redirect URIs.

## Migration Guide

### For Existing Users

If you have existing OAuth MCP server configurations:

1. **Check your OAuth provider's redirect URI configuration**
   - If it's set to `http://localhost:8765/callback`, no changes needed (the default will work)
   - If it's set to a different port, add `callback_port` to your authentication config

2. **Update your MCP configuration** (if needed)
   ```json
   {
     "authentication": {
       "type": "oauth",
       "callback_port": <your-registered-port>,
       ...
     }
   }
   ```

3. **Re-authenticate** if you were experiencing "invalid callback URL" errors

### For New Users

When setting up a new OAuth MCP server:

1. Decide on a callback port (default: 8765)
2. Register your OAuth application with the provider using redirect URI: `http://localhost:<port>/callback`
3. Configure your MCP server with the same port in `callback_port`

## Testing

All existing tests continue to pass, and a new test verifies the callback_port configuration:

```bash
uv run pytest tests/agent_server/test_mcp_oauth_store.py::test_oauth_callback_port_configuration -v
```

## Related Issue

Fixes: https://github.com/OpenHands/OpenHands/issues/17077
