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

# Start agent-server in Docker
docker run -d --name agent-server -p 8010:8000 \
  -v /tmp/agent-workspace:/workspace \
  ghcr.io/openhands/agent-server:main-python

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