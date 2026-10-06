import type { ThinkObservation } from '../generated/agent-server-schema';

/** Requires an Agent Server with model-requested context reset support. */
export interface AgentResetCondenserSettings {
  condenser_kind: 'agent_reset';
  enabled?: boolean;
}

/** Runtime condenser configuration for a direct Agent specification. */
export interface AgentResetCondenser {
  kind: 'AgentResetCondenser';
}

export interface NewContextAction {
  kind?: 'NewContextAction';
  handoff?: string;
}

export type NewContextObservation = Omit<ThinkObservation, 'kind'> & {
  kind: 'NewContextObservation';
  input_event_id?: string | null;
};

export interface ConversationHistoryAction {
  kind?: 'ConversationHistoryAction';
  command: 'search' | 'read';
  query?: string | null;
  event_id?: string | null;
  before_event_id?: string | null;
  offset?: number;
}

export type ConversationHistoryObservation = Omit<ThinkObservation, 'kind'> & {
  kind: 'ConversationHistoryObservation';
};
