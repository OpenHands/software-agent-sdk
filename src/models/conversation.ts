/**
 * Conversation-related models and interfaces
 */

import {
  ConversationID,
  // Event, // Unused for now
  ConversationExecutionStatus,
  AgentExecutionStatus,
  ConfirmationPolicyBase,
  ConversationStats,
  AgentBase,
  Message,
} from '../types/base';
import type { HookConfig } from '../hooks';

export interface ConversationInfo {
  id: ConversationID;
  /**
   * Current execution status of the conversation.
   * Note: This field was renamed from agent_status to execution_status in the API.
   */
  execution_status: ConversationExecutionStatus;
  /**
   * @deprecated Use execution_status instead. This field is kept for backward compatibility.
   */
  agent_status?: AgentExecutionStatus;
  confirmation_policy: ConfirmationPolicyBase;
  activated_knowledge_skills: string[];
  agent: AgentBase;
  workspace: unknown;
  persistence_dir: string;
  conversation_stats?: ConversationStats;
  /** API may return stats instead of conversation_stats */
  stats?: ConversationStats;
  hook_config?: HookConfig | null;
  blocked_actions?: Record<string, string>;
  blocked_messages?: Record<string, string>;
  title?: string;
  created_at?: string;
  updated_at?: string;
  /**
   * @deprecated Use execution_status instead. This field is kept for backward compatibility.
   */
  status?: ConversationExecutionStatus;
  [key: string]: unknown;
}

export interface SendMessageRequest {
  role: 'user';
  content: Array<{
    type: string;
    text?: string;
    image_urls?: string[];
  }>;
  run: boolean;
}

export interface ConfirmationResponseRequest {
  accept: boolean;
  reason?: string;
}

export interface CreateConversationRequest {
  agent: AgentBase;
  initial_message?: Message;
  max_iterations: number;
  stuck_detection: boolean;
  workspace: any;
  hook_config?: HookConfig | null;
}

export interface GenerateTitleRequest {
  max_length: number;
  llm?: any;
}

export interface GenerateTitleResponse {
  title: string;
}

export interface UpdateConversationRequest {
  title?: string;
  tags?: Record<string, string>;
}

export interface StaticSecret {
  kind: 'StaticSecret';
  value: string;
  description?: string;
}

export interface LookupSecret {
  kind: 'LookupSecret';
  source: string;
  key: string;
  description?: string;
}

export type SecretObject = StaticSecret | LookupSecret;

export interface UpdateSecretsRequest {
  secrets: Record<string, SecretObject>;
}

export interface ConversationSearchRequest {
  page_id?: string;
  limit?: number;
  status?: ConversationExecutionStatus;
  sort_order?: string;
  tag?: string[];
}

export interface AskAgentRequest {
  question: string;
}

export interface AskAgentResponse {
  response: string;
}

export interface SetSecurityAnalyzerRequest {
  security_analyzer: any | null;
}

export interface ConversationSearchResponse {
  items: ConversationInfo[];
  next_page_id?: string;
  total_count?: number;
}
