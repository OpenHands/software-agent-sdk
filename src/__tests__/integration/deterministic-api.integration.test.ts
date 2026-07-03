import { Agent, ConversationManager, HttpError, Workspace } from '../../index';
import { ConversationClient } from '../../clients';
import { getServerTestConfig } from './test-config';
import {
  deleteWorkspaceFile,
  readWorkspaceFile,
  sleep,
  uniqueDirName,
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
      expect(health.status).toBe('ok');
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

        const trajectory = await conversation.downloadTrajectory();
        expect(trajectory).toBeInstanceOf(Blob);
        expect(trajectory.size).toBeGreaterThan(0);
        const trajectoryBytes = new Uint8Array(await trajectory.arrayBuffer());
        // Verify it's a valid ZIP file (PK\x03\x04 magic bytes)
        expect(trajectoryBytes[0]).toBe(0x50); // P
        expect(trajectoryBytes[1]).toBe(0x4b); // K

        const forkedConversation = await conversation.fork({
          title: 'Forked from deterministic test',
        });
        forkedId = forkedConversation.id;
        expect(forkedConversation.id).not.toBe(conversation.id);

        await expect(
          conversation.switchProfile('__profile_that_should_not_exist__')
        ).rejects.toBeInstanceOf(HttpError);

        // switchLlm swaps the LLM in place, keyed by usage_id (first-write-wins
        // in the registry). Reusing the agent's existing usage_id would be a
        // silent no-op, so use a fresh one and read the model back off the
        // agent to prove the swap actually took effect.
        await conversation.switchLlm({
          model: 'dummy/switched-model',
          api_key: 'dummy-key',
          usage_id: 'switched',
        });
        const switchedAgent = await conversation.state.getAgent();
        expect(switchedAgent.llm.model).toBe('dummy/switched-model');

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

        const headChanges = await workspace.gitChanges(repoDir, { ref: 'HEAD' });
        expect(headChanges.some((change) => String(change.path).includes('tracked.txt'))).toBe(
          true
        );

        const headDiff = await workspace.gitDiff(trackedFile, { ref: 'HEAD' });
        expect(headDiff.modified || headDiff.diff).toContain('line2');
      } finally {
        deleteWorkspaceFile(fileName);
        await workspace.executeCommand(`rm -rf ${repoDir}`);
      }
    },
    config.testTimeout
  );

  it(
    'round-trips a profile through save, get, list, activate, rename, and delete',
    async () => {
      const profileName = uniqueDirName('it-profile');
      const renamedProfile = `${profileName}-renamed`;
      const llm = { model: 'dummy/model', api_key: 'dummy-key' };

      try {
        const saved = await manager.profiles.saveProfile(profileName, { llm });
        expect(saved.name).toBe(profileName);

        const detail = await manager.profiles.getProfile(profileName);
        expect(detail.name).toBe(profileName);
        // The saved LLM config round-trips...
        expect(detail.config.model).toBe('dummy/model');
        // ...but the default (no X-Expose-Secrets) response nulls api_key and
        // reports the presence of the saved key via api_key_set instead.
        expect(detail.config.api_key).toBeNull();
        expect(detail.api_key_set).toBe(true);

        const list = await manager.profiles.listProfiles();
        expect(list.profiles.some((profile) => profile.name === profileName)).toBe(true);

        const activated = await manager.profiles.activateProfile(profileName);
        expect(activated.name).toBe(profileName);
        expect(activated.llm_applied).toBe(true);

        const afterActivate = await manager.profiles.listProfiles();
        expect(afterActivate.active_profile).toBe(profileName);

        const renamed = await manager.profiles.renameProfile(profileName, renamedProfile);
        expect(renamed.name).toBe(renamedProfile);

        const renamedDetail = await manager.profiles.getProfile(renamedProfile);
        expect(renamedDetail.name).toBe(renamedProfile);
        // The config survives the (atomic) rename...
        expect(renamedDetail.config.model).toBe('dummy/model');
        // ...and because the renamed profile was the active one, the
        // active_profile pointer follows it to the new name.
        const afterRename = await manager.profiles.listProfiles();
        expect(afterRename.active_profile).toBe(renamedProfile);
        // The original name stops resolving once the profile is renamed.
        await expect(manager.profiles.getProfile(profileName)).rejects.toBeInstanceOf(HttpError);

        const deleted = await manager.profiles.deleteProfile(renamedProfile);
        expect(deleted.name).toBe(renamedProfile);

        const finalList = await manager.profiles.listProfiles();
        expect(finalList.profiles.some((profile) => profile.name === renamedProfile)).toBe(false);
      } finally {
        await manager.profiles.deleteProfile(profileName).catch(() => undefined);
        await manager.profiles.deleteProfile(renamedProfile).catch(() => undefined);
      }
    },
    config.testTimeout
  );

  it(
    'switchAcpModel reaches the route and rejects a non-ACP conversation with 400',
    async () => {
      // Contract guard for switchAcpModel against a real agent-server. The
      // route POST /api/conversations/{id}/switch_acp_model was added in
      // software-agent-sdk #3390; a non-ACP conversation exercises it without
      // ACP credentials or a real model switch. A 400 (not a 404) proves the
      // route exists on the pinned image AND that the client targets the right
      // path/body — catching client<->server contract drift the mocked unit
      // tests cannot.
      const conversation = await manager.createConversation(createDummyAgent(), {
        workingDir: config.agentWorkspaceDir,
      });
      try {
        let status: number | undefined;
        try {
          await conversation.switchAcpModel('claude-haiku-4-5');
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          status = (error as HttpError).status;
        }
        expect(status).toBe(400);
      } finally {
        await manager.deleteConversation(conversation.id).catch(() => undefined);
      }
    },
    config.testTimeout
  );

  it(
    'exercises the /goal, /goal/resume, and /goal/stop routes without an LLM',
    async () => {
      // Contract guards for the goal endpoints (software-agent-sdk #3770,
      // released in v1.29.0 — the pinned image). These deliberately stay off
      // the happy path so they need neither a real LLM nor a background loop:
      //   - start with an empty objective is rejected by GoalController -> 400
      //   - resume with nothing to continue -> 400 ("no_resumable_goal")
      //   - stop with no active loop is a no-op Success -> 200
      // A 400/200 (not a 404) proves each route exists on the image AND that
      // the client targets the right path/method/body — client<->server
      // contract drift the mocked unit tests cannot catch.
      const conversation = await manager.createConversation(createDummyAgent(), {
        workingDir: config.agentWorkspaceDir,
      });
      try {
        let startStatus: number | undefined;
        try {
          await conversation.startGoal('');
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          startStatus = (error as HttpError).status;
        }
        expect(startStatus).toBe(400);

        let resumeStatus: number | undefined;
        try {
          await conversation.resumeGoal();
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          resumeStatus = (error as HttpError).status;
        }
        expect(resumeStatus).toBe(400);

        // Stopping when no goal loop is running is a no-op Success, not an error.
        await expect(conversation.stopGoal()).resolves.toBeUndefined();
      } finally {
        await manager.deleteConversation(conversation.id).catch(() => undefined);
      }
    },
    config.testTimeout
  );

  it(
    'returns 404 from the goal endpoints for an unknown conversation',
    async () => {
      // A well-formed but non-existent conversation UUID parses as the path
      // param and reaches each handler, which must answer 404. This is distinct
      // from a 422 (malformed UUID) or 405 (wrong method on a missing route),
      // so it confirms the route templates resolve and the not-found branch is
      // wired for all three goal endpoints.
      const client = new ConversationClient({ host: config.agentServerUrl });
      const missingId = '00000000-0000-0000-0000-000000000000';
      try {
        const calls: Array<() => Promise<unknown>> = [
          () => client.startGoal(missingId, { objective: 'noop' }),
          () => client.resumeGoal(missingId),
          () => client.stopGoal(missingId),
        ];
        for (const call of calls) {
          let status: number | undefined;
          try {
            await call();
          } catch (error) {
            expect(error).toBeInstanceOf(HttpError);
            status = (error as HttpError).status;
          }
          expect(status).toBe(404);
        }
      } finally {
        client.close();
      }
    },
    config.testTimeout
  );

  it(
    'reaches the skills refresh route and 404s for an uninstalled skill',
    async () => {
      // Contract guard for POST /api/skills/installed/{name}/refresh (the route
      // is /refresh, not /update). A valid-but-uninstalled skill name reaches
      // the handler, which must answer 404 — proving the route exists on the
      // image and the client targets the corrected path.
      let status: number | undefined;
      try {
        await manager.skills.refreshSkill('nonexistent-skill');
      } catch (error) {
        expect(error).toBeInstanceOf(HttpError);
        status = (error as HttpError).status;
      }
      expect(status).toBe(404);
    },
    config.testTimeout
  );

  it(
    'navigate re-roots the HEAD across existing events and back to the empty tree',
    async () => {
      // Contract + semantics guard for POST /api/conversations/{id}/navigate
      // (software-agent-sdk #3923, released in v1.31.0 — the pinned image).
      // Two user messages create >=2 events without an LLM; navigate then moves
      // the conversation HEAD (leaf_event_id) across them in place — no fork, no
      // new conversation. This proves the route exists on the image AND that the
      // client targets the right path/body while carrying back the new leaf —
      // client<->server contract drift the mocked unit tests cannot catch.
      const client = new ConversationClient({ host: config.agentServerUrl });
      const conversation = await manager.createConversation(createDummyAgent(), {
        workingDir: config.agentWorkspaceDir,
      });
      try {
        await conversation.sendMessage('First navigate message');
        await conversation.sendMessage('Second navigate message');

        // Ascending sort => items[0] is the earliest event, last is the tail.
        const events = await conversation.state.events.search({
          limit: 50,
          sort_order: 'TIMESTAMP',
        });
        expect(events.items.length).toBeGreaterThanOrEqual(2);
        const firstEventId = events.items[0].id;
        const lastEventId = events.items[events.items.length - 1].id;

        // Re-root HEAD onto the earliest event; the response carries the new leaf.
        const rerooted = await client.navigateConversation(conversation.id, {
          event_id: firstEventId,
        });
        expect(rerooted.leaf_event_id).toBe(firstEventId);

        // A null event_id selects the empty tree (a deliberate new root).
        const emptied = await client.navigateConversation(conversation.id, {
          event_id: null,
        });
        expect(emptied.leaf_event_id).toBeNull();

        // The high-level wrapper reaches the same route and refreshes cached
        // state without throwing; restore the HEAD to the original tail and read
        // it back off the server to prove the move persisted.
        await conversation.navigateTo(lastEventId);
        const restored = await manager.getConversations([conversation.id]);
        expect(restored[0]?.leaf_event_id).toBe(lastEventId);
      } finally {
        client.close();
        await manager.deleteConversation(conversation.id).catch(() => undefined);
      }
    },
    config.testTimeout
  );

  it(
    'navigate 404s for an unknown conversation and an unknown event id',
    async () => {
      // Both not-found branches of the navigate route. A well-formed but
      // non-existent conversation UUID reaches the handler -> "Conversation not
      // found" 404. A real conversation with a bogus event_id hits the server's
      // event_id ValueError branch -> 404 (not a 500). Together they confirm the
      // route template resolves and both not-found paths are wired.
      const client = new ConversationClient({ host: config.agentServerUrl });
      const missingId = '00000000-0000-0000-0000-000000000000';
      const conversation = await manager.createConversation(createDummyAgent(), {
        workingDir: config.agentWorkspaceDir,
      });
      try {
        let unknownConversationStatus: number | undefined;
        try {
          await client.navigateConversation(missingId, { event_id: null });
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          unknownConversationStatus = (error as HttpError).status;
        }
        expect(unknownConversationStatus).toBe(404);

        let unknownEventStatus: number | undefined;
        try {
          await client.navigateConversation(conversation.id, {
            event_id: 'event-that-does-not-exist',
          });
        } catch (error) {
          expect(error).toBeInstanceOf(HttpError);
          unknownEventStatus = (error as HttpError).status;
        }
        expect(unknownEventStatus).toBe(404);
      } finally {
        client.close();
        await manager.deleteConversation(conversation.id).catch(() => undefined);
      }
    },
    config.testTimeout
  );
});
