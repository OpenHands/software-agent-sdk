# OpenHands Agent Server TypeScript Client

> ⚠️ **ALPHA SOFTWARE WARNING** ⚠️
>
> This TypeScript SDK is currently in **alpha** and is **not stable**. The API may change significantly between versions without notice. This software is intended for early testing and development purposes only.
>
> - Breaking changes may occur in any release
> - Features may be incomplete or contain bugs
> - Documentation may be outdated or incomplete
> - Not recommended for production use
>
> Please use with caution and expect frequent updates.

A TypeScript client library for the OpenHands Agent Server API. Mirrors the structure and functionality of the Python [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk),
but only supports remote conversations.

## ✨ Browser Compatible

This client is **fully browser-compatible** and works without Node.js dependencies. File operations use browser-native APIs like `Blob`, `File`, and `FormData` instead of file system operations. Perfect for web applications, React apps, and other browser-based projects.

## Installation

This package is published to GitHub Packages. You have two installation options:

### Option 1: Configure .npmrc (Recommended)

Add this to your `.npmrc` file:

```
@openhands:registry=https://npm.pkg.github.com
```

Then install normally:

```bash
npm install @openhands/typescript-client
```

### Option 2: Direct install with registry flag

```bash
npm install @openhands/typescript-client --registry=https://npm.pkg.github.com
```

## Quick Start

### Start an AgentServer

You'll need an AgentServer running somewhere for the client to connect to. You can run one in docker:

```bash
docker run -p 8000:8000 -p 8001:8001 \
  -e OH_ENABLE_VNC=false \
  -e SESSION_API_KEY="$SESSION_API_KEY" \
  -e OH_ALLOW_CORS_ORIGINS='["*"]' \
  ghcr.io/all-hands-ai/agent-server:78938ee-python
```

### Creating a Conversation

```typescript
import { Conversation, Agent, Workspace } from '@openhands/typescript-client';

const agent = new Agent({
  llm: {
    model: 'gpt-4',
    api_key: 'your-openai-api-key',
  },
});

// Create a remote workspace
const workspace = new Workspace({
  host: 'http://localhost:3000',
  workingDir: '/tmp',
  apiKey: 'your-session-api-key',
});

const conversation = new Conversation(agent, workspace, {
  callback: (event) => {
    console.log('Received event:', event);
  },
});

// Start the conversation with an initial message
await conversation.start({
  initialMessage: 'Hello, can you help me write some code?',
});

// Start WebSocket for real-time events
await conversation.startWebSocketClient();

// Send a message and run the agent
await conversation.sendMessage('Create a simple Python script that prints "Hello World"');
await conversation.run();
```

### Loading an Existing Conversation

```typescript
// Create a remote workspace for the existing conversation
const workspace = new Workspace({
  host: 'http://localhost:3000',
  workingDir: '/tmp',
  apiKey: 'your-session-api-key',
});

const conversation = new Conversation(agent, workspace, {
  conversationId: 'conversation-id-here',
});

// Connect to the existing conversation
await conversation.start();
```

### Using the Workspace

```typescript
// Execute commands
const result = await conversation.workspace.executeCommand('ls -la');
console.log('Command output:', result.stdout);
console.log('Exit code:', result.exit_code);

// Access lower-level bash APIs from the workspace
const bashCommand = await conversation.workspace.bash.startCommand('ls -la');
const bashEvents = await conversation.workspace.bash.searchEvents({
  command_id__eq: bashCommand.id,
  limit: 20,
});

// Upload a file
const uploadResult = await conversation.workspace.fileUpload(
  './local-file.txt',
  '/remote/path/file.txt'
);

// Download a file
const downloadResult = await conversation.workspace.fileDownload(
  '/remote/path/file.txt',
  './downloaded-file.txt'
);
```

### Server-wide Operations

```typescript
import { ConversationManager } from '@openhands/typescript-client';

const manager = new ConversationManager({
  host: 'http://localhost:3000',
  apiKey: 'your-session-api-key',
});

const serverInfo = await manager.server.getServerInfo();
const providers = await manager.llm.getProviders();
const tools = await manager.tools.listTools();
const acpCount = await manager.acp.countConversations();
```

If you need the lower-level endpoint-specific clients directly, import them from the secondary entrypoint:

```typescript
import { ServerClient, BashClient } from '@openhands/typescript-client/clients';
```

### Working with Events

```typescript
// Access the events list
const events = await conversation.state.events.getEvents();
console.log(`Total events: ${events.length}`);

// Iterate through events
for await (const event of conversation.state.events) {
  console.log(`Event: ${event.kind} at ${event.timestamp}`);
}
```

### Managing Conversation State

```typescript
// Get conversation status
const status = await conversation.state.getAgentStatus();
console.log('Agent status:', status);

// Get conversation stats
const stats = await conversation.conversationStats();
console.log('Total events:', stats.total_events);

// Set confirmation policy
await conversation.setConfirmationPolicy({ type: 'always' });

// Update secrets
await conversation.updateSecrets({
  API_KEY: 'secret-value',
  DATABASE_URL: () => process.env.DATABASE_URL || 'default-url',
});
```

## API Reference

### Conversation

Factory function that creates conversations with OpenHands agents.

#### Constructor

- `new Conversation(agent, workspace, options?)` - Create a new conversation instance

#### Instance Methods

- `start(options?)` - Start the conversation (creates new or connects to existing)

- `sendMessage(message)` - Send a message to the agent
- `run()` - Start agent execution
- `pause()` - Pause agent execution
- `setConfirmationPolicy(policy)` - Set confirmation policy
- `sendConfirmationResponse(accept, reason?)` - Respond to confirmation requests
- `generateTitle(maxLength?, llm?)` - Generate a title for the conversation
- `updateSecrets(secrets)` - Update conversation secrets
- `startWebSocketClient()` - Start real-time event streaming
- `stopWebSocketClient()` - Stop real-time event streaming
- `conversationStats()` - Get conversation statistics
- `close()` - Clean up resources

