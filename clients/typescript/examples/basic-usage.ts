/**
 * Basic usage example for the OpenHands Agent Server TypeScript Client
 */

import { Conversation, Agent, Workspace, AgentExecutionStatus } from '../src/index.js';

async function main() {
  // Define the agent configuration
  // Note: In a browser environment, you would get these values from your app's configuration
  const agent = new Agent({
    llm: {
      model: 'gpt-4',
      api_key: 'your-openai-api-key', // Replace with your actual API key
    },
  });

  try {
    // Create a remote workspace
    const workspace = new Workspace({
      host: 'http://localhost:3000',
      workingDir: '/tmp',
      apiKey: 'your-session-api-key', // Replace with your actual session API key
    });

    // Create a new conversation
    console.log('Creating conversation...');
    const conversation = new Conversation(agent, workspace, {
      callback: (event) => {
        console.log(`Event received: ${event.kind} at ${event.timestamp}`);
      },
    });

    // Start the conversation with an initial message
    await conversation.start({
      initialMessage: 'Hello! Can you help me write a simple Python script?',
    });

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
      await new Promise((resolve) => setTimeout(resolve, 1000));
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
  const agent = new Agent({
    llm: {
      model: 'gpt-4',
      api_key: 'your-openai-api-key', // Replace with your actual API key
    },
  });

  try {
    // Create a remote workspace for the existing conversation
    const workspace = new Workspace({
      host: 'http://localhost:3000',
      workingDir: '/tmp',
      apiKey: 'your-session-api-key', // Replace with your actual session API key
    });

    const conversation = new Conversation(agent, workspace, {
      conversationId: 'existing-conversation-id',
    });

    // Connect to the existing conversation
    await conversation.start();

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
// Note: In a browser environment, you would call main() directly or from an event handler
// For Node.js environments, you can use import.meta.main (ES modules) or check if this is the main module
main().catch(console.error);
