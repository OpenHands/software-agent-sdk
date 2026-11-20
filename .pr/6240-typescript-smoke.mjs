import assert from 'node:assert/strict';
import { ConversationManager } from '../clients/typescript/dist/conversation/conversation-manager.js';

const manager = new ConversationManager({
  host: process.env.AGENT_SERVER_URL,
  apiKey: process.env.SESSION_API_KEY,
});
const conversation = await manager.createConversation(
  { kind: 'Agent', llm: { model: 'gpt-4o-mini', api_key: 'test' } },
  {
    workingDir: process.env.AGENT_SERVER_WORKSPACE || '/tmp',
    stuckDetectionThresholds: {
      actionObservation: 6,
      actionError: 2,
      monologue: 5,
      alternatingPattern: 8,
    },
  }
);
const info = await manager.getConversation(conversation.id);
assert.deepEqual(info.stuck_detection_thresholds, {
  action_observation: 6,
  action_error: 2,
  monologue: 5,
  alternating_pattern: 8,
});
console.log(JSON.stringify({ id: conversation.id, thresholds: info.stuck_detection_thresholds }));
await conversation.close();
