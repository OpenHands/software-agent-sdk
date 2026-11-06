import { createServer, Server } from 'node:http';
import { AddressInfo } from 'node:net';
import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('RemoteWorkspace.executeCommand exit code', () => {
  let server: Server;
  let host: string;
  let body: Record<string, unknown> = {};
  beforeAll(async () => {
    server = createServer((_req, res) => {
      res.setHeader('content-type', 'application/json');
      res.end(JSON.stringify(body));
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    host = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  const run = () => new RemoteWorkspace({ host, workingDir: '/workspace' }).executeCommand('x');

  it('does not report a missing exit code as success', async () => {
    body = { stdout: 'partial', stderr: null, exit_code: null };
    expect((await run()).exit_code).toBe(-1);
    body = { stdout: 'partial' };
    expect((await run()).exit_code).toBe(-1);
  });

  it('keeps an explicit exit code', async () => {
    body = { stdout: 'ok', stderr: null, exit_code: 0 };
    const result = await run();
    expect(result.exit_code).toBe(0);
    expect(result.stdout).toBe('ok');
    expect(result.stderr).toBe('');
    body = { exit_code: 7 };
    expect((await run()).exit_code).toBe(7);
  });
});
