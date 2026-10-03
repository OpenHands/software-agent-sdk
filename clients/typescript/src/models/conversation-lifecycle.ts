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

/**
 * Runtime semantics for a local (in-process) conversation, mirroring
 * `ConversationRegistry.runtime_info` on the Agent Server. Used when a response
 * carries `archived_at` but omits the optional `runtime_info` — e.g. the
 * `create`/`fork`/`navigate` responses, which return a bare `ConversationInfo`.
 */
const LOCAL_RUNTIME_STATUS: ConversationRuntimeStatus = 'available';
const LOCAL_CAN_RESUME = true;

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
  if (archivedAt === undefined) {
    throw new Error('Conversation response does not include canonical lifecycle fields');
  }

  // `runtime_info` is optional on the public `ConversationInfo`: the catalog
  // routes add it, but the create/fork/navigate handlers return a bare
  // `ConversationInfo`. A response that carries `archived_at` but no
  // `runtime_info` is therefore a perfectly canonical local conversation, not a
  // pre-contract server, and callers must not need a version check to read it.
  // Fall back to the local runtime semantics the Agent Server itself reports
  // (`ConversationRegistry.runtime_info`: always available and resumable).
  return {
    isArchived: archivedAt !== null,
    archivedAt,
    runtimeStatus: runtimeInfo?.runtime_status ?? LOCAL_RUNTIME_STATUS,
    executionStatus: conversation.execution_status,
    canResume: runtimeInfo?.can_resume ?? LOCAL_CAN_RESUME,
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

  const runtimeStatus =
    conversation.runtime_status ?? cloudRuntimeStatus(conversation.sandbox_status);

  return {
    isArchived,
    archivedAt,
    runtimeStatus,
    executionStatus: cloudExecutionStatus(conversation.execution_status),
    // Matches the Agent Server, which reports an available runtime as resumable.
    canResume: conversation.can_resume ?? (!isArchived && runtimeStatus === 'available'),
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
