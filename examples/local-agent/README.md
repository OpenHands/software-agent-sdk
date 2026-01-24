# OpenHands Local Agent

A browser-based agent UI that demonstrates the OpenHands TypeScript SDK's `LocalConversation` class with custom tool calling capabilities using OpenRouter.

## Features

- 🤖 **LocalConversation Agent Loop** - Uses the SDK's built-in agent loop via `LocalConversation.run()`
- 🔧 **Custom Tool Calling** - Demonstrates how to provide custom tools and a tool executor
- 🔐 **OpenRouter Authentication** - Securely connect with your API key
- 💬 **Real-time Chat** - Conversational interface with message history
- 🤖 **Multiple Models** - Switch between Claude, GPT-4, Gemini, Llama, and more
- 🌙 **Dark Mode** - Beautiful dark theme UI

## Quick Start

### Prerequisites

- Node.js 18+
- An OpenRouter API key ([get one here](https://openrouter.ai/keys))

### Setup

```bash
# From the typescript-client directory, build the SDK first
cd /path/to/typescript-client
npm install
npm run build

# Then set up the example app
cd examples/local-agent
npm install
npm run dev
```

### Running

1. Open http://localhost:12001 in your browser
2. Enter your OpenRouter API key
3. Select a model and start chatting!

## How It Works

This example demonstrates using `LocalConversation` with custom tools:

1. **User sends a message** - Creates a `LocalConversation` with custom tools
2. **conversation.start()** - Initializes the conversation with the user's message
3. **conversation.run()** - Runs the agent loop, calling the custom `toolExecutor` for each tool call
4. **Tool execution** - The `toolExecutor` handles `console_log` and `finish` tools
5. **Events** - The callback receives events for display in the UI

### Custom Tools

The agent has access to custom tools defined in the example:

```typescript
const TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'console_log',
      description: 'Logs a message to the console.',
      parameters: {
        type: 'object',
        properties: {
          message: { type: 'string', description: 'The message to log' },
        },
        required: ['message'],
      },
    },
  },
  {
    type: 'function',
    function: {
      name: 'finish',
      description: 'Call this when you have completed the task.',
      parameters: {
        type: 'object',
        properties: {
          message: { type: 'string', description: 'Final message' },
        },
        required: ['message'],
      },
    },
  },
];
```

### Example Prompts

Try these prompts to see the agent in action:

- "Log 'Hello, World!' to the console"
- "Calculate 2 + 2 and log the result"
- "Log the current date and time"
- "Log a greeting in 3 different languages"

## Code Structure

```
examples/local-agent/
├── index.html           # Entry HTML file
├── package.json         # Dependencies
├── tsconfig.json        # TypeScript config
├── vite.config.ts       # Vite bundler config
├── README.md            # This file
└── src/
    ├── main.tsx         # React entry point
    ├── App.tsx          # Main app component
    ├── styles.css       # Global styles
    └── components/
        ├── AgentChatInterface.tsx  # LocalConversation usage & chat UI
        ├── AuthScreen.tsx          # API key input
        └── SettingsModal.tsx       # Model settings
```

## Using LocalConversation with Custom Tools

The key pattern demonstrated in `AgentChatInterface.tsx`:

```typescript
import { LocalConversation, LocalWorkspace, Agent, Tool, ToolCall } from '@openhands/typescript-client';

// Define custom tools
const TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'console_log',
      description: 'Logs a message to the console.',
      parameters: { /* ... */ },
    },
  },
];

// Define a tool executor
const toolExecutor = (toolCall: ToolCall): string => {
  const { name, arguments: argsString } = toolCall.function;
  const args = JSON.parse(argsString);
  
  if (name === 'console_log') {
    console.log(args.message);
    return `Logged: "${args.message}"`;
  }
  
  return `Unknown tool: ${name}`;
};

// Create the conversation with custom tools
const conversation = new LocalConversation(agent, workspace, {
  llm,
  systemPrompt: 'You are a helpful assistant...',
  tools: TOOLS,           // Custom tools
  toolExecutor,           // Custom tool executor
  maxIterations: 10,
  callback: (event) => {
    // Handle events (assistant_message, tool_result, finish, etc.)
  },
});

// Start and run
await conversation.start({ initialMessage: 'Hello!' });
await conversation.run();
```

## Extending with More Tools

To add more tools:

1. Add the tool definition to the `TOOLS` array
2. Add a handler in the `toolExecutor` function

Example adding a `get_time` tool:

```typescript
const TOOLS: Tool[] = [
  // ... existing tools
  {
    type: 'function',
    function: {
      name: 'get_time',
      description: 'Gets the current time',
      parameters: { type: 'object', properties: {} },
    },
  },
];

const toolExecutor = (toolCall: ToolCall): string => {
  const { name } = toolCall.function;
  
  if (name === 'console_log') { /* ... */ }
  if (name === 'get_time') {
    return new Date().toISOString();
  }
  
  return `Unknown tool: ${name}`;
};
```

## Related

- [OpenRouter Documentation](https://openrouter.ai/docs)
- [OpenHands TypeScript SDK](../../README.md)
- [Local Chat Example](../local/) - Simpler chat without tool calling
