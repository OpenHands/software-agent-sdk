import {
  Agent,
  ConversationExecutionStatus,
  ConversationManager,
  HttpClient,
  HttpError,
  RemoteConversation,
  RemoteEventsList,
  RemoteWorkspace,
  Workspace,
} from '../index';
import {
  AgentProfilesClient,
  AgentServerVersionError,
  BashClient,
  clearAgentServerInfoCache,
  compareAgentServerVersions,
  ConversationClient,
  FileClient,
  HooksClient,
  isAgentServerVersionError,
  LLMMetadataClient,
  MCPClient,
  MetaProfilesClient,
  PluginsClient,
  ProfilesClient,
  ServerClient,
  SettingsClient,
  SharedClient,
  SkillsClient,
  WorkspacesClient,
} from '../clients';
import * as http from 'node:http';
import { EventEmitter } from 'node:events';

// `fetch` rejects a GET body, so the batch endpoints route through node:http.
// Its `request` export isn't configurable (jest.spyOn fails), so mock the module.
jest.mock('node:http', () => ({
  ...jest.requireActual('node:http'),
  request: jest.fn(),
}));

const originalFetch = global.fetch;

/**
 * Drive the mocked Node `http.request` for the GET-with-body transport path used
 * by the batch endpoints. Captures the request URL/options/body and replays a
 * canned JSON response.
 */
function mockNodeHttpRequest(responseBody: string, status = 200) {
  const captured: { url?: URL; options?: http.RequestOptions; body?: string } = {};
  (http.request as jest.Mock).mockImplementation((url: unknown, options: unknown, cb: unknown) => {
    captured.url = url as URL;
    captured.options = options as http.RequestOptions;
    const callback = cb as (res: http.IncomingMessage) => void;
    const req = new EventEmitter() as unknown as http.ClientRequest;
    (req as unknown as { setTimeout: unknown }).setTimeout = jest.fn();
    (req as unknown as { destroy: unknown }).destroy = jest.fn();
    (req as unknown as { end: unknown }).end = jest.fn((body?: string) => {
      captured.body = body;
      process.nextTick(() => {
        const res = new EventEmitter() as unknown as http.IncomingMessage;
        res.statusCode = status;
        res.statusMessage = status === 200 ? 'OK' : 'Error';
        res.headers = { 'content-type': 'application/json' };
        callback(res);
        res.emit('data', Buffer.from(responseBody));
        res.emit('end');
      });
    });
    return req;
  });
  return { captured };
}

