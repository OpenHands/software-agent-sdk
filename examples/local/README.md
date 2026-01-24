# Local Conversation Examples

This directory contains examples demonstrating how to use the TypeScript SDK's local execution capabilities with OpenRouter for LLM integration.

## Prerequisites

1. **Node.js 18+** - Required for the local workspace implementation
2. **OpenRouter API Key** - Get one at [openrouter.ai](https://openrouter.ai)

## Setup

```bash
# From the typescript-client directory
npm install

# Build the SDK (required before running examples)
npm run build

# Set your OpenRouter API key
export OPENROUTER_API_KEY="your-api-key-here"
```

## Examples

### 1. Simple Task (`simple-task.ts`)

A minimal "hello world" example to verify everything is working.

```bash
npx ts-node examples/local/simple-task.ts
```

### 2. Basic Local Agent (`basic-local-agent.ts`)

Shows how to create a local conversation that can execute commands, read/write files, and interact with the filesystem.

```bash
npx ts-node examples/local/basic-local-agent.ts
```

### 3. Interactive CLI (`interactive-cli.ts`)

An interactive command-line interface for chatting with the local agent.

```bash
npx ts-node examples/local/interactive-cli.ts [optional-working-directory]
```

### 4. Code Review Agent (`code-review-agent.ts`)

An example agent that reviews code in a directory and provides feedback.

```bash
npx ts-node examples/local/code-review-agent.ts /path/to/your/project
```

## Architecture

The local execution stack consists of:

```
┌─────────────────────────────────────┐
│         LocalConversation           │
│  (Agent loop, message handling)     │
├─────────────────────────────────────┤
│           OpenRouterLLM             │
│  (LLM calls via OpenRouter API)     │
├─────────────────────────────────────┤
│          LocalWorkspace             │
│  (Command exec, file operations)    │
└─────────────────────────────────────┘
```

## Available Tools

The local agent has access to these tools:

| Tool | Description |
|------|-------------|
| `execute_command` | Run bash commands in the workspace |
| `read_file` | Read file contents |
| `write_file` | Create or modify files |
| `think` | Log reasoning/brainstorming (no side effects) |
| `finish` | Signal task completion |

## Configuration Options

### LocalConversation Options

```typescript
const conversation = new LocalConversation(agent, workspace, {
  llm: llmInstance,           // Required: ILLM instance
  maxIterations: 50,          // Max agent loop iterations (default: 50)
  systemPrompt: customPrompt, // Custom system prompt (optional)
  callback: (event) => {},    // Event callback (optional)
});
```

### OpenRouterLLM Options

```typescript
const llm = new OpenRouterLLM({
  apiKey: 'your-key',
  defaultModel: 'anthropic/claude-3.5-sonnet',  // or any OpenRouter model
  defaultTemperature: 0.7,
  defaultMaxTokens: 4096,
});
```

## Supported Models

OpenRouter provides access to 300+ models. Some recommended options:

- `anthropic/claude-3.5-sonnet` - Best for coding tasks
- `anthropic/claude-3-haiku` - Fast and cheap
- `openai/gpt-4o` - OpenAI's latest
- `google/gemini-pro-1.5` - Google's model
- `meta-llama/llama-3.1-70b-instruct` - Open source option

See [OpenRouter Models](https://openrouter.ai/models) for the full list.
