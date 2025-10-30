/**
 * Conversation-related models and interfaces
 */

import {
  ConversationID,
  // Event, // Unused for now
  AgentExecutionStatus,
  ConfirmationPolicyBase,
  ConversationStats,
  AgentBase,
} from '../types/base';

export interface ConversationInfo {
  id: ConversationID;
  agent_status: AgentExecutionStatus;
  confirmation_policy: ConfirmationPolicyBase;
  activated_knowledge_skills: string[];
  agent: AgentBase;
  workspace: any;
  persistence_dir: string;
  conversation_stats: ConversationStats;
  [key: string]: any;
}

export interface SendMessageRequest {
  role: 'user';
  content: Array<{
    type: string;
    text?: string;
    image_url?: string;
  }>;
  run: boolean;
}

export interface ConfirmationResponseRequest {
  accept: boolean;
  reason?: string;
}

export interface CreateConversationRequest {
  agent: AgentBase;
  initial_message?: string;
  max_iterations: number;
  stuck_detection: boolean;
  workspace: any;
}

export interface GenerateTitleRequest {
  max_length: number;
  llm?: any;
}

export interface GenerateTitleResponse {
  title: string;
}

export interface UpdateSecretsRequest {
  secrets: Record<string, string>;
}

export interface ConversationSearchRequest {
  page_id?: string;
  limit?: number;
  status?: AgentExecutionStatus;
  sort_order?: string;
}

export interface ConversationSearchResponse {
  items: ConversationInfo[];
  next_page_id?: string;
  total_count?: number;
}
