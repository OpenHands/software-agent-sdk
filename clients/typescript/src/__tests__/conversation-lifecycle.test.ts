import {
  normalizeCloudConversationLifecycle,
  normalizeConversationLifecycle,
} from '../models/conversation-lifecycle';
import { ConversationExecutionStatus } from '../types/base';

describe('normalizeConversationLifecycle', () => {
  it('maps explicit archived Agent Server lifecycle fields directly', () => {
    expect(
      normalizeConversationLifecycle({
        archived_at: '2026-09-12T15:00:00Z',
        runtime_status: 'missing',
        execution_status: ConversationExecutionStatus.PAUSED,
        can_resume: true,
      })
    ).toEqual({
      isArchived: true,
      archivedAt: '2026-09-12T15:00:00Z',
      runtimeStatus: 'missing',
      executionStatus: ConversationExecutionStatus.PAUSED,
      canResume: true,
    });
  });

  it('keeps missing runtimes independent from explicit unarchived state', () => {
    expect(
      normalizeConversationLifecycle({
        archived_at: null,
        runtime_status: 'missing',
        execution_status: ConversationExecutionStatus.ERROR,
        can_resume: false,
      })
    ).toEqual({
      isArchived: false,
      archivedAt: null,
      runtimeStatus: 'missing',
      executionStatus: ConversationExecutionStatus.ERROR,
      canResume: false,
    });
  });
});

describe('normalizeCloudConversationLifecycle', () => {
  it('prefers an explicit archive timestamp over the sandbox status', () => {
    expect(
      normalizeCloudConversationLifecycle({
        archived_at: '2026-09-12T15:00:00Z',
        sandbox_status: 'RUNNING',
        execution_status: ConversationExecutionStatus.FINISHED,
      })
    ).toEqual({
      isArchived: true,
      archivedAt: '2026-09-12T15:00:00Z',
      runtimeStatus: 'available',
      executionStatus: ConversationExecutionStatus.FINISHED,
      canResume: false,
    });
  });

  it('prefers explicit unarchived metadata over a missing sandbox', () => {
    expect(
      normalizeCloudConversationLifecycle({
        archived_at: null,
        sandbox_status: 'MISSING',
        execution_status: ConversationExecutionStatus.ERROR,
        can_resume: true,
      })
    ).toEqual({
      isArchived: false,
      archivedAt: null,
      runtimeStatus: 'missing',
      executionStatus: ConversationExecutionStatus.ERROR,
      canResume: true,
    });
  });

  it('temporarily infers archive state from a legacy missing sandbox', () => {
    expect(
      normalizeCloudConversationLifecycle({
        sandbox_status: 'MISSING',
        execution_status: ConversationExecutionStatus.PAUSED,
      })
    ).toEqual({
      isArchived: true,
      archivedAt: null,
      runtimeStatus: 'missing',
      executionStatus: ConversationExecutionStatus.PAUSED,
      canResume: false,
    });
  });

  it.each([
    ['RUNNING', 'available'],
    ['PAUSED', 'available'],
    ['STARTING', 'starting'],
    ['ERROR', 'error'],
    [null, 'missing'],
  ] as const)(
    'maps Cloud sandbox status %s to runtime status %s',
    (sandboxStatus, runtimeStatus) => {
      expect(
        normalizeCloudConversationLifecycle({
          archived_at: null,
          sandbox_status: sandboxStatus,
          execution_status: null,
        })
      ).toMatchObject({
        runtimeStatus,
        executionStatus: null,
      });
    }
  );

  it('uses explicit resumability for unarchived conversations', () => {
    expect(
      normalizeCloudConversationLifecycle({
        archived_at: null,
        sandbox_status: 'ERROR',
        execution_status: ConversationExecutionStatus.ERROR,
        can_resume: false,
      })
    ).toMatchObject({
      isArchived: false,
      runtimeStatus: 'error',
      executionStatus: ConversationExecutionStatus.ERROR,
      canResume: false,
    });
  });
});
