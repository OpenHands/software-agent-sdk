/**
 * Basic Local Agent Example
 *
 * This example demonstrates how to use LocalConversation with OpenRouterLLM
 * to create a local AI agent that can interact with your filesystem.
 *
 * Usage:
 *   export OPENROUTER_API_KEY="your-api-key"
 *   npx ts-node examples/local/basic-local-agent.ts
 */

// When running from source: import from '../../src'
// When using the package: import from '@openhands/typescript-client'
import {
  LocalWorkspace,
  LocalConversation,
  OpenRouterLLM,
  AgentBase,
  Event,
} from '../../dist';

async function main() {
  // Get API key from environment
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) {
    console.error('Error: OPENROUTER_API_KEY environment variable is not set');
    console.error('Get your API key at: https://openrouter.ai');
    process.exit(1);
  }

  // Create the LLM instance using OpenRouter
  const llm = new OpenRouterLLM({
    apiKey,
    defaultModel: 'anthropic/claude-3.5-sonnet', // You can change this to any OpenRouter model
    defaultTemperature: 0.7,
    defaultMaxTokens: 4096,
  });

  console.log('🤖 Created OpenRouterLLM with model: anthropic/claude-3.5-sonnet');

  // Create a workspace pointing to the current directory
  const workingDir = process.cwd();
  const workspace = new LocalWorkspace({ workingDir });

  console.log(`📁 Created LocalWorkspace at: ${workingDir}`);

  // Define the agent configuration
  const agent: AgentBase = {
    kind: 'local-agent',
    llm: { model: 'anthropic/claude-3.5-sonnet' },
  };

  // Create the conversation with event callback
  const conversation = new LocalConversation(agent, workspace, {
    llm,
    maxIterations: 20,
    callback: (event: Event) => {
      // Log events as they happen
      switch (event.kind) {
        case 'user_message':
          console.log('\n📝 User:', (event as any).content);
          break;
        case 'assistant_message':
          console.log('\n🤖 Assistant:', (event as any).content);
          break;
        case 'tool_call':
          console.log(`\n🔧 Tool: ${(event as any).tool}(${(event as any).arguments})`);
          break;
        case 'tool_result':
          const result = (event as any).result;
          const truncated = result.length > 500 ? result.slice(0, 500) + '...' : result;
          console.log(`📋 Result: ${truncated}`);
          break;
        case 'think':
          console.log(`\n🤔 Thinking: ${(event as any).thought}`);
          break;
        case 'finish':
          console.log(`\n✅ Finished: ${(event as any).message}`);
          break;
        case 'agent_error':
          console.error(`\n❌ Error: ${(event as any).error}`);
          break;
      }
    },
  });

  console.log('\n🚀 Starting conversation...\n');

  // Start the conversation with an initial message
  await conversation.start({
    initialMessage: 'List the files in the current directory and tell me what kind of project this is.',
  });

  // Run the agent loop
  try {
    await conversation.run();
  } catch (error) {
    console.error('Agent error:', error);
  }

  // Get final stats
  const stats = await conversation.conversationStats();
  console.log('\n📊 Conversation Stats:');
  console.log(`   Total events: ${stats.total_events}`);
  console.log(`   Messages: ${stats.message_events}`);
  console.log(`   Actions: ${stats.action_events}`);
  console.log(`   Observations: ${stats.observation_events}`);

  // Cleanup
  await conversation.close();
  console.log('\n👋 Conversation closed');
}

main().catch(console.error);
