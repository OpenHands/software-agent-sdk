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