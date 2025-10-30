/**
 * Conversation factory function that returns RemoteConversation
 * Matches the Python SDK pattern: Conversation(agent, workspace)
 */

import { AgentBase } from '../types/base';
import { RemoteWorkspace } from '../workspace/remote-workspace';
import { RemoteConversation, RemoteConversationOptions } from './remote-conversation';

/**
 * Conversation class that extends RemoteConversation.
 * Provides a cleaner API that matches the Python SDK naming.
 *
 * Usage:
 *   const conversation = new Conversation(agent, workspace);
 *   await conversation.start();
 *
 * For existing conversations:
 *   const conversation = new Conversation(agent, workspace, { conversationId: 'existing-id' });
 *   await conversation.start();
 */
export class Conversation extends RemoteConversation {
  constructor(agent: AgentBase, workspace: RemoteWorkspace, options?: RemoteConversationOptions) {
    super(agent, workspace, options);
  }
}
