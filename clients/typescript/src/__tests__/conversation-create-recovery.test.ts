import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { ConversationClient } from '../client/conversation-client';

describe('conversation create response loss', () => {
  let server: Server;
  let host: string;
  let creates: number;
  let exists: boolean;
  let reject: boolean;
  let saveBeforeDrop: boolean;
  let reads: number;
  let readStatus: number;
  beforeEach(async () => {
    creates = 0;
    readStatus = 404;
    exists = false;
    reject = false;
    saveBeforeDrop = true;
    reads = 0;
    server = createServer((req, res) => {
      if (req.method === 'POST') {
        creates++;
        req.resume();
        req.on('end', () => {
          if (reject) {
            res.writeHead(400, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ detail: 'invalid settings' }));
          } else {
            exists = saveBeforeDrop;
            req.socket.destroy();
          }
        });
      } else {
        reads++;
        res.writeHead(exists ? 200 : readStatus, { 'content-type': 'application/json' });
        res.end(JSON.stringify(exists ? { id: 'test-cid' } : { detail: 'not found' }));
      }
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  afterEach(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  it('recovers the existing conversation without repeating its initial message', async () => {
    const result = await new ConversationClient({ host }).createConversation({
      conversation_id: 'test-cid',
      initial_message: { content: 'run once' },
    });
    expect(result.id).toBe('test-cid');
    expect(creates).toBe(1);
  });
  it('does not conceal server validation failures', async () => {
    reject = true;
    await expect(
      new ConversationClient({ host }).createConversation({
        conversation_id: 'test-cid',
      })
    ).rejects.toMatchObject({ status: 400 });
    expect(creates).toBe(1);
    expect(reads).toBe(0);
  });
  it('preserves the transport error if no conversation was saved', async () => {
    saveBeforeDrop = false;
    await expect(
      new ConversationClient({ host, creationRecoveryTimeout: 100 }).createConversation({
        conversation_id: 'test-cid',
      })
    ).rejects.toThrow('Unknown request error');
    expect(creates).toBe(1);
    expect(reads).toBeGreaterThanOrEqual(1);
  });
  it('stops recovery on an authorization failure', async () => {
    saveBeforeDrop = false;
    readStatus = 401;
    const started = Date.now();
    await expect(
      new ConversationClient({ host, creationRecoveryTimeout: 1000 }).createConversation({
        conversation_id: 'test-cid',
      })
    ).rejects.toThrow('Unknown request error');
    expect(reads).toBe(1);
    expect(creates).toBe(1);
    expect(Date.now() - started).toBeLessThan(1000);
  });
  it('does not guess a conversation id when the caller supplied none', async () => {
    await expect(new ConversationClient({ host }).createConversation({})).rejects.toThrow(
      'Unknown request error'
    );
    expect(creates).toBe(1);
    expect(reads).toBe(0);
  });
});

describe('cold creation after client timeout', () => {
  it('recovers when startup finishes after the first reconciliation read', async () => {
    let ready = false;
    let creates = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const server = createServer((req, res) => {
      if (req.method === 'POST') {
        creates++;
        req.resume();
        req.on('end', () => {
          timer = setTimeout(() => {
            ready = true;
            res.writeHead(200, { 'content-type': 'application/json' });
            res.end(JSON.stringify({ id: 'cold-cid' }));
          }, 200);
        });
      } else {
        res.writeHead(ready ? 200 : 404, { 'content-type': 'application/json' });
        res.end(JSON.stringify(ready ? { id: 'cold-cid' } : { detail: 'starting' }));
      }
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    try {
      const host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
      await expect(
        new ConversationClient({ host, timeout: 50 }).createConversation({
          conversation_id: 'cold-cid',
        })
      ).resolves.toMatchObject({ id: 'cold-cid' });
      expect(creates).toBe(1);
    } finally {
      if (timer) clearTimeout(timer);
      server.closeAllConnections();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    }
  });
});
