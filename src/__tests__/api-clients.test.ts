import {
  Agent,
  ConversationManager,
  HttpClient,
  HttpError,
  RemoteConversation,
  RemoteWorkspace,
  Workspace,
} from '../index';
import { BashClient, ProfilesClient, ServerClient, SkillsClient } from '../clients';

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
});
