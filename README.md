# OpenHands Agent Server TypeScript Client

A TypeScript client library for the OpenHands Agent Server API that mirrors the structure and functionality of the Python SDK.

## Features

- **RemoteConversation**: Manage conversations with OpenHands agents
- **RemoteWorkspace**: Execute commands and perform file operations remotely
- **Real-time Events**: WebSocket support for live event streaming
- **Type Safety**: Full TypeScript support with comprehensive type definitions
- **Python SDK Compatibility**: Same class names and method signatures as the Python SDK

## Installation

```bash
npm install @openhands/agent-server-typescript-client
```

## Quick Start

### Creating a Conversation

```typescript
import { Conversation, Agent, Workspace } from '@openhands/agent-server-typescript-client';

const agent = new Agent({
  llm: {
    model: 'gpt-4',
    api_key: 'your-openai-api-key'
  }
});

// Create a remote workspace
const workspace = new Workspace({
  host: 'http://localhost:3000',
  workingDir: '/tmp',
  apiKey: 'your-session-api-key'
});

const conversation = new Conversation(agent, workspace, {
  callback: (event) => {
    console.log('Received event:', event);
  }
});

// Start the conversation with an initial message
await conversation.start({
  initialMessage: 'Hello, can you help me write some code?'
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
  apiKey: 'your-session-api-key'
});

const conversation = new Conversation(agent, workspace, {
  conversationId: 'conversation-id-here'
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
  'API_KEY': 'secret-value',
  'DATABASE_URL': () => process.env.DATABASE_URL || 'default-url'
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
import { HttpError } from '@openhands/agent-server-typescript-client';

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

## License

MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Development
```
docker run -p 8000:8000 -p 8001:8001 \
  -e OH_ENABLE_VNC=false \
  -e SESSION_API_KEY="$SESSION_API_KEY" \
  -e OH_ALLOW_CORS_ORIGINS='["*"]' \
  ghcr.io/all-hands-ai/agent-server:78938ee-python
```

Then start the app
