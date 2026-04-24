# Agent Server TypeScript Client

## General Purpose

This repository contains a TypeScript client library for the OpenHands Agent Server. It provides a complete, type-safe interface for interacting with the OpenHands Agent Server API, enabling developers to build applications that can create and manage AI agent conversations, workspaces, and real-time event streams.

The client is designed to mirror the structure and functionality of the Python SDK (`software-agent-sdk`) while providing idiomatic TypeScript/JavaScript APIs with full type safety and modern development tooling.

## Key Features

- **Complete API Coverage**: Implements all endpoints from the OpenHands Agent Server OpenAPI specification
- **Type Safety**: Full TypeScript support with comprehensive interfaces and type definitions
- **Real-time Events**: WebSocket client for streaming conversation events and agent status updates
- **Workspace Management**: File operations, uploads, downloads, and workspace state management
- **Conversation Lifecycle**: Create, start, stop, and manage AI agent conversations
- **Error Handling**: Robust error handling with custom exception classes and retry logic
- **Modern Tooling**: ESLint, Prettier, Jest testing framework, and GitHub Actions CI/CD

## Browser Compatibility Requirement

**All code in this library must be browser-compatible.** This is a hard constraint — the SDK is designed to run in browser environments and must never depend on Node.js-specific APIs.

**Do NOT use:**

- `fs`, `fs/promises`, or any filesystem APIs
- `child_process`, `spawn`, `exec`, or any process execution APIs
- `path` (Node.js module — use string manipulation or URL APIs instead)
- `os`, `net`, `http`, `https`, `stream`, `buffer` (Node.js built-in), `crypto` (Node.js built-in), or any other Node.js built-in modules
- Any npm package that depends on Node.js built-in modules

**DO use:**

- `fetch` for HTTP requests
- `WebSocket` for real-time communication
- Web-standard APIs (`URL`, `Blob`, `File`, `FormData`, `TextEncoder`/`TextDecoder`, etc.)
- Browser-compatible npm packages only

This applies to all source code under `src/`. Test files (`src/__tests__/`) are an exception since they run in Node.js via Jest.

## Source Material

This TypeScript client is based on the following source materials:

### 1. OpenAPI Specification

