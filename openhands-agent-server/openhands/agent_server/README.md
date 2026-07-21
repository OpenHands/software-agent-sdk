# OpenHands Agent Server

The OpenHands Agent Server is a minimal REST API and WebSocket server that provides a programmatic interface for interacting with OpenHands AI agents. It uses the local filesystem to store conversations, events, and workspace files, making it ideal for development, testing, and lightweight deployments.

## Features

- **REST API**: Full CRUD operations for conversations and events
- **WebSocket Support**: Real-time communication with agents
- **Local Storage**: File-based storage for conversations and workspace data
- **CORS Support**: Configurable cross-origin resource sharing
- **Authentication**: Optional session-based API key authentication
- **Webhooks**: Configurable webhook notifications for events
- **Auto-reload**: Development mode with automatic code reloading

## Quick Start

### Prerequisites

Before starting the server, make sure to build the project and install dependencies:

```bash
make build
```

### Starting the Server

The server can be started using Python's module execution:

```bash
# Start with default settings (host: 0.0.0.0, port: 8000)
uv run python -m openhands.agent_server

# Start with custom host and port
uv run python -m openhands.agent_server --host localhost --port 3000

# Start with auto-reload (for dev)
uv run python -m openhands.agent_server --reload
```

### Command Line Options

- `--host`: Host to bind to (default: `0.0.0.0`)
- `--port`: Port to bind to (default: `8000`)
- `--reload`: Enable auto-reload

## Configuration

