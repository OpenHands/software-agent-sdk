# OpenHands Local Agent

A browser-based agent UI that demonstrates the OpenHands TypeScript SDK's `LocalConversation` class with JavaScript evaluation capabilities using OpenRouter.

## Features

- 🤖 **LocalConversation Agent Loop** - Uses the SDK's built-in agent loop via `LocalConversation.run()`
- 🔧 **JavaScript Eval Tool** - Agent can execute JavaScript code in the browser
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

This example demonstrates using `LocalConversation` with a custom `eval` tool:

1. **User sends a message** - Creates a `LocalConversation` with the eval tool
2. **conversation.start()** - Initializes the conversation with the user's message
3. **conversation.run()** - Runs the agent loop, calling the custom `toolExecutor` for each tool call
4. **Tool execution** - The `toolExecutor` evaluates JavaScript code and returns results
5. **Events** - The callback receives events for display in the UI

### The Eval Tool

The agent has access to a JavaScript evaluation tool:

```typescript
const TOOLS: Tool[] = [
  {
    type: 'function',
    function: {
      name: 'eval',
      description: 'Evaluates JavaScript code in the browser and returns the result.',
      parameters: {
        type: 'object',
        properties: {
          code: { type: 'string', description: 'The JavaScript code to evaluate' },
        },
        required: ['code'],
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

- "What is 2 + 2?"
- "Calculate the factorial of 10"
- "Generate an array of the first 10 fibonacci numbers"
- "What's the current date and time?"
- "Create a function that reverses a string and test it"

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
      name: 'eval',
      description: 'Evaluates JavaScript code in the browser.',
      parameters: {
        type: 'object',
        properties: {
          code: { type: 'string', description: 'The JavaScript code to evaluate' },
        },
        required: ['code'],
      },
    },
  },
];

// Define a tool executor
const toolExecutor = (toolCall: ToolCall): string => {
  const { name, arguments: argsString } = toolCall.function;
  const args = JSON.parse(argsString);
  
  if (name === 'eval') {
    try {
      const result = eval(args.code);
      return JSON.stringify(result, null, 2);
    } catch (error) {
      return `Error: ${error.message}`;
    }
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

## Security Note

⚠️ **Warning**: This example uses `eval()` which can execute arbitrary JavaScript code. This is intentional for demonstration purposes but should be used with caution. In a production environment, consider sandboxing or restricting the code that can be executed.

## Related

- [OpenRouter Documentation](https://openrouter.ai/docs)
- [OpenHands TypeScript SDK](../../README.md)
- [Local Chat Example](../local/) - Simpler chat without tool calling
