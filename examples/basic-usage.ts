/**
 * Basic usage example for the OpenHands Agent Server TypeScript Client
 */

import { RemoteConversation, AgentBase, AgentExecutionStatus } from '../src/index.js';

async function main() {
  // Define the agent configuration
  const agent: AgentBase = {
    name: 'CodeActAgent',
    llm: {
      model: 'gpt-4',
      api_key: process.env.OPENAI_API_KEY || 'your-openai-api-key',
    },
  };

  try {
    // Create a new conversation
    console.log('Creating conversation...');
    const conversation = await RemoteConversation.create(
      'http://localhost:3000', // Replace with your agent server URL
      agent,
      {
        apiKey: process.env.SESSION_API_KEY || 'your-session-api-key',
        initialMessage: 'Hello! Can you help me write a simple Python script?',
        callback: (event) => {
          console.log(`Event received: ${event.kind} at ${event.timestamp}`);
        },
      }
    );

    console.log(`Conversation created with ID: ${conversation.id}`);

    // Start WebSocket for real-time events
    await conversation.startWebSocketClient();
    console.log('WebSocket client started');

    // Send a message
    await conversation.sendMessage('Create a Python script that prints "Hello, World!"');
    console.log('Message sent');

    // Run the agent
    await conversation.run();
    console.log('Agent started');

    // Monitor the conversation status
    let status = await conversation.state.getAgentStatus();
    console.log(`Initial status: ${status}`);

    // Wait for the agent to finish (in a real application, you'd handle this differently)
    while (status === AgentExecutionStatus.RUNNING) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      status = await conversation.state.getAgentStatus();
      console.log(`Current status: ${status}`);
    }

    // Get conversation statistics
    const stats = await conversation.conversationStats();
    console.log('Conversation stats:', stats);

    // Get all events
    const events = await conversation.state.events.getEvents();
    console.log(`Total events: ${events.length}`);

    // Example of using the workspace
    const result = await conversation.workspace.executeCommand('ls -la');
    console.log('Command result:', {
      exitCode: result.exit_code,
      stdout: result.stdout.substring(0, 200) + '...', // Truncate for display
    });

    // Clean up
    await conversation.close();
    console.log('Conversation closed');

  } catch (error) {
    console.error('Error:', error);
  }
}

// Example of loading an existing conversation
async function loadExistingConversation() {
  try {
    const conversation = await RemoteConversation.load(
      'http://localhost:3000',
      'existing-conversation-id',
      {
        apiKey: process.env.SESSION_API_KEY || 'your-session-api-key',
      }
    );

    console.log(`Loaded conversation: ${conversation.id}`);
    
    // Get current status
    const status = await conversation.state.getAgentStatus();
    console.log(`Status: ${status}`);

    // Clean up
    await conversation.close();

  } catch (error) {
    console.error('Error loading conversation:', error);
  }
}

// Run the example
if (require.main === module) {
  main().catch(console.error);
}