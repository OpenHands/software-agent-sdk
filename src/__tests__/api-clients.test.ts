import {
  Agent,
  ConversationManager,
  HttpClient,
  HttpError,
  RemoteConversation,
  RemoteWorkspace,
  Workspace,
} from '../index';
import {
  ApiKeysClient,
  BashClient,
  ConversationClient,
  FileClient,
  ProfilesClient,
  SecurityClient,
  ServerClient,
  SessionClient,
  SettingsClient,
  SharedClient,
  SkillsClient,
} from '../clients';

const originalFetch = global.fetch;

describe('Auxiliary API clients', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    jest.restoreAllMocks();
  });

  it('ConversationManager exposes server and skills namespaces', () => {
    const manager = new ConversationManager({ host: 'http://example.com', apiKey: 'secret' });

    expect(manager.server).toBeInstanceOf(ServerClient);
    expect(manager.skills).toBeInstanceOf(SkillsClient);
    expect(manager.profiles).toBeInstanceOf(ProfilesClient);
    expect(manager.server.host).toBe('http://example.com');
    expect(manager.server.apiKey).toBe('secret');
    expect(manager.profiles.host).toBe('http://example.com');
    expect(manager.profiles.apiKey).toBe('secret');
    expect(manager.files).toBeInstanceOf(FileClient);
    expect(manager.security).toBeInstanceOf(SecurityClient);
    expect(manager.apiKeys).toBeInstanceOf(ApiKeysClient);
    expect(manager.session).toBeInstanceOf(SessionClient);
    expect(manager.shared).toBeInstanceOf(SharedClient);
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
      'http://example.com/api/skills/installed/my-skill/update',
      expect.objectContaining({ method: 'POST' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      7,
      'http://example.com/api/skills/marketplace',
      expect.objectContaining({ method: 'GET' })
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
      { deleted: true },
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
    await client.getSettings({ exposeSecrets: 'encrypted' });
    await client.updateSettings({ conversation_settings_diff: { max_iterations: 50 } });
    await client.listSecrets();
    await client.upsertSecret({ name: 'TOKEN', value: 'secret', description: 'token' });
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
      .mockResolvedValueOnce(new Response(binary, { status: 200 }))
      .mockResolvedValueOnce(new Response(new Blob(['trajectory']), { status: 200 }));

    const client = new FileClient({ host: 'http://example.com' });
    await expect(client.getHome()).resolves.toEqual({ home: '/workspace' });
    await client.searchSubdirectories('/workspace', { limit: 10, pageId: 'p1' });
    await expect(client.downloadTextFile('/workspace/README.md')).resolves.toBe('hello');
    await expect(client.downloadTrajectory('conv 1')).resolves.toBeInstanceOf(Blob);

    expect(global.fetch).toHaveBeenNthCalledWith(
      2,
      'http://example.com/api/file/search_subdirs?path=%2Fworkspace&page_id=p1&limit=10',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      3,
      'http://example.com/api/file/download?path=%2Fworkspace%2FREADME.md',
      expect.objectContaining({ method: 'GET' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      4,
      'http://example.com/api/file/download-trajectory/conv%201',
      expect.objectContaining({ method: 'GET' })
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

  it('Security ApiKeys Session and Shared clients wrap app endpoints', async () => {
    const responses = [
      { policy: 'default' },
      { RISK_SEVERITY: 2 },
      [{ id: 'key-1', name: 'Key', prefix: 'oh', created_at: 'now', last_used_at: null }],
      { id: 'key-2', name: 'New', key: 'full', prefix: 'oh', created_at: 'now' },
      { redirect_url: '/home' },
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
    await new SecurityClient(options).getPolicy();
    await new SecurityClient(options).getRiskSeverity();
    await new ApiKeysClient(options).listApiKeys();
    await new ApiKeysClient(options).createApiKey('New');
    await new ApiKeysClient(options).deleteApiKey('key/2');
    await new SessionClient(options).acceptTos('/home');
    await new SessionClient(options).unsetProviderTokens();
    await new SharedClient(options).getSharedConversation('shared-1');
    await new SharedClient(options).searchSharedEvents({ conversationId: 'shared-1', limit: 50 });

    expect(global.fetch).toHaveBeenNthCalledWith(
      5,
      'http://example.com/api/keys/key%2F2',
      expect.objectContaining({ method: 'DELETE' })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      6,
      'http://example.com/api/accept_tos',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ redirect_url: '/home' }) })
    );
    expect(global.fetch).toHaveBeenNthCalledWith(
      9,
      'http://example.com/api/shared-events/search?conversation_id=shared-1&limit=50',
      expect.objectContaining({ method: 'GET' })
    );
  });
});
