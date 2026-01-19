/**
 * Remote conversation state management
 */

import { HttpClient } from '../client/http-client';
import { RemoteEventsList } from '../events/remote-events-list';
import {
  ConversationID,
  Event,
  ConversationExecutionStatus,
  AgentExecutionStatus,
  ConfirmationPolicyBase,
  // ConversationStats, // Unused for now
  AgentBase,
  ConversationCallbackType,
} from '../types/base';
import { ConversationInfo } from '../models/conversation';

const FULL_STATE_KEY = '__full_state__';

export interface ConversationStateUpdateEvent extends Event {
  kind: 'ConversationStateUpdateEvent';
  key: string;
  value: any;
}

export class RemoteState {
  private client: HttpClient;
  private conversationId: string;
  private _events: RemoteEventsList;
  private cachedState: ConversationInfo | null = null;
  private lock = new AsyncLock();

  constructor(client: HttpClient, conversationId: string) {
    this.client = client;
    this.conversationId = conversationId;
    this._events = new RemoteEventsList(client, conversationId);
  }

  private async getConversationInfo(): Promise<ConversationInfo> {
    return await this.lock.acquire(async () => {
      // Return cached state if available
      if (this.cachedState !== null) {
        return this.cachedState;
      }

      // Fallback to REST API if no cached state
      const response = await this.client.get<any>(`/api/conversations/${this.conversationId}`);

      // Handle the case where the API returns a full_state wrapper
      let conversationInfo: ConversationInfo;
      if (response.data.full_state) {
        conversationInfo = response.data.full_state as ConversationInfo;
      } else {
        conversationInfo = response.data as ConversationInfo;
      }

      this.cachedState = conversationInfo;
      return conversationInfo;
    });
  }

  async updateStateFromEvent(event: ConversationStateUpdateEvent): Promise<void> {
    await this.lock.acquire(async () => {
      // Handle full state snapshot
      if (event.key === FULL_STATE_KEY) {
        // Update cached state with the full snapshot
        if (this.cachedState === null) {
          this.cachedState = {} as ConversationInfo;
        }
        Object.assign(this.cachedState, event.value);
      } else {
        // Handle individual field updates
        if (this.cachedState === null) {
          this.cachedState = {} as ConversationInfo;
        }
        (this.cachedState as any)[event.key] = event.value;
      }
    });
  }

  createStateUpdateCallback(): ConversationCallbackType {
    return (event: Event) => {
      if (event.kind === 'ConversationStateUpdateEvent') {
        this.updateStateFromEvent(event as ConversationStateUpdateEvent).catch((error) => {
          console.error('Error updating state from event:', error);
        });
      }
    };
  }

  get events(): RemoteEventsList {
    return this._events;
  }

  get id(): ConversationID {
    return this.conversationId;
  }

  /**
   * Get the current execution status of the conversation.
   * This method handles both the new `execution_status` field and the legacy `agent_status` field.
   */
  async getExecutionStatus(): Promise<ConversationExecutionStatus> {
    const info = await this.getConversationInfo();
    // Try new field first, fall back to legacy field
    const statusStr = info.execution_status ?? info.agent_status;
    if (statusStr === undefined || statusStr === null) {
      throw new Error(`execution_status missing in conversation info: ${JSON.stringify(info)}`);
    }
    return statusStr;
  }

  /**
   * @deprecated Use getExecutionStatus() instead. This method is kept for backward compatibility.
   */
  async getAgentStatus(): Promise<AgentExecutionStatus> {
    return this.getExecutionStatus();
  }

  async setAgentStatus(value: AgentExecutionStatus): Promise<void> {
    throw new Error(
      `Setting execution_status on RemoteState has no effect. ` +
        `Remote execution status is managed server-side. Attempted to set: ${value}`
    );
  }

  async getConfirmationPolicy(): Promise<ConfirmationPolicyBase> {
    const info = await this.getConversationInfo();
    const policyData = info.confirmation_policy;
    if (policyData === undefined || policyData === null) {
      throw new Error(`confirmation_policy missing in conversation info: ${JSON.stringify(info)}`);
    }
    return policyData;
  }

  async getActivatedKnowledgeSkills(): Promise<string[]> {
    const info = await this.getConversationInfo();
    return info.activated_knowledge_skills || [];
  }

  async getAgent(): Promise<AgentBase> {
    const info = await this.getConversationInfo();
    const agentData = info.agent;
    if (agentData === undefined || agentData === null) {
      throw new Error(`agent missing in conversation info: ${JSON.stringify(info)}`);
    }
    return agentData;
  }

  async getWorkspace(): Promise<any> {
    const info = await this.getConversationInfo();
    const workspace = info.workspace;
    if (workspace === undefined || workspace === null) {
      throw new Error(`workspace missing in conversation info: ${JSON.stringify(info)}`);
    }
    return workspace;
  }

  async getPersistenceDir(): Promise<string> {
    const info = await this.getConversationInfo();
    const persistenceDir = info.persistence_dir;
    if (persistenceDir === undefined || persistenceDir === null) {
      throw new Error(`persistence_dir missing in conversation info: ${JSON.stringify(info)}`);
    }
    return persistenceDir;
  }

  async modelDump(): Promise<Record<string, any>> {
    const info = await this.getConversationInfo();
    return info as Record<string, any>;
  }

  async modelDumpJson(): Promise<string> {
    const data = await this.modelDump();
    return JSON.stringify(data);
  }
}

// Simple async lock implementation (reused from remote-events-list.ts)
class AsyncLock {
  private locked = false;
  private queue: Array<() => void> = [];

  async acquire<T>(fn: () => Promise<T> | T): Promise<T> {
    return new Promise((resolve, reject) => {
      const execute = async () => {
        try {
          const result = await fn();
          resolve(result);
        } catch (error) {
          reject(error);
        } finally {
          this.locked = false;
          const next = this.queue.shift();
          if (next) {
            next();
          }
        }
      };

      if (this.locked) {
        this.queue.push(execute);
      } else {
        this.locked = true;
        execute();
      }
    });
  }
}
