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

### Configuration File

Create a JSON configuration file (default: `workspace/openhands_agent_server_config.json`):

```json
{
  "session_api_key": "your-secret-api-key",
  "allow_cors_origins": ["https://your-frontend.com"],
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
- **`webhooks`**: Array of webhook configurations for event notifications

**Note**: Directory configuration (`working_dir`) will be handled at the conversation level rather than globally. These directories are specified when starting a conversation through the API.

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