#### Properties

- `id` - Conversation ID
- `state` - RemoteState instance for accessing conversation state
- `workspace` - RemoteWorkspace instance for command execution and file operations

### RemoteWorkspace

Handles remote command execution and file operations.

#### Methods

- `executeCommand(command, cwd?, timeout?)` - Execute a bash command
- `fileUpload(sourcePath, destinationPath)` - Upload a file
- `fileDownload(sourcePath, destinationPath)` - Download a file
- `gitChanges(path)` - Get git changes for a path
- `gitDiff(path)` - Get git diff for a path
- `close()` - Clean up resources

### RemoteState

Manages conversation state and provides access to events.

#### Properties

- `id` - Conversation ID
- `events` - RemoteEventsList instance

#### Methods

- `getAgentStatus()` - Get current agent execution status
- `getConfirmationPolicy()` - Get current confirmation policy
- `getActivatedKnowledgeSkills()` - Get activated knowledge skills
- `getAgent()` - Get agent configuration
- `getWorkspace()` - Get workspace configuration
- `getPersistenceDir()` - Get persistence directory
- `modelDump()` - Get state as plain object
- `modelDumpJson()` - Get state as JSON string

### RemoteEventsList

Manages conversation events with caching and synchronization.

#### Methods

- `addEvent(event)` - Add an event to the cache
- `length()` - Get number of cached events
- `getEvent(index)` - Get event by index
- `getEvents(start?, end?)` - Get events slice
- `createDefaultCallback()` - Create a default event callback

### WebSocketCallbackClient

Handles real-time event streaming via WebSocket.

#### Methods

- `start()` - Start WebSocket connection
- `stop()` - Stop WebSocket connection

## Types

The library includes comprehensive TypeScript type definitions:

- `ConversationID` - Conversation identifier type
- `Event` - Base event interface
- `Message` - Message interface with content
- `AgentBase` - Agent configuration interface
- `CommandResult` - Command execution result
- `FileOperationResult` - File operation result
- `ConversationStats` - Conversation statistics
- `AgentExecutionStatus` - Agent status enum
- And many more...

## Error Handling

The client includes proper error handling with custom error types:

```typescript
import { HttpError } from '@openhands/typescript-client';

try {
  await conversation.sendMessage('Hello');
} catch (error) {
  if (error instanceof HttpError) {
    console.error(`HTTP Error ${error.status}: ${error.statusText}`);
    console.error('Response:', error.response);
  } else {
    console.error('Unexpected error:', error);
  }
}
```

## Development

### Building

```bash
npm run build
```

### Development Mode

```bash
npm run dev
```

### Linting

```bash
npm run lint
```

### Formatting

```bash
npm run format
```

## Testing

### Unit Tests

Run unit tests (no external dependencies required):

```bash
npm test
```

### Integration Tests

Integration tests require a running agent-server in Docker with a mounted workspace. These tests verify the full functionality against a real server.

#### Prerequisites

1. Docker installed and running
2. LLM API key (e.g., Anthropic or OpenAI)

#### Running Integration Tests Locally

1. Create a workspace directory:

   ```bash
   mkdir -p /tmp/agent-workspace
   chmod 777 /tmp/agent-workspace
   ```

2. Start the agent-server container (software-agent-sdk v1.18.1):

   ```bash
   docker run -d \
     --name agent-server \
     -p 8010:8000 \
     -v /tmp/agent-workspace:/workspace \
     ghcr.io/openhands/agent-server:7c37803-python
   ```

3. Wait for the server to be ready:

   ```bash
   # Check server health
   curl http://localhost:8010/health
   ```

4. Run integration tests:

   ```bash
   export LLM_API_KEY="your-api-key"
   export LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"
   npm run test:integration
   ```

5. Cleanup:
   ```bash
   docker stop agent-server && docker rm agent-server
   ```

#### Environment Variables

| Variable              | Required | Default                 | Description                                                   |
| --------------------- | -------- | ----------------------- | ------------------------------------------------------------- |
| `LLM_API_KEY`         | Yes      | -                       | API key for the LLM provider                                  |
| `LLM_MODEL`           | Yes      | -                       | LLM model name (e.g., `anthropic/claude-sonnet-4-5-20250929`) |
| `LLM_BASE_URL`        | No       | -                       | Custom base URL for LLM API                                   |
| `AGENT_SERVER_URL`    | No       | `http://localhost:8010` | URL of the agent server                                       |
| `HOST_WORKSPACE_DIR`  | No       | `/tmp/agent-workspace`  | Path to workspace on host                                     |
| `AGENT_WORKSPACE_DIR` | No       | `/workspace`            | Path to workspace inside container                            |
| `TEST_TIMEOUT`        | No       | `120000`                | Test timeout in milliseconds                                  |

#### Integration Test Coverage

The integration tests cover:

- **Workspace Operations**: Command execution, file upload/download, round-trip operations
- **Conversation Lifecycle**: Creation, message sending, running agents, pausing
- **Event Streaming**: WebSocket connections, event callbacks, different event types
- **HTTP Client**: Health checks, GET/POST requests, error handling
- **End-to-End Tasks**: File creation/modification/deletion via agent, multi-step tasks

#### CI/CD

Integration tests run automatically in GitHub Actions when:

- Pushing to `main` or `develop` branches
- Opening pull requests to these branches
- Manually triggering the workflow

The workflow requires the following secrets:

- `LLM_API_KEY`: API key for the LLM provider
- `LLM_MODEL` (optional): Override the default model

### Running All Tests

```bash
npm run test:all
```

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
