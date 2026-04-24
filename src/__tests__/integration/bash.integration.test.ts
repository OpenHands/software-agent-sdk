import { BashOutput, BashWebSocketClient, Workspace } from '../../index';
import { getServerTestConfig } from './test-config';
import { waitFor, sleep } from './test-utils';

const config = getServerTestConfig();

describe('Bash API Integration Tests', () => {
  const workspace = new Workspace({
    host: config.agentServerUrl,
    workingDir: config.agentWorkspaceDir,
  });
  const bash = workspace.bash;

  afterAll(() => {
    workspace.close();
  });

  it(
    'supports start, search, get, batch-get, execute, and clear bash endpoints',
    async () => {
      await bash.clearEvents();

      const command = await bash.startCommand('printf "bash-search-test"', undefined, 5);
      let outputEvent: BashOutput | undefined;

      await waitFor(
        async () => {
          const page = await bash.searchEvents({ command_id__eq: command.id, limit: 20 });
          outputEvent = page.items.find((event) => event.kind === 'BashOutput') as
            | BashOutput
            | undefined;
          return Boolean(outputEvent?.exit_code !== undefined);
        },
        { timeout: config.testTimeout, interval: 250, message: 'bash output was not produced' }
      );

      const fetchedCommand = await bash.getEvent(command.id);
      const batch = await bash.getEvents([command.id, outputEvent!.id]);
      const executeResult = await bash.executeCommand('printf "bash-execute-test"');
      const cleared = await bash.clearEvents();

      expect(fetchedCommand.id).toBe(command.id);
      expect(batch[0]?.id).toBe(command.id);
      expect(batch[1]?.id).toBe(outputEvent!.id);
      expect(outputEvent?.stdout).toContain('bash-search-test');
      expect(executeResult.stdout).toContain('bash-execute-test');
      expect(cleared.cleared_count).toBeGreaterThanOrEqual(1);
    },
    config.testTimeout
  );

  it(
    'streams bash events over the websocket endpoint',
    async () => {
      const receivedEvents: Array<{ kind?: string; stdout?: string | null }> = [];
      const wsClient = new BashWebSocketClient({
        host: config.agentServerUrl,
        callback: (event) => {
          receivedEvents.push(event);
        },
      });

      try {
        wsClient.start();
        await sleep(500);
        await bash.startCommand('printf "bash-websocket-test"', undefined, 5);

        await waitFor(
          () =>
            receivedEvents.some(
              (event) =>
                event.kind === 'BashOutput' && event.stdout?.includes('bash-websocket-test')
            ),
          {
            timeout: config.testTimeout,
            interval: 250,
            message: 'bash websocket output was not received',
          }
        );
      } finally {
        wsClient.stop();
        await bash.clearEvents().catch(() => undefined);
      }
    },
    config.testTimeout
  );
});
