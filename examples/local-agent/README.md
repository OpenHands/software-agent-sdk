# OpenHands Local Agent

A browser-based agent UI that demonstrates the OpenHands TypeScript SDK with tool calling capabilities using OpenRouter.

## Features

- 🤖 **Agent Loop** - Full agent loop that handles tool calls and responses
- 🔧 **Tool Calling** - Demonstrates LLM tool/function calling with a `console_log` tool
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

This example demonstrates a complete agent loop with tool calling:

1. **User sends a message** - The message is added to the conversation history
2. **LLM processes with tools** - The LLM can choose to call the `console_log` tool
3. **Tool execution** - When the LLM calls a tool, it's executed locally
4. **Tool results** - Results are sent back to the LLM for further processing
5. **Loop continues** - The agent continues until no more tool calls are needed

### The `console_log` Tool

The agent has access to a simple `console_log` tool that logs messages to the browser console:

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
          message: {
            type: 'string',
            description: 'The message to log to the console',
          },
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
        ├── AgentChatInterface.tsx  # Agent loop & chat UI
        ├── AuthScreen.tsx          # API key input
        └── SettingsModal.tsx       # Model settings
```

## Agent Loop Implementation

The core agent loop is implemented in `AgentChatInterface.tsx`:

```typescript
const runAgentLoop = async (conversationMessages: ChatMessage[]): Promise<Message[]> => {
  const newMessages: Message[] = [];
  let currentMessages = [...conversationMessages];
  
  for (let i = 0; i < maxIterations; i++) {
    // Call LLM with tools
    const response = await llm.chatCompletion({
      messages: currentMessages,
      model,
      tools: TOOLS,
      toolChoice: 'auto',
    });

    const choice = response.choices[0];
    const assistantMessage = choice.message;
    
    // If no tool calls, we're done
    if (!assistantMessage.tool_calls || assistantMessage.tool_calls.length === 0) {
      break;
    }

    // Execute tool calls and add results
    for (const toolCall of assistantMessage.tool_calls) {
      const result = executeToolCall(toolCall);
      currentMessages.push({
        role: 'tool',
        content: result,
        tool_call_id: toolCall.id,
      });
    }
  }
  
  return newMessages;
};
```

## Extending with More Tools

To add more tools, simply:

1. Add the tool definition to the `TOOLS` array
2. Add a handler in the `executeToolCall` function

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

function executeToolCall(toolCall: ToolCall): string {
  const { name } = toolCall.function;
  
  if (name === 'console_log') {
    // ... existing handler
  }
  
  if (name === 'get_time') {
    return new Date().toISOString();
  }
  
  return `Unknown tool: ${name}`;
}
```

## Related

- [OpenRouter Documentation](https://openrouter.ai/docs)
- [OpenHands TypeScript SDK](../../README.md)
- [Local Chat Example](../local/) - Simpler chat without tool calling
