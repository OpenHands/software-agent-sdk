import { ConversationExecutionStatus } from '../types/base';
import type { ConversationInfo, ConversationRuntimeStatus } from './conversation';

export interface ConversationLifecycle {
  isArchived: boolean;
  archivedAt: string | null;
  runtimeStatus: ConversationRuntimeStatus;
  executionStatus: ConversationExecutionStatus | null;
  canResume: boolean;
}

export type ConversationLifecycleFields = Pick<
  ConversationInfo,
  'archived_at' | 'runtime_info' | 'execution_status'
>;

export interface LegacyCloudConversationLifecycleFields {
  archived_at?: string | null;
  runtime_status?: ConversationRuntimeStatus | null;
  execution_status?: string | null;
  can_resume?: boolean | null;
  sandbox_status?: string | null;
}

export function normalizeConversationLifecycle(
  conversation: ConversationLifecycleFields
): ConversationLifecycle {
  const { archived_at: archivedAt, runtime_info: runtimeInfo } = conversation;
  if (archivedAt === undefined || runtimeInfo == null) {
    throw new Error('Conversation response does not include canonical lifecycle fields');
  }

  return {
    isArchived: archivedAt !== null,
    archivedAt,
    runtimeStatus: runtimeInfo.runtime_status,
    executionStatus: conversation.execution_status,
    canResume: runtimeInfo.can_resume,
  };
}

export function normalizeCloudConversationLifecycle(
  conversation: LegacyCloudConversationLifecycleFields
): ConversationLifecycle {
  const archivedAt = conversation.archived_at ?? null;
  const isArchived =
    conversation.archived_at !== undefined
      ? archivedAt !== null
      : inferLegacyCloudArchiveState(conversation.sandbox_status);

  return {
    isArchived,
    archivedAt,
    runtimeStatus: conversation.runtime_status ?? cloudRuntimeStatus(conversation.sandbox_status),
    executionStatus: cloudExecutionStatus(conversation.execution_status),
    canResume: conversation.can_resume ?? (!isArchived && conversation.sandbox_status === 'PAUSED'),
  };
}

/**
 * @deprecated Replaced by explicit Cloud `archived_at`. Remove when every
 * supported Cloud deployment returns that field. See the linked removal issue.
 */
function inferLegacyCloudArchiveState(sandboxStatus: string | null | undefined): boolean {
  return sandboxStatus === 'MISSING';
}

function cloudRuntimeStatus(sandboxStatus: string | null | undefined): ConversationRuntimeStatus {
  switch (sandboxStatus) {
    case 'RUNNING':
    case 'PAUSED':
      return 'available';
    case 'STARTING':
      return 'starting';
    case 'ERROR':
      return 'error';
    case 'MISSING':
    case null:
    case undefined:
    default:
      return 'missing';
  }
}

function cloudExecutionStatus(
  executionStatus: string | null | undefined
): ConversationExecutionStatus | null {
  return Object.values(ConversationExecutionStatus).includes(
    executionStatus as ConversationExecutionStatus
  )
    ? (executionStatus as ConversationExecutionStatus)
    : null;
}
