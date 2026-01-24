/**
 * Interactive CLI Example
 *
 * An interactive command-line interface for chatting with a local AI agent.
 * The agent can execute commands, read/write files, and help with coding tasks.
 *
 * Usage:
 *   export OPENROUTER_API_KEY="your-api-key"
 *   npx ts-node examples/local/interactive-cli.ts [working-directory]
 */

import * as readline from 'readline';
// When running from source: import from '../../src'
// When using the package: import from '@openhands/typescript-client'
import {
  LocalWorkspace,
  LocalConversation,
  OpenRouterLLM,
  AgentBase,
  Event,
  generateSystemPrompt,
} from '../../dist';

// ANSI color codes for pretty output
const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  dim: '\x1b[2m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  magenta: '\x1b[35m',
  cyan: '\x1b[36m',
};

function colorize(text: string, color: keyof typeof colors): string {
  return `${colors[color]}${text}${colors.reset}`;
}

async function main() {
  // Get API key and working directory
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) {
    console.error(colorize('Error: OPENROUTER_API_KEY environment variable is not set', 'red'));
    console.error('Get your API key at: https://openrouter.ai');
    process.exit(1);
  }

  const workingDir = process.argv[2] || process.cwd();

  // Print welcome message
  console.log(colorize('\n╔════════════════════════════════════════════════════════════╗', 'cyan'));
  console.log(colorize('║          OpenHands Local Agent - Interactive CLI           ║', 'cyan'));
  console.log(colorize('╚════════════════════════════════════════════════════════════╝', 'cyan'));
  console.log();
  console.log(`${colorize('Working Directory:', 'bright')} ${workingDir}`);
  console.log(`${colorize('Model:', 'bright')} anthropic/claude-3.5-sonnet`);
  console.log();
  console.log(colorize('Commands:', 'yellow'));
  console.log('  /quit or /exit  - Exit the CLI');
  console.log('  /clear          - Clear conversation history');
  console.log('  /stats          - Show conversation statistics');
  console.log('  /help           - Show this help message');
  console.log();
  console.log(colorize('Type your message and press Enter to chat with the agent.', 'dim'));
  console.log(colorize('─'.repeat(60), 'dim'));

  // Create components
  const llm = new OpenRouterLLM({
    apiKey,
    defaultModel: 'anthropic/claude-3.5-sonnet',
    defaultTemperature: 0.7,
    defaultMaxTokens: 4096,
  });

  const workspace = new LocalWorkspace({ workingDir });

  const agent: AgentBase = {
    kind: 'local-agent',
    llm: { model: 'anthropic/claude-3.5-sonnet' },
  };

  // Custom system prompt for interactive mode
  const systemPrompt = generateSystemPrompt({
    workingDir,
    additionalContext: `
<INTERACTIVE_MODE>
You are in an interactive CLI session. The user will send messages and you should respond helpfully.
- Be concise but thorough
- When executing commands or modifying files, explain what you're doing
- If a task has multiple steps, complete them all before finishing
- Always call finish() when you've completed the user's request
</INTERACTIVE_MODE>`,
  });

  let conversation: LocalConversation | null = null;
  let isRunning = false;

  // Event handler
  const handleEvent = (event: Event) => {
    switch (event.kind) {
      case 'assistant_message':
        console.log();
        console.log(colorize('🤖 Assistant:', 'green'));
        console.log((event as any).content);
        break;
      case 'tool_call':
        const tool = (event as any).tool;
        const args = (event as any).arguments;
        if (tool === 'execute_command') {
          try {
            const parsed = JSON.parse(args);
            console.log(colorize(`\n$ ${parsed.command}`, 'yellow'));
          } catch {
            console.log(colorize(`\n🔧 ${tool}`, 'yellow'));
          }
        } else if (tool === 'think') {
          // Don't show think tool calls - they're shown in the result
        } else {
          console.log(colorize(`\n🔧 ${tool}`, 'yellow'));
        }
        break;
      case 'tool_result':
        const toolName = (event as any).tool;
        const result = (event as any).result;
        if (toolName === 'think') {
          console.log(colorize('\n🤔 Thinking...', 'magenta'));
        } else if (toolName !== 'finish') {
          // Truncate long output
          const maxLen = 1000;
          const display = result.length > maxLen ? result.slice(0, maxLen) + '\n...(truncated)' : result;
          console.log(colorize(display, 'dim'));
        }
        break;
      case 'think':
        console.log(colorize(`\n🤔 ${(event as any).thought}`, 'magenta'));
        break;
      case 'finish':
        console.log();
        console.log(colorize('✅ ' + (event as any).message, 'green'));
        break;
      case 'agent_error':
        console.log(colorize(`\n❌ Error: ${(event as any).error}`, 'red'));
        break;
    }
  };

  // Create new conversation
  const createConversation = async () => {
    if (conversation) {
      await conversation.close();
    }
    conversation = new LocalConversation(agent, workspace, {
      llm,
      maxIterations: 30,
      systemPrompt,
      callback: handleEvent,
    });
    await conversation.start({});
  };

  await createConversation();

  // Create readline interface
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  const prompt = () => {
    rl.question(colorize('\n> ', 'bright'), async (input) => {
      const trimmed = input.trim();

      if (!trimmed) {
        prompt();
        return;
      }

      // Handle commands
      if (trimmed.startsWith('/')) {
        const cmd = trimmed.toLowerCase();

        if (cmd === '/quit' || cmd === '/exit') {
          console.log(colorize('\n👋 Goodbye!', 'cyan'));
          if (conversation) await conversation.close();
          rl.close();
          process.exit(0);
        }

        if (cmd === '/clear') {
          await createConversation();
          console.log(colorize('🔄 Conversation cleared', 'yellow'));
          prompt();
          return;
        }

        if (cmd === '/stats') {
          if (conversation) {
            const stats = await conversation.conversationStats();
            console.log(colorize('\n📊 Conversation Statistics:', 'cyan'));
            console.log(`   Total events: ${stats.total_events}`);
            console.log(`   Messages: ${stats.message_events}`);
            console.log(`   Actions: ${stats.action_events}`);
            console.log(`   Observations: ${stats.observation_events}`);
          }
          prompt();
          return;
        }

        if (cmd === '/help') {
          console.log(colorize('\nCommands:', 'yellow'));
          console.log('  /quit or /exit  - Exit the CLI');
          console.log('  /clear          - Clear conversation history');
          console.log('  /stats          - Show conversation statistics');
          console.log('  /help           - Show this help message');
          prompt();
          return;
        }

        console.log(colorize(`Unknown command: ${cmd}`, 'red'));
        prompt();
        return;
      }

      // Send message to agent
      if (conversation && !isRunning) {
        isRunning = true;
        try {
          await conversation.sendMessage(trimmed);
          await conversation.run();
        } catch (error) {
          console.error(colorize(`Error: ${error}`, 'red'));
        }
        isRunning = false;
      }

      prompt();
    });
  };

  prompt();
}

main().catch(console.error);