- **Source**: [OpenHands Docs - Agent SDK OpenAPI](https://github.com/OpenHands/docs/blob/main/openapi/agent-sdk.json)
- **Purpose**: Defines the complete REST API specification for the OpenHands Agent Server
- **Usage**: Used to generate TypeScript interfaces, API client methods, and ensure complete endpoint coverage

### 2. Python SDK Reference Implementation

- **Source**: [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk)
- **Key Components**:
  - `RemoteConversation` class - Main conversation management
  - `RemoteWorkspace` class - Workspace file operations
  - `RemoteState` class - Agent state and configuration management
- **Purpose**: Provides the architectural blueprint and API design patterns
- **Usage**: Ensures consistent class names, method signatures, and behavior across language implementations

### 3. Agent Server Implementation

- **Source**: Located within the `software-agent-sdk` repository as `agent-server`
- **Purpose**: The actual server implementation that this client communicates with
- **Usage**: Reference for understanding expected request/response formats and WebSocket event structures

## Architecture Alignment

The TypeScript client maintains architectural consistency with the Python SDK:

```typescript
// TypeScript Client (this repo)
const conversation = new RemoteConversation(config);
await conversation.start();
await conversation.workspace.write_file('/path/file.txt', 'content');
const state = conversation.state;
```

```python
# Python SDK (reference implementation)
conversation = RemoteConversation(config)
await conversation.start()
await conversation.workspace.write_file('/path/file.txt', 'content')
state = conversation.state
```

### Workspace Architecture

The workspace module follows the Python SDK's architecture with a common interface and multiple implementations:

```
src/workspace/
├── base.ts           # IWorkspace interface defining the contract
├── remote-workspace.ts  # RemoteWorkspace - connects to remote agent server
├── local-workspace.ts   # LocalWorkspace - stub (throws errors, directs to RemoteWorkspace)
└── workspace.ts      # Factory functions and Workspace class (backwards compatible)
```

**IWorkspace Interface**: Defines the common contract for all workspace implementations:

- `executeCommand()` - Execute bash commands
- `fileUpload()` / `fileDownload()` - File operations
- `gitChanges()` / `gitDiff()` - Git operations
- `close()` - Cleanup resources

**RemoteWorkspace**: Fully implemented class that connects to a remote OpenHands agent server via HTTP API.

**LocalWorkspace**: Stub implementation that throws descriptive errors directing users to RemoteWorkspace.

**Factory Functions**:

- `createWorkspace({ type, options })` - Explicit type selection
- `createWorkspaceAuto(options)` - Auto-detect based on presence of `host` option


**Ergonomic API note**: Keep `Workspace`/`RemoteWorkspace` as the main user-facing entry point for remote execution. Lower-level bash endpoint access should hang off `workspace.bash`, while direct endpoint-specific clients belong in the secondary `@openhands/typescript-client/clients` entrypoint instead of the root SDK surface.

### LLM Architecture

The LLM module provides a unified interface for interacting with Large Language Models:

```
src/llm/
├── base.ts           # ILLM interface and message types
├── openrouter-llm.ts # OpenRouterLLM - implementation using @openrouter/sdk
├── llm.ts            # Factory functions and LLM class
└── index.ts          # Module exports
```

**ILLM Interface**: Defines the common contract for all LLM implementations:

- `chatCompletion()` - Send a chat completion request
- `chatCompletionStream()` - Stream a chat completion response
- `generate()` - Simple helper for single-turn generation
- `close()` - Cleanup resources

**OpenRouterLLM**: Implementation using the official `@openrouter/sdk` package, providing access to 300+ models.

**Usage Example**:

```typescript
import { createOpenRouterLLM } from '@openhands/typescript-client';

const llm = createOpenRouterLLM({
  apiKey: 'your-openrouter-api-key',
  defaultModel: 'anthropic/claude-3.5-sonnet',
});

// Simple generation
const response = await llm.generate('Hello, how are you?');

// Full chat completion
const completion = await llm.chatCompletion({
  messages: [{ role: 'user', content: 'Explain quantum computing' }],
  temperature: 0.7,
  maxTokens: 1000,
});

// Streaming
for await (const chunk of llm.chatCompletionStream({ messages })) {
  process.stdout.write(chunk.choices[0]?.delta?.content || '');
}
```

### Conversation Architecture

The conversation module follows the same pattern as workspaces:

```
src/conversation/
├── base.ts               # IConversation interface defining the contract
├── remote-conversation.ts   # RemoteConversation - connects to remote agent server
├── local-conversation.ts    # LocalConversation - local agent execution with LLM
├── conversation.ts       # Factory functions and Conversation class (backwards compatible)
├── remote-state.ts       # State management for remote conversations
└── conversation-manager.ts  # Manager for multiple conversations
```

**IConversation Interface**: Defines the common contract for all conversation implementations:

- `start()` - Initialize or resume a conversation
- `sendMessage()` - Send a message to the agent
- `run()` / `pause()` - Control agent execution
- `generateTitle()` - Generate a title using LLM
- `close()` - Cleanup resources

**RemoteConversation**: Fully implemented class that connects to a remote OpenHands agent server via HTTP/WebSocket.

**LocalConversation**: Fully implemented class for local agent execution:

- Integrates with ILLM interface for LLM communication
- Implements agent loop with tool calling support
- Built-in tools: `execute_command`, `read_file`, `write_file`, `finish`
- Maintains message history for multi-turn conversations
- Supports pause/resume functionality
- Event emission with callback support

**Local Conversation Example**:

```typescript
import { LocalWorkspace, LocalConversation, OpenRouterLLM } from '@openhands/typescript-client';

// Create components
const workspace = new LocalWorkspace({ workingDir: '/path/to/project' });
const llm = new OpenRouterLLM({
  apiKey: 'your-key',
  defaultModel: 'anthropic/claude-3.5-sonnet',
});

// Create and run conversation
const conversation = new LocalConversation({ kind: 'local-agent' }, workspace, {
  llm,
  maxIterations: 50,
});

await conversation.start({ initialMessage: 'List all TypeScript files' });
await conversation.run();
await conversation.close();
```

**Factory Functions**:

**Ergonomic API note**: Keep `ConversationManager` as the main server-scoped entry point. Server/LLM/settings/skills/tools/VSCode/desktop operations should be reachable through manager namespaces such as `manager.server`, `manager.llm`, and `manager.desktop`; ACP-specific operations should be reachable via `manager.acp`.


- `createConversation({ type, agent, workspace, options })` - Explicit type selection
- `createConversationAuto(agent, workspace, options)` - Auto-detect based on workspace type

### Hooks Architecture

The hooks module provides type-safe interfaces for the agent-server's hook system. Hooks are event-driven shell scripts that execute at specific lifecycle events during agent execution, enabling deterministic control over agent behavior.

```
src/hooks/
├── types.ts          # Enums (HookEventType, HookType, HookDecision) and interfaces (HookEvent, HookResult)
├── config.ts         # HookConfig interface and pure functions (matching, normalization, merging)
└── index.ts          # Module exports
```

**Key Design Decisions:**

- **Browser-compatible**: No file I/O or subprocess execution — hooks execute server-side
- **Pure functions**: Config parsing, matching, and merging are all pure functions operating on plain data
- **Type-safe**: Full TypeScript interfaces matching the Python SDK's Pydantic models
- **Server-side execution**: Hook commands run on the agent-server; the client only sends configuration and receives `HookExecutionEvent` via WebSocket

**Integration Points:**

- `CreateConversationRequest.hook_config` - Send hooks when creating a conversation
- `RemoteConversation.loadHooks()` - Load hooks from server's `.openhands/hooks.json`
- `RemoteConversation.getHookConfig()` - Get hooks from current conversation info
- `HookExecutionEvent` - Received via WebSocket when hooks execute server-side

**Event Types:** `HookExecutionEvent` is emitted by the server for each hook execution, providing observability into hook results (success, blocked, stdout, stderr, etc.)

## Development Workflow

1. **API Changes**: When the OpenAPI specification is updated, corresponding TypeScript interfaces and client methods should be updated
2. **Feature Parity**: New features added to the Python SDK should be implemented in this TypeScript client
3. **Testing**: All functionality should be tested against a running OpenHands Agent Server instance
4. **Documentation**: API changes should be reflected in the README.md and example code

## Testing

### Unit Tests

Unit tests are located in `src/__tests__/` and test basic functionality without requiring external services:

```bash
npm test                  # Run unit tests
npm run test:coverage     # Run with coverage report
```

### Integration Tests

Integration tests are in `src/__tests__/integration/` and require a running agent-server in Docker:

```bash
# Set up environment
export LLM_API_KEY="your-api-key"
export LLM_MODEL="anthropic/claude-sonnet-4-5-20250929"

# Start agent-server in Docker (software-agent-sdk v1.18.1)
docker run -d --name agent-server -p 8010:8000 \
  -v /tmp/agent-workspace:/workspace \
  ghcr.io/openhands/agent-server:7c37803-python

# Run integration tests
npm run test:integration
```

Integration tests cover:

- **workspace.integration.test.ts**: Command execution, file upload/download
- **conversation.integration.test.ts**: Conversation lifecycle, messaging, agent execution
- **websocket.integration.test.ts**: Real-time event streaming
- **http-client.integration.test.ts**: HTTP client functionality
- **e2e.integration.test.ts**: End-to-end agent workflows

### CI/CD

The GitHub Action workflow `integration-tests.yml` automatically:

1. Starts an agent-server container with a mounted workspace
2. Runs all integration tests against the real server
3. Reports results and logs on failure

Required GitHub secrets:

- `LLM_API_KEY`: API key for the LLM provider
- `LLM_MODEL` (optional): Override the default model

### CI Image Version

- The integration workflow pins `ghcr.io/openhands/agent-server:7c37803-python`, which corresponds to the `software-agent-sdk` release `v1.18.1`.
- Keep the TypeScript client tests strict against that released server image rather than adding compatibility fallbacks for older prerelease builds.

## Agent Behavior Guidelines

**IMPORTANT**: The agent should NEVER start the server or browse to view the app unless the user explicitly asks for it. This includes:

- Running development servers (e.g., `npm run dev`, `npm start`)
- Opening browsers or navigating to application URLs
- Starting any web servers or applications automatically
- Viewing the running application in a browser

The agent should focus on code development, testing, and documentation tasks. Only when the user specifically requests to run or view the application should the agent start servers or open browsers.

## Related Repositories

- **[OpenHands/OpenHands](https://github.com/OpenHands/OpenHands)**: Core OpenHands application
- **[OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk)**: Python SDK and Agent Server
- **[OpenHands/docs](https://github.com/OpenHands/docs)**: Documentation and OpenAPI specifications

## Usage Context

This client is intended for developers who want to:

- Build web applications that interact with OpenHands agents
- Create Node.js services that manage agent conversations
- Integrate OpenHands capabilities into existing TypeScript/JavaScript applications
- Develop custom frontends for the OpenHands Agent Server

The client abstracts away the complexity of HTTP requests, WebSocket management, and API authentication, providing a clean, type-safe interface for all OpenHands Agent Server functionality.

## Example Application

The `example/` directory contains a React application built with Vite that demonstrates how to integrate the TypeScript SDK into a modern web application. This example serves as both a reference implementation and a verification tool to ensure the SDK works correctly in browser environments.

The example application showcases:

- Proper SDK integration with ES module compatibility
- TypeScript configuration for client-side development
- Build processes that compile the SDK before running the application
- Import verification of all major SDK classes and enums
- Modern React development patterns with Vite tooling

This provides developers with a working template for building their own applications using the OpenHands Agent Server TypeScript Client.