The server can be configured using environment variables or a JSON configuration file.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENHANDS_AGENT_SERVER_CONFIG_PATH` | Path to JSON configuration file | `workspace/openhands_agent_server_config.json` |
| `SESSION_API_KEY` | API key for authentication (optional) | None |
| `OH_SECRET_KEY` | Secret key for encrypting sensitive data (LLM API keys, secrets) in stored conversations. **Required for persistence across restarts.** | None |
| `OH_ALLOW_CORS_ORIGIN_REGEX` | Regular expression for additional allowed CORS origins. Use `https?://.+` to allow any HTTP(S) origin while echoing the concrete origin. | None |
| `OH_TELEMETRY_EXPORTER` | Where events go: `none`, `posthog`, or `http`. See [Telemetry](#telemetry). | `none` |
| `OH_TELEMETRY_POSTHOG_API_KEY` | PostHog project API key. Required by the `posthog` exporter. | None |
| `OH_TELEMETRY_POSTHOG_HOST` | PostHog ingestion host. | `https://us.i.posthog.com` |
| `OH_TELEMETRY_HTTP_ENDPOINT` | Endpoint the `http` exporter POSTs sanitized batches to. | None |
| `OH_TELEMETRY_HTTP_TOKEN` | Bearer token for the `http` exporter, if required. | None |
| `OH_TELEMETRY_CONSENT` | `granted` or `denied`. Seeds or overrides the persisted consent value. | Unset |
| `OH_TELEMETRY_CONSENT_MODE` | `seed` (applies only while consent is `unset`) or `override` (wins over settings). | `seed` |
| `OH_TELEMETRY_SALT` | Key used to pseudonymize conversation ids. Falls back to `OH_SECRET_KEY`, then to a per-process random salt. | None |
| `DO_NOT_TRACK` | Set to `1` to force telemetry off, overriding consent and env. | Unset |

### Configuration File

Create a JSON configuration file (default: `workspace/openhands_agent_server_config.json`):

```json
{
  "session_api_key": "your-secret-api-key",
  "allow_cors_origins": ["https://your-frontend.com"],
  "allow_cors_origin_regex": null,
  "conversations_path": "workspace/conversations",
  "webhooks": [
    {
      "webhook_url": "https://your-webhook-endpoint.com/events",
      "method": "POST",
      "event_buffer_size": 10,
      "num_retries": 3,
      "retry_delay": 5,
      "headers": {
        "Authorization": "Bearer your-webhook-token"
      }
    }
  ]
}
```

### Configuration Options

- **`session_api_key`**: Optional API key for securing the server. If set, all requests must include this key in the `Authorization` header as `Bearer <key>`
- **`allow_cors_origins`**: List of allowed CORS origins (localhost is always allowed)
- **`allow_cors_origin_regex`**: Regular expression for additional allowed CORS origins. Use `https?://.+` to allow any HTTP(S) origin while keeping credential-compatible origin echoing.
- **`webhooks`**: Array of webhook configurations for event notifications

**Note**: Directory configuration (`working_dir`) will be handled at the conversation level rather than globally. These directories are specified when starting a conversation through the API.

### Telemetry

The agent server can emit a small set of **product-analytics** events to PostHog.
It is **disabled by default** and does nothing unless a deployment opts in.

#### This is not the same as the other two observability features

| Feature | What it collects | Where it goes |
|---|---|---|
| **Telemetry** (this section) | Allowlisted lifecycle/failure events. Never prompts, messages, file contents, paths, secrets, request/response bodies, or tracebacks. | PostHog, when configured |
| **LLM completion logging** (`log_completions`) | Full prompts, responses, and raw provider payloads — deliberately high fidelity for debugging. | Local disk only |
| **Laminar / OpenTelemetry tracing** | Distributed traces and spans for latency analysis. | Your OTel/Laminar backend |

Nothing from completion logging or tracing is ever forwarded to telemetry.

#### Consent

There is no deployment "mode" and nothing in the agent-server special-cases a
hosted deployment. It resolves one thing — **effective consent** — and delivery
additionally requires that an exporter is configured.

Consent lives at `misc_settings.telemetry.consent` (`granted` | `denied` |
`unset`), with an optional `misc_settings.telemetry.managed = true` marking the
choice as administrator-managed so a UI can render it read-only. Canvas writes
it through `PATCH /api/settings` like any other frontend preference:

```
PATCH /api/settings
{"misc_settings_diff": {"telemetry": {"consent": "granted"}}}
```

`misc_settings` is otherwise opaque to the agent-server. **This one namespace is
the documented exception**: the frontend still owns the value, the server only
reads it. Nothing else in the container is interpreted.

Because it is ordinary `misc_settings`, consent persists across restarts for
free and needs no settings schema change.

Precedence, highest first:

1. `DO_NOT_TRACK=1` — operator kill switch, overrides everything.
2. `OH_TELEMETRY_CONSENT` when `OH_TELEMETRY_CONSENT_MODE=override`.
3. `misc_settings.telemetry.consent`.
4. The legacy `misc_settings.app_preferences.user_consents_to_analytics` key,
   read only as a fallback so existing users are not reset. Never written.
5. `OH_TELEMETRY_CONSENT` as a seed (the default mode) — applies only while the
   persisted value is `unset`, so an operator default never silently overrules
   an explicit choice.
6. Otherwise `unset`, which is **not** consent.

Revoking takes effect before the settings request returns: delivery stops and
anything already queued is **discarded**, not flushed.

#### Exporters

`OH_TELEMETRY_EXPORTER` selects the transport:

- **`none`** (default) — nothing is delivered. This is what library and headless
  consumers get, and it requires no vendor dependency.
- **`posthog`** — requires `OH_TELEMETRY_POSTHOG_API_KEY` and the optional
  `[posthog]` extra. Without either, telemetry logs one line and stays inactive.
- **`http`** — POSTs sanitized batches to `OH_TELEMETRY_HTTP_ENDPOINT`. Intended
  to front a backend that revalidates auth and consent before forwarding onward,
  so no vendor credentials need to live in the sandbox. Payload shape:

```
POST <endpoint>
Content-Type: application/json
Authorization: Bearer <OH_TELEMETRY_HTTP_TOKEN>   # only when configured

{
  "schema_version": 1,
  "events": [
    {
      "event": "agent_server.conversation_finished",
      "distinct_id": "<user id, or anon:...>",
      "occurred_at": "2026-07-21T10:00:00+00:00",
      "properties": { ... allowlisted properties ... }
    }
  ]
}
```

All three share the same bounded queue, drop-on-failure and capped-shutdown
behaviour.

#### Hosted deployments

A hosted deployment enforces its policy by **seeding** the sandbox's misc
settings before any conversation starts (`consent = granted`, `managed = true`),
not by the agent-server knowing about it. That seeding lives outside this
repository.

#### What is sent

Events: `conversation_started`, `conversation_finished`, `conversation_failed`,
`conversation_error`, `request_failed`, `server_started`, `server_stopped` — all
prefixed `agent_server.`.

Properties are limited to:

- **Envelope** — `schema_version`, `source`.
- **Release / runtime** — `server_version`, `sdk_version`, `tools_version`,
  `build_git_sha`, `build_git_ref`, `python_version`, `platform`,
  `deferred_init`.
- **Conversation shape** — `conversation_ref`, `llm_model_family`, `agent_kind`,
  `tool_count`, `is_fork`, `has_agent_profile`, `workspace_kind`,
  `confirmation_policy`.
- **Outcome (bucketed)** — `terminal_status`, `duration_bucket`,
  `event_count_bucket`, `total_tokens_bucket`, `cost_bucket`.
- **Failure** — `error_class`, `error_category`, `error_fingerprint`,
  `error_origin_module`, `error_origin_lineno`, `is_first_party`,
  `is_terminal`, `tool_name`, `error_id`.
- **Request failure** — `route_template` (the parametrised route, never the
  concrete path), `method`, `status_code`.

Magnitudes are bucketed rather than exact, because a raw count joined with a
timestamp is a re-identification vector. `conversation_ref` is a keyed digest,
never the raw conversation UUID. Exception *messages* and tracebacks are never
read at all — failures are grouped by a fingerprint computed from the exception
type and first-party module/line pairs.

#### Identity

Events use the deployment-supplied `user_id` verbatim as the PostHog
`distinct_id`, so they attach to the person your product already identified.
The exporter only ever calls `capture()` — never `identify()`, `alias()`, or
`group_identify()` — so it cannot create a duplicate identity or irreversibly
merge two. Without a `user_id`, an in-memory `anon:<hex>` id is used that resets
on restart and is flagged so PostHog creates no person profile.

#### Installation

The PostHog client is an optional extra:

```bash
pip install 'openhands-agent-server[posthog]'
```

Without it, telemetry logs one warning and stays inactive; the server starts
normally.

### Secret Encryption

The server encrypts sensitive data (such as LLM API keys and conversation secrets) when storing conversations to disk. To enable this encryption and ensure secrets persist across server restarts, you **must** set the `OH_SECRET_KEY` environment variable.

#### Setting OH_SECRET_KEY

```bash
# Generate a secure random key (recommended)
export OH_SECRET_KEY=$(openssl rand -hex 32)

# Or set a custom key
export OH_SECRET_KEY="your-secret-key-here"
```

**Important Security Notes:**
- Use a strong, randomly generated key with at least 256 bits of entropy
- Store this key securely (e.g., in a secrets manager or environment variable)
- **If you change this key, previously encrypted secrets cannot be decrypted**
- Without `OH_SECRET_KEY`, secrets will be redacted (not encrypted) and will be lost on restart

#### What Gets Encrypted

The following fields are encrypted when `OH_SECRET_KEY` is set:
- LLM API keys (`agent.llm.api_key`)
- AWS credentials (`agent.llm.aws_access_key_id`, `agent.llm.aws_secret_access_key`)
- Conversation secrets (from the `secrets` field in conversation requests)

#### Behavior Without OH_SECRET_KEY

If `OH_SECRET_KEY` is not set:
- The server will log a warning: `⚠️ OH_SECRET_KEY was not defined. Secrets will not be persisted between restarts.`
- Secrets will be redacted (masked) in stored conversations
- When the server restarts, encrypted secrets cannot be decrypted and will be `None`
- Conversations will need to be recreated with fresh API keys

### Webhook Configuration

Each webhook can be configured with:
- **`webhook_url`**: The endpoint URL to receive event notifications
- **`method`**: HTTP method (POST, PUT, or PATCH)
- **`event_buffer_size`**: Number of events to buffer before sending (default: 10)
- **`num_retries`**: Number of retry attempts on failure (default: 3)
- **`retry_delay`**: Delay between retries in seconds (default: 5)
- **`headers`**: Custom headers to include in webhook requests

## API Documentation

Once the server is running, you can access the interactive OpenAPI documentation at:

```
http://localhost:8000/docs
```

This provides a complete reference for all available endpoints, request/response schemas, and allows you to test the API directly from your browser.

### Key API Endpoints

- **`GET /conversations/search`**: Search and list conversations
- **`POST /conversations`**: Create a new conversation
- **`GET /conversations/{conversation_id}`**: Get conversation details
- **`DELETE /conversations/{conversation_id}`**: Delete a conversation
- **`GET /conversations/{conversation_id}/events`**: Get events for a conversation
- **`POST /conversations/{conversation_id}/events`**: Send a message to the agent
- **`WebSocket /conversations/{conversation_id}/events/socket`**: Real-time event streaming

### Event schema compatibility

The event endpoints use extensible discriminated unions in their OpenAPI
response schemas. New event, action, observation, or tool variants may be added
over time as the platform grows.

If you build a generated or hand-written client, treat discriminator values
such as `kind` as open-ended: **skip or ignore unknown variants instead of
assuming the current set is exhaustive**. This keeps clients
forward-compatible when the server starts returning newer event types.


## WebSocket Communication

The server supports WebSocket connections for real-time communication with agents:

```javascript
const ws = new WebSocket('ws://localhost:8000/conversations/{conversation_id}/events/socket');

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    console.log('Received event:', data);
};

// Send a message to the agent
ws.send(JSON.stringify({
    type: 'message',
    content: 'Hello, agent!'
}));
```

## Directory Structure

The server creates and manages the following directory structure:

```
workspace/
├── openhands_agent_server_config.json    # Configuration file
├── conversations/               # Conversation storage
│   ├── {conversation_id}/
│   │   ├── metadata.json       # Conversation metadata
│   │   └── events.jsonl        # Event log
└── project/                    # Agent workspace
    └── (agent files and outputs)
```

## Development

For development, the server runs with auto-reload enabled by default. Any changes to the source code will automatically restart the server.

### Running Tests

```bash
# Run all agent server tests
uv run pytest tests/agent_server/

# Run with coverage
uv run pytest tests/agent_server/ --cov=openhands.agent_server
```

## Security Considerations

- **Authentication**: Use `session_api_key` in production environments
- **Secret Encryption**: Always set `OH_SECRET_KEY` in production to encrypt sensitive data
- **CORS**: Configure `allow_cors_origins` appropriately for your use case
- **Network**: The server binds to `0.0.0.0` by default - restrict access as needed
- **File System**: The server has full access to the configured workspace directory

## Troubleshooting

### Common Issues

1. **Port already in use**: Change the port using `--port` option
2. **Permission denied**: Ensure the user has write access to the workspace directory
3. **Configuration not found**: Check the `OPENHANDS_AGENT_SERVER_CONFIG_PATH` environment variable
4. **CORS errors**: Add your frontend domain to `allow_cors_origins`
5. **LLM API keys are None after restart**: This happens when `OH_SECRET_KEY` is not set or has changed. Set `OH_SECRET_KEY` before starting the server to encrypt and persist secrets. Note: If you change the key, previously encrypted secrets cannot be decrypted.

### Logs

The server logs important events to stdout. For debugging, check:
- Server startup messages
- Configuration loading
- API request/response logs
- WebSocket connection events
