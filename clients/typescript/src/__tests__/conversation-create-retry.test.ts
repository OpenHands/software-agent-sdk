import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { ConversationClient } from '../client/conversation-client';
import { HttpError } from '../client/http-client';

/**
 * The client rethrows the transport error from `fetch` when a create fails
 * without an HTTP response. Its exact message is runtime-specific, so only
 * assert the stable contract: a plain Error, never an HttpError.
 */
async function expectPreservedTransportError(promise: Promise<unknown>) {
  const error = await promise.then(
    () => {
      throw new Error('expected createConversation to reject');
    },
    (rejection: unknown) => rejection
  );
  expect(error).toBeInstanceOf(Error);
  expect(error).not.toBeInstanceOf(HttpError);
}

interface ServerOptions {
  /** Advertise the idempotent-create capability. */
  supportsRetry?: boolean;
  /** Drop the connection on the first POST after committing it. */
  commitThenDrop?: boolean;
  /** Reject the POST with an HTTP error rather than dropping it. */
  rejectPost?: boolean;
}

/**
 * Minimal agent server: serves `/server_info` for the capability probe and
 * `/api/conversations` for creates. Each scenario controls whether a dropped
 * POST had already committed server-side, which is the case the retry exists
 * for and the case a retry must not turn into a duplicate.
 */
function startServer(options: ServerOptions = {}) {
  const { supportsRetry = true, commitThenDrop = true, rejectPost = false } = options;
  const state = { creates: 0, committed: false, duplicates: 0 };

  const server = createServer((req, res) => {
    if (req.url?.startsWith('/server_info')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(
        JSON.stringify({
          version: '1.47.0',
          capabilities: supportsRetry ? ['idempotent_conversation_create_v1'] : [],
        })
      );
      return;
    }

    state.creates++;
    req.resume();
    req.on('end', () => {
      if (rejectPost) {
        res.writeHead(400, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ detail: 'invalid settings' }));
        return;
      }
      // A duplicate create on a server that dedupes returns the same
      // conversation; one that does not would start a second conversation here.
      if (state.committed && !supportsRetry) {
        state.duplicates++;
      }
      state.committed = true;
      // Only the first POST loses its response; the re-send must be answerable.
      if (commitThenDrop && state.creates === 1) {
        req.socket.destroy();
      } else {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ id: 'test-cid' }));
      }
    });
  });

  return { server, state };
}

async function listen(server: Server): Promise<string> {
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

async function close(server: Server): Promise<void> {
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

describe('conversation create response loss', () => {
  it('retries on a server that deduplicates concurrent creates', async () => {
    const { server, state } = startServer({ supportsRetry: true, commitThenDrop: true });
    const host = await listen(server);
    try {
      const result = await new ConversationClient({ host }).createConversation({
        conversation_id: 'test-cid',
        initial_message: { content: 'run once' },
      });
      expect(result.id).toBe('test-cid');
      // The committed create plus the re-send, which the server deduplicated.
      expect(state.creates).toBe(2);
      expect(state.duplicates).toBe(0);
    } finally {
      await close(server);
    }
  });

  it('does not replay the create against a server without the capability', async () => {
    const { server, state } = startServer({ supportsRetry: false, commitThenDrop: true });
    const host = await listen(server);
    try {
      await expectPreservedTransportError(
        new ConversationClient({ host }).createConversation({
          conversation_id: 'test-cid',
          initial_message: { content: 'run once' },
        })
      );
      // Replaying here would have created a second conversation and re-run the
      // initial message, so the transport error must surface instead.
      expect(state.creates).toBe(1);
    } finally {
      await close(server);
    }
  });

  it('does not retry when the caller supplied no conversation id', async () => {
    const { server, state } = startServer({ supportsRetry: true, commitThenDrop: true });
    const host = await listen(server);
    try {
      await expectPreservedTransportError(new ConversationClient({ host }).createConversation({}));
      expect(state.creates).toBe(1);
    } finally {
      await close(server);
    }
  });

  it('does not conceal server validation failures', async () => {
    const { server, state } = startServer({ supportsRetry: true, rejectPost: true });
    const host = await listen(server);
    try {
      await expect(
        new ConversationClient({ host }).createConversation({
          conversation_id: 'test-cid',
        })
      ).rejects.toMatchObject({ status: 400 });
      expect(state.creates).toBe(1);
    } finally {
      await close(server);
    }
  });
});