describe('Auxiliary API clients', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    clearAgentServerInfoCache();
    jest.restoreAllMocks();
  });

  it('ConversationManager exposes server and skills namespaces', () => {
    const manager = new ConversationManager({ host: 'http://example.com', apiKey: 'secret' });

    expect(manager.server).toBeInstanceOf(ServerClient);
    expect(manager.skills).toBeInstanceOf(SkillsClient);
    expect(manager.profiles).toBeInstanceOf(ProfilesClient);
    expect(manager.agentProfiles).toBeInstanceOf(AgentProfilesClient);
    expect(manager.metaProfiles).toBeInstanceOf(MetaProfilesClient);
    expect(manager.server.host).toBe('http://example.com');
    expect(manager.server.apiKey).toBe('secret');
    expect(manager.profiles.host).toBe('http://example.com');
    expect(manager.profiles.apiKey).toBe('secret');
    expect(manager.agentProfiles.host).toBe('http://example.com');
    expect(manager.agentProfiles.apiKey).toBe('secret');
    expect(manager.metaProfiles.host).toBe('http://example.com');
    expect(manager.metaProfiles.apiKey).toBe('secret');
    expect(manager.files).toBeInstanceOf(FileClient);
    expect(manager.workspaces).toBeInstanceOf(WorkspacesClient);
    expect(manager.shared).toBeInstanceOf(SharedClient);
    expect(manager.hooks).toBeInstanceOf(HooksClient);
    expect(manager.mcp).toBeInstanceOf(MCPClient);
  });

  describe('AgentProfilesClient', () => {
    it('listAgentProfiles fetches /api/agent-profiles', async () => {
      const payload = {
        profiles: [
          {
            id: 'uuid-1',
            name: 'default',
            agent_kind: 'openhands',
            revision: 0,
            llm_profile_ref: 'gpt-4o',
            mcp_server_refs: null,
          },
        ],
        active_agent_profile_id: 'uuid-1',
      };
      global.fetch = jest.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com', apiKey: 'k' });
      const result = await client.listAgentProfiles();

      expect(result.active_agent_profile_id).toBe('uuid-1');
      expect(result.profiles).toHaveLength(1);
      expect(result.profiles[0].name).toBe('default');
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/agent-profiles',
        expect.objectContaining({ method: 'GET' })
      );
    });

    it('getAgentProfile sets X-Expose-Secrets header when requested', async () => {
      const payload = { name: 'default', profile: { agent_kind: 'openhands' } };
      global.fetch = jest.fn().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com' });
      await client.getAgentProfile('default', { exposeSecrets: 'plaintext' });

      const call = (global.fetch as jest.Mock).mock.calls[0];
      expect(call[0]).toBe('http://example.com/api/agent-profiles/default');
      const headers = call[1]?.headers as Record<string, string>;
      expect(headers['X-Expose-Secrets']).toBe('plaintext');
    });

    it('saveAgentProfile posts to /api/agent-profiles/{name}', async () => {
      global.fetch = jest.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ name: 'myprofile', message: "Agent profile 'myprofile' saved" }),
          {
            status: 201,
            headers: { 'content-type': 'application/json' },
          }
        )
      ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com' });
      const result = await client.saveAgentProfile('myprofile', {
        agent_kind: 'openhands',
        llm_profile_ref: 'gpt-4o',
      });

      expect(result.name).toBe('myprofile');
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/agent-profiles/myprofile',
        expect.objectContaining({ method: 'POST' })
      );
      const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body as string);
      expect(body).toEqual({ agent_kind: 'openhands', llm_profile_ref: 'gpt-4o' });
    });

    it('deleteAgentProfile sends DELETE to /api/agent-profiles/{name}', async () => {
      global.fetch = jest.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ name: 'myprofile', message: "Agent profile 'myprofile' deleted" }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }
        )
      ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com' });
      const result = await client.deleteAgentProfile('myprofile');

      expect(result.name).toBe('myprofile');
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/agent-profiles/myprofile',
        expect.objectContaining({ method: 'DELETE' })
      );
    });

    it('renameAgentProfile posts new_name', async () => {
      global.fetch = jest.fn().mockResolvedValue(
        new Response(JSON.stringify({ name: 'newname', message: 'renamed' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com' });
      await client.renameAgentProfile('oldname', 'newname');

      const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body as string);
      expect(body.new_name).toBe('newname');
    });

    it('activateAgentProfile posts to /{profileId}/activate', async () => {
      const profileId = 'uuid-abc-123';
      global.fetch = jest
        .fn()
        .mockResolvedValue(
          new Response(
            JSON.stringify({ id: profileId, message: 'activated', agent_settings_applied: false }),
            { status: 200, headers: { 'content-type': 'application/json' } }
          )
        ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com' });
      const result = await client.activateAgentProfile(profileId);

      expect(result.id).toBe(profileId);
      expect(result.agent_settings_applied).toBe(false);
      expect(global.fetch).toHaveBeenCalledWith(
        `http://example.com/api/agent-profiles/${profileId}/activate`,
        expect.objectContaining({ method: 'POST' })
      );
    });

    it('materializeAgentProfile posts to /{name}/materialize', async () => {
      const diagnostics = {
        agent_kind: 'openhands',
        valid: true,
        errors: [],
        llm_profile_ref: 'gpt-4o',
        llm_profile_resolved: true,
        llm_api_key_set: true,
        mcp_server_refs: null,
        resolved_mcp_servers: [],
        dangling_mcp_server_refs: [],
        acp_api_key_secret_name: null,
        acp_base_url_secret_name: null,
        acp_file_secret_names: [],
        resolved_settings: {},
      };
      global.fetch = jest.fn().mockResolvedValue(
        new Response(JSON.stringify(diagnostics), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      ) as typeof fetch;

      const client = new AgentProfilesClient({ host: 'http://example.com' });
      const result = await client.materializeAgentProfile('default');

      expect(result.valid).toBe(true);
      expect(result.llm_profile_ref).toBe('gpt-4o');
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/agent-profiles/default/materialize',
        expect.objectContaining({ method: 'POST' })
      );
    });
  });

  it('Workspace exposes bash namespace', () => {
    const workspace = new Workspace({
      host: 'http://example.com',
      workingDir: '/tmp',
      apiKey: 'secret',
    });

    expect(workspace.bash).toBeInstanceOf(BashClient);
    expect(workspace.bash.host).toBe('http://example.com');
    expect(workspace.bash.apiKey).toBe('secret');
  });

  it('ServerClient.getReady accepts a 503 readiness response', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'initializing', message: 'Booting' }), {
        status: 503,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ServerClient({ host: 'http://example.com' });
    const ready = await client.getReady();

    expect(ready.status).toBe('initializing');
    expect(ready.message).toBe('Booting');
  });

  it('RemoteEventsList can be constructed from client options', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_page_id: null }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const events = new RemoteEventsList({ baseUrl: 'http://example.com', apiKey: 'secret' }, 'c1');
    const page = await events.search({ limit: 25 });

    expect(page.items).toEqual([]);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/c1/events/search?limit=25',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({
          'X-Session-API-Key': 'secret',
        }),
      })
    );
  });

  it('ConversationClient.switchLLM posts an explicit LLM config', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    await client.switchLLM('c1', { model: 'gpt-4o', api_key: 'encrypted' });

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/c1/switch_llm',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ llm: { model: 'gpt-4o', api_key: 'encrypted' } }),
      })
    );
  });

  it('LLMMetadataClient.getOpenAISubscriptionModels returns models array', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ vendor: 'openai', models: ['gpt-5.2', 'gpt-5.3-codex'] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new LLMMetadataClient({ host: 'http://example.com', apiKey: 'secret' });
    const models = await client.getOpenAISubscriptionModels();

    expect(models).toEqual(['gpt-5.2', 'gpt-5.3-codex']);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/llm/subscription/openai/models',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('LLMMetadataClient calls OpenAI subscription endpoints without exposing tokens', async () => {
    const responses = [
      { vendor: 'openai', connected: false, account_email: null, expires_at: null },
      {
        device_code: 'opaque-token',
        user_code: 'ABCD-EFGH',
        verification_uri: 'https://auth.example/device',
        verification_uri_complete: null,
        expires_at: 4102444800000,
        interval_seconds: 5,
      },
      { vendor: 'openai', connected: true, account_email: null, expires_at: 4102444800000 },
      { vendor: 'openai', connected: false, account_email: null, expires_at: null },
    ];
    global.fetch = jest.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(responses.shift()), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    ) as typeof fetch;

    const client = new LLMMetadataClient({ host: 'http://example.com', apiKey: 'secret' });

    await expect(client.getOpenAISubscriptionStatus()).resolves.toMatchObject({
      vendor: 'openai',
      connected: false,
    });
    await expect(client.startOpenAISubscriptionDeviceLogin()).resolves.toMatchObject({
      device_code: 'opaque-token',
      user_code: 'ABCD-EFGH',
    });
    await expect(client.pollOpenAISubscriptionDeviceLogin('opaque-token')).resolves.toMatchObject({
      connected: true,
      expires_at: 4102444800000,
    });
    await expect(client.logoutOpenAISubscription()).resolves.toMatchObject({ connected: false });

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/llm/subscription/openai/status',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/llm/subscription/openai/device/start',
      expect.objectContaining({ method: 'POST' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/llm/subscription/openai/device/poll',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ device_code: 'opaque-token' }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/llm/subscription/openai/logout',
      expect.objectContaining({ method: 'POST' })
    );
    expect(JSON.stringify((global.fetch as jest.Mock).mock.calls)).not.toContain('access-token');
    expect(JSON.stringify((global.fetch as jest.Mock).mock.calls)).not.toContain('refresh-token');
  });

  it('SkillsClient.syncSkills posts to the sync endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'success', message: 'ok' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new SkillsClient({ host: 'http://example.com' });
    const response = await client.syncSkills();

    expect(response.status).toBe('success');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/skills/sync',
      expect.objectContaining({
        method: 'POST',
        body: '{}',
      })
    );
  });

  it('SkillsClient CRUD methods map to the correct endpoints', async () => {
    const installedSkill = {
      name: 'my-skill',
      version: '1.0.0',
      description: 'A test skill',
      enabled: true,
      source: '/tmp/my-skill',
      installed_at: '2026-05-12T12:00:00Z',
      install_path: '/home/.openhands/skills/installed/my-skill',
    };
    const installedList = { skills: [{ name: 'my-skill', version: '1.0.0', enabled: true }] };
    const toggleResponse = { name: 'my-skill', enabled: false };
    const uninstallResponse = { message: "Skill 'my-skill' uninstalled" };
    const refreshResponse = {
      message: "Skill 'my-skill' updated",
      skill: { name: 'my-skill', version: '1.0.0', enabled: true },
    };
    const marketplaceResponse = {
      skills: [
        { name: 'my-skill', description: 'desc', source: 'github:org/repo', installed: false },
      ],
    };

    const responses = [
      installedSkill,
      installedList,
      installedSkill,
      toggleResponse,
      uninstallResponse,
      refreshResponse,
      marketplaceResponse,
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const client = new SkillsClient({ host: 'http://example.com' });

    const installed = await client.installSkill({ source: '/tmp/my-skill', force: false });
    expect(installed.name).toBe('my-skill');
    expect(installed.enabled).toBe(true);

    const list = await client.listInstalledSkills();
    expect(list.skills).toHaveLength(1);
    expect(list.skills[0].name).toBe('my-skill');

    const got = await client.getInstalledSkill('my-skill');
    expect(got.name).toBe('my-skill');

    const toggled = await client.toggleSkill('my-skill', false);
    expect(toggled.enabled).toBe(false);

    const uninstalled = await client.uninstallSkill('my-skill');
    expect(uninstalled.message).toContain('uninstalled');

    const refreshed = await client.refreshSkill('my-skill');
    expect(refreshed.message).toContain('updated');
    expect(refreshed.skill.name).toBe('my-skill');

    const marketplace = await client.getMarketplace();
    expect(marketplace.skills).toHaveLength(1);
    expect(marketplace.skills[0].installed).toBe(false);

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/skills/install',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ source: '/tmp/my-skill', force: false }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/skills/installed',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/skills/installed/my-skill',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/skills/installed/my-skill',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ enabled: false }) })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/skills/installed/my-skill',
      expect.objectContaining({ method: 'DELETE' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      6,
      'http://example.com/api/skills/installed/my-skill/refresh',
      expect.objectContaining({ method: 'POST' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      7,
      'http://example.com/api/skills/marketplace',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('SkillsClient.refreshSkill POSTs to the /refresh route', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ message: 'updated', skill: { name: 'my-skill' } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new SkillsClient({ host: 'http://example.com' });
    const refreshed = await client.refreshSkill('my-skill');

    expect(refreshed.message).toBe('updated');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/skills/installed/my-skill/refresh',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('SkillsClient percent-encodes skill names with special characters', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'my skill', enabled: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new SkillsClient({ host: 'http://example.com' });
    await client.getInstalledSkill('my skill');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/skills/installed/my%20skill',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('PluginsClient.getPluginsMarketplace fetches the plugins marketplace', async () => {
    const payload = {
      plugins: [
        {
          name: 'city-weather',
          description: 'Weather plugin',
          source: 'github:OpenHands/extensions',
          ref: null,
          repo_path: 'plugins/city-weather',
          installed: false,
        },
      ],
    };
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new PluginsClient({ host: 'http://example.com' });
    const result = await client.getPluginsMarketplace();

    expect(result.plugins).toHaveLength(1);
    expect(result.plugins[0].installed).toBe(false);
    expect(result.plugins[0].repo_path).toBe('plugins/city-weather');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/plugins/marketplace',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('PluginsClient management methods map to the correct endpoints', async () => {
    const installedPlugin = {
      name: 'demo-plugin',
      version: '1.0.0',
      description: 'A test plugin',
      enabled: true,
      source: '/tmp/demo-plugin',
      resolved_ref: null,
      repo_path: null,
      installed_at: '2026-05-12T12:00:00Z',
      install_path: '/home/.openhands/plugins/installed/demo-plugin',
    };
    const availableList = {
      plugins: [{ name: 'demo-plugin', version: '1.0.0', description: 'A test plugin' }],
    };
    const installedList = { plugins: [installedPlugin] };
    const toggleResponse = { name: 'demo-plugin', enabled: false };
    const uninstallResponse = { message: "Plugin 'demo-plugin' uninstalled" };
    const refreshResponse = {
      message: "Plugin 'demo-plugin' updated",
      plugin: installedPlugin,
    };

    const responses = [
      availableList,
      installedPlugin,
      installedList,
      installedPlugin,
      toggleResponse,
      uninstallResponse,
      refreshResponse,
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const client = new PluginsClient({ host: 'http://example.com' });

    const available = await client.getPlugins({ load_user: true, load_project: false });
    expect(available.plugins[0].name).toBe('demo-plugin');

    const installed = await client.installPlugin({
      source: '/tmp/demo-plugin',
      force: false,
    });
    expect(installed.name).toBe('demo-plugin');
    expect(installed.enabled).toBe(true);

    const list = await client.listInstalledPlugins();
    expect(list.plugins).toHaveLength(1);

    const got = await client.getInstalledPlugin('demo-plugin');
    expect(got.name).toBe('demo-plugin');

    const toggled = await client.setPluginEnabled('demo-plugin', false);
    expect(toggled.enabled).toBe(false);

    const uninstalled = await client.uninstallPlugin('demo-plugin');
    expect(uninstalled.message).toContain('uninstalled');

    const refreshed = await client.refreshPlugin('demo-plugin');
    expect(refreshed.message).toContain('updated');
    expect(refreshed.plugin.name).toBe('demo-plugin');

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/plugins',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ load_user: true, load_project: false }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/plugins/install',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ source: '/tmp/demo-plugin', force: false }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/plugins/installed',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/plugins/installed/demo-plugin',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/plugins/installed/demo-plugin',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ enabled: false }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      6,
      'http://example.com/api/plugins/installed/demo-plugin',
      expect.objectContaining({ method: 'DELETE' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      7,
      'http://example.com/api/plugins/installed/demo-plugin/refresh',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('WorkspacesClient checks agent-server version before listing workspaces', async () => {
    const responses = [
      { version: '1.23.0', uptime: 1, idle_time: 0 },
      {
        workspaces: [
          {
            id: '/repo',
            name: 'repo',
            path: '/repo',
            parentPath: '/home',
          },
        ],
        workspaceParents: [{ id: '/home', name: 'home', path: '/home' }],
      },
      {
        workspaces: [
          {
            id: '/repo',
            name: 'repo',
            path: '/repo',
            parentPath: '/home',
          },
          {
            id: '/repo2',
            name: 'repo2',
            path: '/repo2',
            parentPath: '/home',
          },
        ],
        workspaceParents: [{ id: '/home', name: 'home', path: '/home' }],
      },
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const client = new WorkspacesClient({ host: 'http://example.com' });
    const listed = await client.listWorkspaces();
    const updated = await client.addWorkspaces([
      { id: '/repo2', name: 'repo2', path: '/repo2', parentPath: '/home' },
    ]);

    expect(listed.workspaces).toHaveLength(1);
    expect(updated.workspaces).toHaveLength(2);
    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/server_info',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/workspaces',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/workspaces',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          workspaces: [{ id: '/repo2', name: 'repo2', path: '/repo2', parentPath: '/home' }],
        }),
      })
    );
  });

  it('WorkspacesClient throws AgentServerVersionError for old agent servers', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ version: '1.22.1', uptime: 1, idle_time: 0 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new WorkspacesClient({ host: 'http://example.com' });

    await expect(client.listWorkspaces()).rejects.toMatchObject({
      code: 'AGENT_SERVER_VERSION_TOO_OLD',
      feature: 'workspaces',
      requiredVersion: '1.23.0',
      actualVersion: '1.22.1',
    });

    try {
      await client.listWorkspaces();
    } catch (error) {
      expect(error).toBeInstanceOf(AgentServerVersionError);
      expect(isAgentServerVersionError(error)).toBe(true);
    }

    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('compares agent-server semantic versions', () => {
    expect(compareAgentServerVersions('1.23.0', '1.23.0')).toBe(0);
    expect(compareAgentServerVersions('v1.24.0+build.1', '1.23.0')).toBe(1);
    expect(compareAgentServerVersions('1.22.9', '1.23.0')).toBe(-1);
    expect(compareAgentServerVersions('1.23.0-rc.1', '1.23.0')).toBe(-1);
    expect(compareAgentServerVersions('not-a-version', '1.23.0')).toBeNull();
  });

  it('BashClient.startCommand normalizes string requests', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          id: 'bash-command-1',
          timestamp: new Date().toISOString(),
          command: 'echo hi',
          cwd: '/tmp',
          timeout: 3,
          kind: 'BashCommand',
        }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }
      )
    ) as typeof fetch;

    const client = new BashClient({ host: 'http://example.com' });
    const result = await client.startCommand('echo hi', '/tmp', 3.8);

    expect(result.command).toBe('echo hi');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/bash/start_bash_command',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ command: 'echo hi', cwd: '/tmp', timeout: 3 }),
      })
    );
  });

  it('ProfilesClient.listProfiles GETs the profiles endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          profiles: [{ name: 'default', model: 'gpt-4o', api_key_set: true }],
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.listProfiles();

    expect(result.profiles).toHaveLength(1);
    expect(result.profiles[0].name).toBe('default');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('ProfilesClient.getProfile sends X-Expose-Secrets header when requested', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          name: 'default',
          config: { model: 'gpt-4o', api_key: 'sk-x' },
          api_key_set: true,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.getProfile('default', { exposeSecrets: 'plaintext' });

    expect(result.name).toBe('default');
    expect(result.api_key_set).toBe(true);
    const fetchMock = global.fetch as jest.Mock;
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://example.com/api/profiles/default');
    expect(init.method).toBe('GET');
    expect(init.headers['X-Expose-Secrets']).toBe('plaintext');
  });

  it('ProfilesClient.getProfile omits X-Expose-Secrets header by default', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ name: 'default', config: { model: 'gpt-4o' }, api_key_set: false }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    await client.getProfile('default');

    const fetchMock = global.fetch as jest.Mock;
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers['X-Expose-Secrets']).toBeUndefined();
  });

  it('ProfilesClient.getProfile percent-encodes the profile name', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'my profile', config: {}, api_key_set: false }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    await client.getProfile('my profile');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles/my%20profile',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('ProfilesClient.saveProfile POSTs the profile body', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'default', message: "Profile 'default' saved" }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.saveProfile('default', {
      llm: { model: 'gpt-4o', api_key: 'sk-secret' },
      include_secrets: true,
    });

    expect(result.name).toBe('default');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles/default',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          llm: { model: 'gpt-4o', api_key: 'sk-secret' },
          include_secrets: true,
        }),
      })
    );
  });

  it('ProfilesClient.deleteProfile DELETEs the profile endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'default', message: "Profile 'default' deleted" }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.deleteProfile('default');

    expect(result.name).toBe('default');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles/default',
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('ProfilesClient.renameProfile POSTs to the rename endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'new', message: "Profile 'old' renamed to 'new'" }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.renameProfile('old', 'new');

    expect(result.name).toBe('new');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles/old/rename',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ new_name: 'new' }),
      })
    );
  });

  it('ProfilesClient.getProfile sends X-Expose-Secrets: encrypted', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          name: 'default',
          config: { model: 'gpt-4o', api_key: 'gAAAAA-encrypted-blob' },
          api_key_set: true,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.getProfile('default', { exposeSecrets: 'encrypted' });

    expect(result.config.api_key).toBe('gAAAAA-encrypted-blob');
    const fetchMock = global.fetch as jest.Mock;
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers['X-Expose-Secrets']).toBe('encrypted');
  });

  it('ProfilesClient.getProfile surfaces HttpError on 404', async () => {
    global.fetch = jest.fn(
      async () =>
        new Response(JSON.stringify({ detail: "Profile 'missing' not found" }), {
          status: 404,
          statusText: 'Not Found',
          headers: { 'content-type': 'application/json' },
        })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const error = await client.getProfile('missing').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).status).toBe(404);
    expect((error as HttpError).response).toEqual({ detail: "Profile 'missing' not found" });
  });

  it('ProfilesClient.saveProfile omits include_secrets when not provided', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'default', message: "Profile 'default' saved" }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    await client.saveProfile('default', { llm: { model: 'gpt-4o' } });

    const fetchMock = global.fetch as jest.Mock;
    const [, init] = fetchMock.mock.calls[0];
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ llm: { model: 'gpt-4o' } });
    expect(body).not.toHaveProperty('include_secrets');
  });

  it('ProfilesClient.renameProfile percent-encodes the source name', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'fresh', message: 'renamed' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    await client.renameProfile('my profile', 'fresh');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles/my%20profile/rename',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('ProfilesClient.activateProfile POSTs to the activate endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          name: 'default',
          message: "Profile 'default' activated and applied to current settings",
          llm_applied: true,
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    ) as typeof fetch;

    const client = new ProfilesClient({ host: 'http://example.com' });
    const result = await client.activateProfile('my profile');

    expect(result.name).toBe('default');
    expect(result.llm_applied).toBe(true);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/profiles/my%20profile/activate',
      expect.objectContaining({
        method: 'POST',
        body: '{}',
      })
    );
  });

  it('MetaProfilesClient.listMetaProfiles GETs the meta-profiles endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          meta_profiles: [
            {
              name: 'balanced',
              classifier_model: 'classifier',
              default_model: 'default',
              num_classes: 2,
            },
          ],
          active_meta_profile: 'balanced',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    ) as typeof fetch;

    const client = new MetaProfilesClient({ host: 'http://example.com' });
    const result = await client.listMetaProfiles();

    expect(result.meta_profiles).toHaveLength(1);
    expect(result.meta_profiles[0].name).toBe('balanced');
    expect(result.active_meta_profile).toBe('balanced');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/meta-profiles',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('MetaProfilesClient.getMetaProfile percent-encodes the name', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          name: 'my profile',
          config: {
            classifier_model: 'classifier',
            default_model: 'default',
            classes: [{ description: 'UI', model: 'fast' }],
          },
        }),
        { status: 200, headers: { 'content-type': 'application/json' } }
      )
    ) as typeof fetch;

    const client = new MetaProfilesClient({ host: 'http://example.com' });
    const result = await client.getMetaProfile('my profile');

    expect(result.name).toBe('my profile');
    expect(result.config.classes).toHaveLength(1);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/meta-profiles/my%20profile',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('MetaProfilesClient.saveMetaProfile POSTs the meta-profile body', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ name: 'balanced', message: "Meta-profile 'balanced' saved" }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new MetaProfilesClient({ host: 'http://example.com' });
    const config = {
      classifier_model: 'classifier',
      default_model: 'default',
      classes: [{ description: 'tests', model: 'slow' }],
    };
    const result = await client.saveMetaProfile('balanced', config);

    expect(result.name).toBe('balanced');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/meta-profiles/balanced',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(config) })
    );
  });

  it('MetaProfilesClient.deleteMetaProfile DELETEs the meta-profile endpoint', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ name: 'balanced', message: "Meta-profile 'balanced' deleted" }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      ) as typeof fetch;

    const client = new MetaProfilesClient({ host: 'http://example.com' });
    const result = await client.deleteMetaProfile('balanced');

    expect(result.name).toBe('balanced');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/meta-profiles/balanced',
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('MetaProfilesClient.activateMetaProfile POSTs to the activate endpoint', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({ name: 'balanced', message: "Meta-profile 'balanced' activated" }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      ) as typeof fetch;

    const client = new MetaProfilesClient({ host: 'http://example.com' });
    const result = await client.activateMetaProfile('balanced');

    expect(result.name).toBe('balanced');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/meta-profiles/balanced/activate',
      expect.objectContaining({ method: 'POST', body: '{}' })
    );
  });

  it('RemoteConversation.switchLlm POSTs the llm to the switch_llm endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    const llm = { model: 'gpt-4o-mini', api_key: 'sk-new' };
    await conversation.switchLlm(llm);

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/switch_llm',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ llm }),
      })
    );
  });

  it('RemoteConversation.switchAcpModel POSTs the model to the switch_acp_model endpoint', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    await conversation.switchAcpModel('claude-haiku-4-5');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/switch_acp_model',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ model: 'claude-haiku-4-5' }),
      })
    );
  });

  it('RemoteConversation.startGoal includes max_iterations only when provided and surfaces HttpError 409', async () => {
    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    // Omitted maxIterations must not appear on the wire (server default applies).
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;
    await conversation.startGoal('Fix the failing test');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/goal',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ objective: 'Fix the failing test' }),
      })
    );

    // A 409 from the server (run/goal already active) must propagate as HttpError,
    // and maxIterations must be forwarded when supplied.
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Conversation run or goal loop already running.' }), {
        status: 409,
        statusText: 'Conflict',
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;
    const error = await conversation.startGoal('Fix the failing test', 5).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).status).toBe(409);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/goal',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ objective: 'Fix the failing test', max_iterations: 5 }),
      })
    );
  });

  it('RemoteConversation.resumeGoal POSTs to goal/resume and surfaces HttpError 400 when nothing is resumable', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'no_resumable_goal' }), {
        status: 400,
        statusText: 'Bad Request',
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    const error = await conversation.resumeGoal().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).status).toBe(400);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/goal/resume',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({}) })
    );
  });

  it('RemoteConversation.stopGoal POSTs to goal/stop and surfaces HttpError 404 for a missing conversation', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Item not found' }), {
        status: 404,
        statusText: 'Not Found',
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    const error = await conversation.stopGoal().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).status).toBe(404);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/goal/stop',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({}) })
    );
  });

  it('RemoteConversation.setConfirmationPolicy wraps the SDK v1.23.0 request body', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    await conversation.setConfirmationPolicy({ kind: 'NeverConfirm' });

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/confirmation_policy',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ policy: { kind: 'NeverConfirm' } }),
      })
    );
  });

  it('RemoteConversation.start sends the optional observability user ID', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'conv-123' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      userId: 'user-42',
    });

    await conversation.start({ initialMessage: 'hello' });

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations',
      expect.objectContaining({
        method: 'POST',
      })
    );
    const [, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(JSON.parse(init.body)).toMatchObject({
      user_id: 'user-42',
      initial_message: {
        role: 'user',
        content: [{ type: 'text', text: 'hello' }],
      },
    });
  });

  it('ConversationManager.createACPConversation sends the optional observability user ID', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'acp-123' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const manager = new ConversationManager({ host: 'http://example.com' });
    await manager.createACPConversation(
      { kind: 'ACPAgent', llm: { model: 'gpt-4o' } },
      { userId: 'user-42' }
    );

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations',
      expect.objectContaining({
        method: 'POST',
      })
    );
    const [, init] = (global.fetch as jest.Mock).mock.calls[0];
    expect(JSON.parse(init.body)).toMatchObject({
      user_id: 'user-42',
    });
  });

  it('HttpClient can parse blob responses when requested', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(new Blob(['zip-data']), {
        status: 200,
        headers: { 'content-type': 'application/zip' },
      })
    ) as typeof fetch;

    const client = new HttpClient({ baseUrl: 'http://example.com' });
    const response = await client.get<Blob>('/download.zip', { responseType: 'blob' });

    expect(response.data).toBeInstanceOf(Blob);
    expect(await response.data.text()).toBe('zip-data');
  });
  it('SettingsClient manages LLM profiles', async () => {
    const responses = [
      { profiles: [{ name: 'fast', model: 'openai/gpt-4o', base_url: null, api_key_set: true }] },
      { name: 'fast', config: { model: 'openai/gpt-4o', api_key: 'encrypted' }, api_key_set: true },
      { name: 'fast', message: "Profile 'fast' saved" },
      { name: 'slow', message: "Profile 'fast' renamed to 'slow'" },
      { name: 'slow', message: "Profile 'slow' deleted" },
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const client = new SettingsClient({ host: 'http://example.com' });

    await expect(client.listProfiles()).resolves.toEqual({
      profiles: [{ name: 'fast', model: 'openai/gpt-4o', base_url: null, api_key_set: true }],
    });
    await expect(client.getProfile('fast', { exposeSecrets: 'encrypted' })).resolves.toEqual({
      name: 'fast',
      config: { model: 'openai/gpt-4o', api_key: 'encrypted' },
      api_key_set: true,
    });
    await client.saveProfile('fast', { llm: { model: 'openai/gpt-4o' } });
    await client.renameProfile('fast', 'slow');
    await client.deleteProfile('slow');

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/profiles',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/profiles/fast',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ 'X-Expose-Secrets': 'encrypted' }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/profiles/fast',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ llm: { model: 'openai/gpt-4o' } }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/profiles/fast/rename',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ new_name: 'slow' }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/profiles/slow',
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('ConversationClient.switchProfile posts the profile name', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    await client.switchProfile('conversation-1', 'fast');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conversation-1/switch_profile',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ profile_name: 'fast' }),
      })
    );
  });

  it('ConversationClient.switchAcpModel posts the model', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    await client.switchAcpModel('conversation-1', 'claude-haiku-4-5');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conversation-1/switch_acp_model',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ model: 'claude-haiku-4-5' }),
      })
    );
  });

  it('ConversationClient.navigateConversation POSTs event_id and returns the re-rooted info', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'conversation-1', leaf_event_id: 'event-7' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    const info = await client.navigateConversation(
      'conversation-1',
      { event_id: 'event-7' },
      { includeSkills: true }
    );

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conversation-1/navigate?include_skills=true',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ event_id: 'event-7' }),
      })
    );
    // The response carries the new HEAD.
    expect(info.leaf_event_id).toBe('event-7');
  });

  it('ConversationClient.navigateConversation selects the empty tree with a null event_id', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: 'conversation-1', leaf_event_id: null }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    await client.navigateConversation('conversation-1', { event_id: null });

    // No includeSkills option => no query string on the URL.
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conversation-1/navigate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ event_id: null }),
      })
    );
  });

  it('RemoteConversation.navigateTo POSTs to /navigate then refreshes cached state', async () => {
    // navigateTo posts to the route and then GETs the conversation to refresh
    // the cache (leaf_event_id is not broadcast over the WebSocket). Two calls
    // means each needs its own Response (a body can only be read once).
    global.fetch = jest.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ id: 'conv-123', leaf_event_id: 'event-3' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    await conversation.navigateTo('event-3');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/navigate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ event_id: 'event-3' }),
      })
    );
    // The trailing refresh re-reads the conversation from the REST API.
    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('RemoteConversation.navigateTo passes a null event_id for the empty tree', async () => {
    // Fresh Response per call: navigateTo posts and then refreshes state.
    global.fetch = jest.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ id: 'conv-123', leaf_event_id: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    ) as typeof fetch;

    const agent = new Agent({ llm: { model: 'gpt-4o', api_key: 'k' } });
    const workspace = new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' });
    const conversation = new RemoteConversation(agent, workspace, {
      conversationId: 'conv-123',
    });

    await conversation.navigateTo(null);

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conv-123/navigate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ event_id: null }),
      })
    );
  });

  it('ConversationManager.switchProfile posts the profile name', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ success: true }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    const manager = new ConversationManager({ host: 'http://example.com' });
    await manager.switchProfile('conversation-1', 'fast');

    expect(global.fetch).toHaveBeenCalledWith(
      'http://example.com/api/conversations/conversation-1/switch_profile',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ profile_name: 'fast' }),
      })
    );
  });

  it('SettingsClient fetches and updates settings and secrets', async () => {
    const responses = [
      { agent_settings: { llm_model: 'gpt-4o' }, conversation_settings: {} },
      { agent_settings: {}, conversation_settings: { max_iterations: 50 } },
      { secrets: [{ name: 'TOKEN', description: 'token' }] },
      { name: 'TOKEN', description: 'token' },
      'plain-secret',
      { deleted: true },
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      const isText = typeof body === 'string';
      return Promise.resolve(
        new Response(isText ? body : JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': isText ? 'text/plain' : 'application/json' },
        })
      );
    }) as typeof fetch;

    const client = new SettingsClient({ host: 'http://example.com' });
    await client.getSettings({ exposeSecrets: 'encrypted' });
    await client.updateSettings({ conversation_settings_diff: { max_iterations: 50 } });
    await client.listSecrets();
    await client.upsertSecret({ name: 'TOKEN', value: 'secret', description: 'token' });
    await expect(client.getSecret('TOKEN')).resolves.toBe('plain-secret');
    await client.deleteSecret('TOKEN/with slash');

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/settings',
      expect.objectContaining({
        method: 'GET',
        headers: expect.objectContaining({ 'X-Expose-Secrets': 'encrypted' }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/settings',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ conversation_settings_diff: { max_iterations: 50 } }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/settings/secrets',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/settings/secrets',
      expect.objectContaining({ method: 'PUT' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/settings/secrets/TOKEN',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      6,
      'http://example.com/api/settings/secrets/TOKEN%2Fwith%20slash',
      expect.objectContaining({ method: 'DELETE' })
    );
  });

  it('FileClient wraps file browsing and download endpoints', async () => {
    const binary = new TextEncoder().encode('hello').buffer;
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ home: '/workspace' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ items: [{ name: 'src', path: '/workspace/src' }], next_page_id: null }),
          {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }
        )
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ success: true }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(new Response(binary, { status: 200 }))
      .mockResolvedValueOnce(new Response(new Blob(['trajectory']), { status: 200 }));

    const client = new FileClient({ host: 'http://example.com' });
    await expect(client.getHome()).resolves.toEqual({ home: '/workspace' });
    await client.searchSubdirectories('/workspace', { limit: 10, pageId: 'p1' });
    await expect(client.uploadTextFile('hello', '/workspace/hello.txt')).resolves.toEqual({
      success: true,
    });
    await expect(client.downloadTextFile('/workspace/README.md')).resolves.toBe('hello');
    await expect(client.downloadTrajectory('conv 1')).resolves.toBeInstanceOf(Blob);

    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/file/search_subdirs?path=%2Fworkspace&page_id=p1&limit=10',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/file/upload?path=%2Fworkspace%2Fhello.txt',
      expect.objectContaining({ method: 'POST', body: expect.any(FormData) })
    );
    const uploadInit = (global.fetch as jest.Mock).mock.calls[2][1];
    expect(uploadInit.headers['Content-Type']).toBeUndefined();
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/file/download?path=%2Fworkspace%2FREADME.md',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/file/download-trajectory/conv%201',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('FileClient forwards includeHidden to the home and search_subdirs endpoints', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ home: '/workspace' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], next_page_id: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );

    const client = new FileClient({ host: 'http://example.com' });
    await expect(client.getHome({ includeHidden: true })).resolves.toEqual({
      home: '/workspace',
    });
    await client.searchSubdirectories('/workspace', { includeHidden: true });

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/file/home?include_hidden=true',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/file/search_subdirs?path=%2Fworkspace&include_hidden=true',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('FileClient omits include_hidden when includeHidden is not set', async () => {
    global.fetch = jest
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ home: '/workspace' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ items: [], next_page_id: null }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );

    const client = new FileClient({ host: 'http://example.com' });
    await client.getHome();
    await client.searchSubdirectories('/workspace');

    expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe('http://example.com/api/file/home');
    expect((global.fetch as jest.Mock).mock.calls[1][0]).toBe(
      'http://example.com/api/file/search_subdirs?path=%2Fworkspace'
    );
  });

  it('ConversationClient wraps agent-canvas conversation endpoints', async () => {
    global.fetch = jest.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ success: true, response: 'ok' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      )
    ) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    await client.sendEvent('c1', { role: 'user', content: [] }, { run: true });
    await client.pauseConversation('c1');
    await client.interruptConversation('c1');
    await client.runConversation('c1');
    await client.askAgent('c1', 'status?');
    await client.respondToConfirmation('c1', { accept: true });
    await client.deleteConversation('c1');
    await client.updateConversation('c1', { title: 'New title' });

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/conversations/c1/events',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ role: 'user', content: [], run: true }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/conversations/c1/interrupt',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({}) })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/conversations/c1/ask_agent',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ question: 'status?' }) })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      6,
      'http://example.com/api/conversations/c1/events/respond_to_confirmation',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ accept: true }) })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      8,
      'http://example.com/api/conversations/c1',
      expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ title: 'New title' }) })
    );
  });

  it('ConversationClient wraps SDK v1.23.0 conversation endpoints', async () => {
    const event = {
      id: 'event-1',
      kind: 'MessageEvent',
      timestamp: '2026-05-23T12:00:00Z',
      source: 'agent',
    };
    const responses = [
      2,
      { items: [event], next_page_id: null },
      event,
      4,
      { response: 'done' },
      { success: true },
      { success: true },
      { success: true },
      { success: true },
      { id: 'fork-1' },
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const client = new ConversationClient({ host: 'http://example.com' });
    await expect(
      client.countConversations({ status: ConversationExecutionStatus.IDLE })
    ).resolves.toBe(2);
    await expect(client.searchEvents('c1', { kind: 'MessageEvent', limit: 5 })).resolves.toEqual({
      items: [event],
      next_page_id: null,
    });
    await expect(client.getEvent('c1', 'event-1')).resolves.toEqual(event);
    await expect(client.getEventCount('c1', { source: 'agent' })).resolves.toBe(4);
    await expect(client.getAgentFinalResponse('c1')).resolves.toEqual({ response: 'done' });
    await client.setConfirmationPolicy('c1', { kind: 'NeverConfirm' });
    await client.condenseConversation('c1');
    await client.setSecurityAnalyzer('c1', { kind: 'LLMSecurityAnalyzer' });
    await client.updateSecrets('c1', {
      secrets: { TOKEN: { kind: 'StaticSecret', value: 'secret' } },
    });
    await client.forkConversation('c1', { title: 'Fork' }, { includeSkills: true });

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/api/conversations/count?status=idle',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/conversations/c1/events/search?kind=MessageEvent&limit=5',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/conversations/c1/agent_final_response',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      6,
      'http://example.com/api/conversations/c1/confirmation_policy',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ policy: { kind: 'NeverConfirm' } }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      8,
      'http://example.com/api/conversations/c1/security_analyzer',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ security_analyzer: { kind: 'LLMSecurityAnalyzer' } }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      10,
      'http://example.com/api/conversations/c1/fork?include_skills=true',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ title: 'Fork' }) })
    );
  });

  describe('ConversationClient goal endpoints (error paths)', () => {
    function mockErrorResponse(status: number, statusText: string, detail: string): void {
      global.fetch = jest.fn(
        async () =>
          new Response(JSON.stringify({ detail }), {
            status,
            statusText,
            headers: { 'content-type': 'application/json' },
          })
      ) as typeof fetch;
    }

    it('startGoal posts the objective and rejects with HttpError 409 when a run is already active', async () => {
      mockErrorResponse(409, 'Conflict', 'Conversation run or goal loop already running.');
      const client = new ConversationClient({ host: 'http://example.com' });

      const error = await client
        .startGoal('c1', { objective: 'Audit the repo', max_iterations: 3 })
        .catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(409);
      // The failure must still have targeted the right route, method, and body
      // so a 409 is attributable to server state, not a client contract bug.
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/conversations/c1/goal',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ objective: 'Audit the repo', max_iterations: 3 }),
        })
      );
    });

    it('startGoal rejects with HttpError 400 when the objective is invalid', async () => {
      mockErrorResponse(400, 'Bad Request', 'Goal objective must not be empty.');
      const client = new ConversationClient({ host: 'http://example.com' });

      const error = await client.startGoal('c1', { objective: '' }).catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(400);
      // max_iterations is omitted from the wire body when not supplied so the
      // server applies its own default.
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/conversations/c1/goal',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({ objective: '' }) })
      );
    });

    it('startGoal rejects with HttpError 404 for an unknown conversation', async () => {
      mockErrorResponse(404, 'Not Found', 'Item not found');
      const client = new ConversationClient({ host: 'http://example.com' });

      const error = await client
        .startGoal('does-not-exist', { objective: 'Audit the repo' })
        .catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(404);
    });

    it('resumeGoal posts an empty body and rejects with HttpError 400 when there is no resumable goal', async () => {
      mockErrorResponse(400, 'Bad Request', 'no_resumable_goal');
      const client = new ConversationClient({ host: 'http://example.com' });

      const error = await client.resumeGoal('c1').catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(400);
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/conversations/c1/goal/resume',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({}) })
      );
    });

    it('resumeGoal rejects with HttpError 409 when a run or goal loop is already active', async () => {
      mockErrorResponse(409, 'Conflict', 'Conversation run or goal loop already running.');
      const client = new ConversationClient({ host: 'http://example.com' });

      const error = await client.resumeGoal('c1').catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(409);
    });

    it('stopGoal posts an empty body and rejects with HttpError 404 for an unknown conversation', async () => {
      mockErrorResponse(404, 'Not Found', 'Item not found');
      const client = new ConversationClient({ host: 'http://example.com' });

      const error = await client.stopGoal('does-not-exist').catch((e: unknown) => e);

      expect(error).toBeInstanceOf(HttpError);
      expect((error as HttpError).status).toBe(404);
      expect(global.fetch).toHaveBeenCalledWith(
        'http://example.com/api/conversations/does-not-exist/goal/stop',
        expect.objectContaining({ method: 'POST', body: JSON.stringify({}) })
      );
    });
  });

  it('Hooks and MCP clients wrap SDK v1.23.0 endpoints', async () => {
    const serverInfo = { version: '1.23.0', uptime: 1, idle_time: 0 };
    const responses = [
      serverInfo,
      { hook_config: null },
      serverInfo,
      { ok: true, tools: ['ping'] },
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift();
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const options = { host: 'http://example.com', apiKey: 'secret' };
    await expect(
      new HooksClient(options).loadHooks({ project_dir: '/workspace' })
    ).resolves.toEqual({
      hook_config: null,
    });
    await expect(
      new MCPClient(options).testServer({
        server: { type: 'stdio', command: 'node', args: ['server.js'] },
        timeout: 10,
      })
    ).resolves.toEqual({ ok: true, tools: ['ping'] });

    expect(global.fetch).toHaveBeenNthCalledWith(
      1,
      'http://example.com/server_info',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/hooks',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ project_dir: '/workspace' }),
        headers: expect.objectContaining({ 'X-Session-API-Key': 'secret' }),
      })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/mcp/test',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          server: { type: 'stdio', command: 'node', args: ['server.js'] },
          timeout: 10,
        }),
      })
    );
  });

  it('new SDK v1.23.0 clients throw AgentServerVersionError for old servers', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      new Response(JSON.stringify({ version: '1.22.1', uptime: 1, idle_time: 0 }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;

    await expect(
      new MCPClient({ host: 'http://example.com' }).testServer({
        server: { type: 'stdio', command: 'node' },
      })
    ).rejects.toMatchObject({
      code: 'AGENT_SERVER_VERSION_TOO_OLD',
      feature: 'mcp-test',
      requiredVersion: '1.23.0',
      actualVersion: '1.22.1',
    });

    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('Shared client wraps app endpoints', async () => {
    const responses = [
      [{ id: 'shared-1', created_by_user_id: null, selected_repository: null }],
      { items: [], next_page_id: null },
    ];
    global.fetch = jest.fn().mockImplementation(() => {
      const body = responses.shift() ?? { success: true };
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      );
    }) as typeof fetch;

    const options = { host: 'http://example.com' };
    await new SharedClient(options).getSharedConversation('shared-1');
    await new SharedClient(options).searchSharedEvents({ conversationId: 'shared-1', limit: 50 });

    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/shared-events/search?conversation_id=shared-1&limit=50',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('BashClient.batchGetEvents GETs the batch endpoint with the event ids in the body', async () => {
    const events = [{ id: 'e1', kind: 'BashOutput', timestamp: '2026-05-23T12:00:00Z' }, null];
    const { captured } = mockNodeHttpRequest(JSON.stringify(events));

    const client = new BashClient({ host: 'http://example.com' });
    const result = await client.batchGetEvents(['e1', 'missing']);

    expect(result).toHaveLength(2);
    expect(result[0]?.id).toBe('e1');
    expect(result[1]).toBeNull();
    expect(captured.options?.method).toBe('GET');
    expect(captured.url?.toString()).toBe('http://example.com/api/bash/bash_events/');
    expect(JSON.parse(captured.body ?? 'null')).toEqual(['e1', 'missing']);
  });
});
