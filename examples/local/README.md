# OpenHands Local Chat

A browser-based chat UI that demonstrates the OpenHands TypeScript SDK's `LLM` class with OpenRouter integration.

![OpenHands Chat Screenshot](https://via.placeholder.com/800x500?text=OpenHands+Chat+UI)

## Features

- 🔐 **OpenRouter Authentication** - Securely connect with your API key
- 💬 **Real-time Chat** - Conversational interface with message history
- 🤖 **Multiple Models** - Switch between Claude, GPT-4, Gemini, Llama, and more
- ⚙️ **Configurable Settings** - Adjust temperature and max tokens
- 🌙 **Dark Mode** - Beautiful dark theme UI
- 📱 **Responsive** - Works on desktop and mobile

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
cd examples/local
npm install
npm run dev
```

### Running

1. Open http://localhost:12000 in your browser
2. Enter your OpenRouter API key
3. Select a model and start chatting!

## Available Models

The app comes pre-configured with popular models:

| Model | Description |
|-------|-------------|
| Claude 3.5 Sonnet | Best for complex reasoning and coding |
| Claude 3 Haiku | Fast and efficient |
| GPT-4o | OpenAI's latest flagship |
| GPT-4o Mini | Faster, cheaper GPT-4 |
| Gemini Pro 1.5 | Google's advanced model |
| Llama 3.1 70B | Meta's open source model |
| Mistral Large | Mistral's premium model |

You can access 300+ more models through OpenRouter's unified API.

## Project Structure

```
examples/local/
├── index.html           # Entry HTML file
├── package.json         # Dependencies
├── tsconfig.json        # TypeScript config
├── vite.config.ts       # Vite bundler config
└── src/
    ├── main.tsx         # React entry point
    ├── App.tsx          # Main app component
    ├── styles.css       # Global styles
    └── components/
        ├── AuthScreen.tsx      # API key input
        ├── ChatInterface.tsx   # Chat messages & input
        └── SettingsModal.tsx   # Model settings
```

## How It Works

This example demonstrates using the `LLM` class from the TypeScript SDK. The `LLM` class uses OpenRouter under the hood to provide access to 300+ models:

```typescript
import { LLM } from '@openhands/typescript-client';

// Create LLM instance (uses OpenRouter under the hood)
const llm = new LLM({
  apiKey: 'your-api-key',
  defaultModel: 'anthropic/claude-3.5-sonnet',
  defaultTemperature: 0.7,
  defaultMaxTokens: 4096,
});

// Send a message using chatCompletion
const response = await llm.chatCompletion({
  messages: [
    { role: 'user', content: 'Hello!' }
  ],
  model: 'anthropic/claude-3.5-sonnet',
});

console.log(response.choices[0].message.content);

// Or use the simple generate() helper
const text = await llm.generate('Hello!');
console.log(text);
```

## Configuration

### Environment Variables

The app stores settings in localStorage:
- `openrouter_api_key` - Your OpenRouter API key
- `openrouter_model` - Selected model ID

### Settings

Click the ⚙️ button to adjust:
- **Model** - Switch between available models
- **Temperature** - 0.0 (precise) to 2.0 (creative)
- **Max Tokens** - Response length limit (256-8192)

## Development

```bash
# Start dev server with hot reload
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview
```

## Security Notes

- API keys are stored in localStorage (client-side only)
- Keys are validated against OpenRouter's API before use
- All API calls go directly to OpenRouter (no backend proxy)
- Click "Logout" to clear stored credentials

## Troubleshooting

### "Invalid API key" error
- Make sure your API key starts with `sk-or-v1-`
- Check that your key has available credits at openrouter.ai

### Build errors
- Ensure the main SDK is built first: `cd ../.. && npm run build`
- Try clearing node_modules: `rm -rf node_modules && npm install`

### CORS issues
- OpenRouter's API supports browser requests
- If issues persist, check your API key permissions

## Related

- [OpenRouter Documentation](https://openrouter.ai/docs)
- [OpenHands TypeScript SDK](../../README.md)
- [Model Pricing](https://openrouter.ai/models)
- [Local Agent Example](../local-agent/) - Agent with tool calling
