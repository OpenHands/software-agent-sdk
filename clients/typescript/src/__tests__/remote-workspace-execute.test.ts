import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('RemoteWorkspace command results', () => {
  let workspace: RemoteWorkspace;

  beforeEach(() => {
    workspace = new RemoteWorkspace({ host: 'https://example.com', workingDir: '/workspace' });
  });

  afterEach(() => {
    workspace.close();
    vi.restoreAllMocks();
  });

  function mockExecuteBashOutput(body: unknown) {
    vi.spyOn(global, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    );
  }

  it('reports a missing exit code as failure, not success', async () => {
    // A non-final output slice carries no exit code; it must not read as 0.
    mockExecuteBashOutput({ order: 99, exit_code: null, stdout: 'partial' });

    const result = await workspace.executeCommand('yes a | head -c 106000000; exit 7');

    expect(result.exit_code).toBe(-1);
  });

  it.each([0, 7])('keeps the explicit exit code %s as-is', async (exitCode) => {
    mockExecuteBashOutput({ exit_code: exitCode, stdout: 'output', stderr: '' });

    const result = await workspace.executeCommand('some command');

    expect(result.exit_code).toBe(exitCode);
    expect(result.stdout).toBe('output');
  });
});
