/**
 * Simple Task Example
 *
 * A minimal example showing how to run a simple task with the local agent.
 * Great for testing that everything is working correctly.
 *
 * Usage:
 *   export OPENROUTER_API_KEY="your-api-key"
 *   npx ts-node examples/local/simple-task.ts
 */

// When running from source: import from '../../src'
// When using the package: import from '@openhands/typescript-client'
import {
  LocalWorkspace,
  LocalConversation,
  OpenRouterLLM,
  AgentBase,
} from '../../dist';

async function main() {
  // Check for API key
  const apiKey = process.env.OPENROUTER_API_KEY;
  if (!apiKey) {
    console.error('❌ OPENROUTER_API_KEY environment variable is required');
    console.error('   Get your API key at: https://openrouter.ai');
    process.exit(1);
  }

  console.log('🚀 Simple Local Agent Task\n');

  // Create the LLM
  const llm = new OpenRouterLLM({
    apiKey,
    defaultModel: 'anthropic/claude-3.5-sonnet',
  });

  // Create workspace in a temp directory
  const workspace = new LocalWorkspace({
    workingDir: '/tmp/openhands-test',
  });

  // Ensure the temp directory exists
  const { execSync } = await import('child_process');
  execSync('mkdir -p /tmp/openhands-test');

  // Create the agent
  const agent: AgentBase = {
    kind: 'simple-agent',
    llm: { model: 'anthropic/claude-3.5-sonnet' },
  };

  // Create conversation with verbose logging
  const conversation = new LocalConversation(agent, workspace, {
    llm,
    maxIterations: 10,
    callback: (event) => {
      const kind = event.kind;
      if (kind === 'tool_call') {
        console.log(`🔧 Tool: ${(event as any).tool}`);
      } else if (kind === 'tool_result') {
        const result = (event as any).result;
        console.log(`📋 Result: ${result.length > 100 ? result.slice(0, 100) + '...' : result}`);
      } else if (kind === 'finish') {
        console.log(`\n✅ Done: ${(event as any).message}`);
      }
    },
  });

  // Start and run a simple task
  console.log('📝 Task: Create a hello.txt file and verify it exists\n');

  await conversation.start({
    initialMessage: 'Create a file called hello.txt in the current directory with the content "Hello from OpenHands!" then verify it was created by reading it back.',
  });

  await conversation.run();

  // Show final stats
  const stats = await conversation.conversationStats();
  console.log(`\n📊 Stats: ${stats.action_events} actions, ${stats.observation_events} observations`);

  await conversation.close();

  // Cleanup
  execSync('rm -rf /tmp/openhands-test');
  console.log('\n🧹 Cleaned up temp directory');
}

main().catch(console.error);
