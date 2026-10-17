import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { ConversationManager } from '../conversation/conversation-manager';
import { RemoteConversation } from '../conversation/remote-conversation';
import type { AgentBase } from '../types/base';
import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('conversation create options', () => {
  let server: Server;
  let host: string;
  const bodies: Record<string, unknown>[] = [];
  const agent = { kind: 'Agent', llm: { model: 'test/model' } } as unknown as AgentBase;

  beforeAll(async () => {
    server = createServer((req, res) => {
      let raw = '';
      req.on('data', (chunk) => (raw += chunk));
      req.on('end', () => {
        res.setHeader('content-type', 'application/json');
        if (req.url === '/server_info') {
          res.end(JSON.stringify({ version: '1.47.0', capabilities: [] }));
          return;
        }
        if (req.method === 'POST' && req.url === '/api/conversations') {
          bodies.push(JSON.parse(raw));
          res.end(JSON.stringify({ id: 'created', workspace: { working_dir: '/workspace' } }));
          return;
        }
        res.statusCode = 404;
        res.end('{}');
      });
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  afterAll(async () => {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });
  beforeEach(() => {
    bodies.length = 0;
  });

  it('ConversationManager.createConversation sends maxIterations and stuckDetection', async () => {
    const manager = new ConversationManager({ host });
    await manager.createConversation(agent, { maxIterations: 7, stuckDetection: false });

    expect(bodies).toHaveLength(1);
    expect(bodies[0]).toMatchObject({ max_iterations: 7, stuck_detection: false });
  });

  it('ConversationManager.createConversation keeps the defaults when both are omitted', async () => {
    const manager = new ConversationManager({ host });
    await manager.createConversation(agent);

    expect(bodies[0]).toMatchObject({ max_iterations: 500, stuck_detection: true });
  });

  it('RemoteConversation uses its constructor options, overridden by start() options', async () => {
    const workspace = new RemoteWorkspace({ host, workingDir: '/workspace' });
    const options = { maxIterations: 7, stuckDetection: false };

    await new RemoteConversation(agent, workspace, options).start();
    await new RemoteConversation(agent, workspace, options).start({
      maxIterations: 9,
      stuckDetection: true,
    });

    expect(bodies[0]).toMatchObject({ max_iterations: 7, stuck_detection: false });
    expect(bodies[1]).toMatchObject({ max_iterations: 9, stuck_detection: true });
  });
});
