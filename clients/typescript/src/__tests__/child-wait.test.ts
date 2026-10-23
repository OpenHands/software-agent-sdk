import { afterEach, expect, it, vi } from 'vitest';
import { ConversationClient } from '../client/conversation-client';
import { ConversationExecutionStatus } from '../types/base';
import type { WaitForChildrenResponse } from '../models/conversation';

const originalFetch = global.fetch;
afterEach(() => {
  global.fetch = originalFetch;
});

it('waits with one authenticated request and forwards caller cancellation', async () => {
  const result: WaitForChildrenResponse = {
    children: [
      {
        conversation_id: 'child',
        execution_status: ConversationExecutionStatus.RUNNING,
        settled: false,
        deleted: false,
      },
    ],
    timed_out: true,
    interrupted: false,
  };
  const fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(result), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  );
  global.fetch = fetch;
  const client = new ConversationClient({ host: 'http://test', apiKey: 'secret' });
  const controller = new AbortController();
  expect(
    await client.waitForChildren(
      'parent',
      { conversation_ids: ['child'], timeout_seconds: 300 },
      { signal: controller.signal }
    )
  ).toEqual(result);
  expect(fetch).toHaveBeenCalledTimes(1);
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe('http://test/api/conversations/parent/children/wait');
  expect(options.headers['X-Session-API-Key']).toBe('secret');
  expect(JSON.parse(options.body)).toEqual({ conversation_ids: ['child'], timeout_seconds: 300 });
  controller.abort();
  expect(options.signal.aborted).toBe(true);
});
