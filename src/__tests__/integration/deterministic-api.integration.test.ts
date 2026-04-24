import { Agent, ConversationManager, HttpError, Workspace } from '../../index';
import { getServerTestConfig } from './test-config';
import {
  deleteWorkspaceFile,
  readWorkspaceFile,
  sleep,
  uniqueFileName,
  workspaceFileExists,
} from './test-utils';

const config = getServerTestConfig();

function createDummyAgent(): Agent {
  return new Agent({
    llm: {
      model: 'dummy/model',
      api_key: 'dummy-key',
    },
  });
}

describe('Deterministic API Integration Tests', () => {
  const manager = new ConversationManager({
    host: config.agentServerUrl,
  });
  const workspace = new Workspace({
    host: config.agentServerUrl,
    workingDir: config.agentWorkspaceDir,
  });

  afterAll(() => {
    manager.close();
    workspace.close();
  });

  it(
    'reads server, metadata, settings, tools, and skills endpoints',
    async () => {
      const root = await manager.server.getRoot<Record<string, unknown>>();
      const alive = await manager.server.getAlive();
      const health = await manager.server.getHealth();
      const ready = await manager.server.getReady();
      const info = await manager.server.getServerInfo();

      const providers = await manager.llm.getProviders();
      const models = await manager.llm.getModels();
      const verifiedModels = await manager.llm.getVerifiedModels();
      const agentSchema = await manager.settings.getAgentSchema();
      const conversationSchema = await manager.settings.getConversationSchema();
      const tools = await manager.tools.listTools();
      const skills = await manager.skills.getSkills({
        load_public: false,
        load_user: false,
        load_project: false,
        load_org: false,
      });
      const vscodeStatus = await manager.vscode.getStatus();

      expect(root).toBeDefined();
      expect(alive.status).toBe('ok');
      expect(health).toBe('OK');
      expect(['ready', 'initializing']).toContain(ready.status);
      expect(info.version).toBeDefined();
      expect(Array.isArray(providers)).toBe(true);
      expect(Array.isArray(models)).toBe(true);
      expect(typeof verifiedModels).toBe('object');
      expect(agentSchema.model_name).toBeTruthy();
      expect(conversationSchema.model_name).toBeTruthy();
      expect(Array.isArray(tools)).toBe(true);
      expect(Array.isArray(skills.skills)).toBe(true);
      expect(typeof vscodeStatus.enabled).toBe('boolean');

      try {
        const desktopUrl = await manager.desktop.getUrl();
        expect(desktopUrl === null || typeof desktopUrl === 'string').toBe(true);
      } catch (error) {
        expect(error).toBeInstanceOf(HttpError);
        expect((error as HttpError).status).toBe(503);
      }
    },
    config.testTimeout
  );

  it(
    'supports deterministic conversation, event, fork, and ACP endpoints',
    async () => {
      const beforeCount = await manager.countConversations();
      const conversation = await manager.createConversation(createDummyAgent(), {
        workingDir: config.agentWorkspaceDir,
      });
      let forkedId: string | undefined;
      let acpConversationId: string | undefined;

      try {
        const afterCount = await manager.countConversations();
        expect(afterCount).toBeGreaterThanOrEqual(beforeCount);

        const batch = await manager.getConversations([conversation.id]);
        expect(batch[0]?.id).toBe(conversation.id);

        await conversation.sendMessage('Deterministic integration message');
        const searchResult = await conversation.state.events.search({
          limit: 10,
          sort_order: 'TIMESTAMP_DESC',
        });
        expect(searchResult.items.length).toBeGreaterThan(0);

        const eventId = searchResult.items[0].id;
        const fetchedEvent = await conversation.state.events.getEventById(eventId);
        const fetchedBatch = await conversation.state.events.getEventsById([eventId]);
        expect(fetchedEvent.id).toBe(eventId);
        expect(fetchedBatch[0]?.id).toBe(eventId);

        const finalResponse = await conversation.getAgentFinalResponse();
        expect(typeof finalResponse).toBe('string');

        const trajectoryFile = `/workspace/conversations/${conversation.id.replace(/-/g, '')}.zip`;
        await workspace.executeCommand(
          `mkdir -p /workspace/conversations && printf 'trajectory-data' > ${trajectoryFile}`
        );
        const trajectory = await conversation.downloadTrajectory();
        expect(trajectory).toBeInstanceOf(Blob);
        expect(await trajectory.text()).toContain('trajectory-data');
        await workspace.executeCommand(`rm -f ${trajectoryFile}`);

        const forkedConversation = await conversation.fork({
          title: 'Forked from deterministic test',
        });
        forkedId = forkedConversation.id;
        expect(forkedConversation.id).not.toBe(conversation.id);

        await expect(
          conversation.switchProfile('__profile_that_should_not_exist__')
        ).rejects.toBeInstanceOf(HttpError);

        const acpConversation = await manager.acp.createConversation(
          {
            kind: 'Agent',
            llm: { model: 'dummy/model', api_key: 'dummy-key' },
          },
          { workingDir: config.agentWorkspaceDir }
        );
        acpConversationId = acpConversation.id;

        const acpCount = await manager.acp.countConversations();
        const fetchedACP = await manager.acp.getConversation(acpConversation.id);
        const batchACP = await manager.acp.getConversations([acpConversation.id]);
        expect(acpCount).toBeGreaterThan(0);
        expect(fetchedACP.id).toBe(acpConversation.id);
        expect(batchACP[0]?.id).toBe(acpConversation.id);
      } finally {
        if (forkedId) {
          await manager.deleteConversation(forkedId).catch(() => undefined);
        }
        if (acpConversationId) {
          await manager.deleteConversation(acpConversationId).catch(() => undefined);
        }
        await manager.deleteConversation(conversation.id).catch(() => undefined);
      }
    },
    config.testTimeout
  );

  it(
    'uses preferred workspace file and git query endpoints',
    async () => {
      const fileName = uniqueFileName('deterministic-workspace');
      const fileContent = 'workspace content from deterministic test';
      const destinationPath = `${config.agentWorkspaceDir}/${fileName}`;
      const repoDir = `${config.agentWorkspaceDir}/git-query-test`;
      const trackedFile = `${repoDir}/tracked.txt`;

      try {
        const uploadResult = await workspace.uploadText(fileContent, destinationPath, fileName);
        expect(uploadResult.success).toBe(true);

        await sleep(300);
        expect(workspaceFileExists(fileName)).toBe(true);
        expect(readWorkspaceFile(fileName)).toBe(fileContent);

        const downloaded = await workspace.downloadAsText(destinationPath);
        expect(downloaded).toBe(fileContent);

        await workspace.executeCommand(
          [
            `rm -rf ${repoDir}`,
            `mkdir -p ${repoDir}`,
            `cd ${repoDir}`,
            'git init',
            'git config user.email tester@example.com',
            'git config user.name tester',
            'printf "line1\\n" > tracked.txt',
            'git add tracked.txt',
            'git commit -m initial',
            'printf "line2\\n" >> tracked.txt',
          ].join(' && ')
        );

        const changes = await workspace.gitChanges(repoDir);
        const diff = await workspace.gitDiff(trackedFile);

        expect(changes.some((change) => String(change.path).includes('tracked.txt'))).toBe(true);
        expect(diff.modified || diff.diff).toContain('line2');
      } finally {
        deleteWorkspaceFile(fileName);
        await workspace.executeCommand(`rm -rf ${repoDir}`);
      }
    },
    config.testTimeout
  );
});
