import { SettingsClient } from '../client/settings-client';
import type { MCPConfig, MCPServer } from '../models/mcp-settings';

type JsonObject = Record<string, unknown>;

function clone<Value>(value: Value): Value {
  return structuredClone(value);
}

function mergePatch(target: JsonObject, patch: JsonObject): JsonObject {
  const result = clone(target);
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) {
      delete result[key];
    } else if (
      typeof value === 'object' &&
      !Array.isArray(value) &&
      typeof result[key] === 'object' &&
      result[key] !== null &&
      !Array.isArray(result[key])
    ) {
      result[key] = mergePatch(result[key] as JsonObject, value as JsonObject);
    } else {
      result[key] = clone(value);
    }
  }
  return result;
}

function installStatefulSettingsServer(initial: MCPConfig) {
  const catalog = clone(initial) as unknown as JsonObject;
  const fetchMock = jest.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const settingsKey = decodeURIComponent(url.pathname.split('/').at(-1) ?? '');
    const method = init?.method;
    const body = init?.body ? (JSON.parse(String(init.body)) as JsonObject) : undefined;

    if (method === 'POST') {
      if (settingsKey in catalog) {
        return new Response(null, { status: 409, statusText: 'Conflict' });
      }
      catalog[settingsKey] = clone(body as unknown as MCPServer) as unknown as JsonObject;
    } else if (method === 'PATCH') {
      if (!(settingsKey in catalog)) {
        return new Response(null, { status: 404, statusText: 'Not Found' });
      }
      catalog[settingsKey] = mergePatch(catalog[settingsKey] as JsonObject, body ?? {});
    } else if (method === 'DELETE') {
      if (!(settingsKey in catalog)) {
        return new Response(null, { status: 404, statusText: 'Not Found' });
      }
      delete catalog[settingsKey];
    }

    return new Response(
      JSON.stringify({
        agent_settings: { mcp_config: catalog },
        conversation_settings: {},
        llm_api_key_is_set: false,
      }),
      {
        status: method === 'POST' ? 201 : 200,
        headers: { 'content-type': 'application/json' },
      }
    );
  });
  global.fetch = fetchMock as typeof fetch;
  return {
    fetchMock,
    getCatalog: () => clone(catalog),
  };
}

const initialCatalog = (): MCPConfig => ({
  github: {
    transport: 'http',
    url: 'https://api.githubcopilot.com/mcp/',
    auth: { strategy: 'bearer', value: 'github-secret' },
    headers: { 'X-Existing': 'keep-me', 'X-Remove': 'old' },
  },
  filesystem: {
    transport: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-filesystem', '/workspace'],
    env: { FILESYSTEM_TOKEN: 'filesystem-secret' },
  },
});

describe('SettingsClient canonical MCP mutations', () => {
  it('adds one named server while preserving siblings and credentials', async () => {
    const server = installStatefulSettingsServer(initialCatalog());
    const client = new SettingsClient({ host: 'http://example.com' });

    await client.createMcpServer('docs', {
      transport: 'http',
      url: 'https://example.test/mcp',
      auth: { strategy: 'bearer', value: 'docs-secret' },
    });

    expect(server.fetchMock).toHaveBeenCalledTimes(1);
    expect(server.getCatalog()).toMatchObject({
      github: { auth: { strategy: 'bearer', value: 'github-secret' } },
      filesystem: { env: { FILESYSTEM_TOKEN: 'filesystem-secret' } },
      docs: {
        transport: 'http',
        url: 'https://example.test/mcp',
        auth: { strategy: 'bearer', value: 'docs-secret' },
      },
    });
    expect(server.fetchMock).toHaveBeenCalledWith(
      'http://example.com/api/settings/mcp/docs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          transport: 'http',
          url: 'https://example.test/mcp',
          auth: { strategy: 'bearer', value: 'docs-secret' },
        }),
      })
    );
  });

  it('preserves stored auth when a sparse update omits it', async () => {
    const server = installStatefulSettingsServer(initialCatalog());
    const client = new SettingsClient({ host: 'http://example.com' });

    await client.patchMcpServer('github', {
      url: 'https://example.test/github-mcp',
    });

    expect(server.fetchMock).toHaveBeenCalledTimes(1);
    expect(server.getCatalog().github).toMatchObject({
      url: 'https://example.test/github-mcp',
      auth: { strategy: 'bearer', value: 'github-secret' },
    });
    expect(server.fetchMock).toHaveBeenCalledWith(
      'http://example.com/api/settings/mcp/github',
      expect.objectContaining({
        method: 'PATCH',
        body: JSON.stringify({ url: 'https://example.test/github-mcp' }),
      })
    );
  });

  it('replaces and explicitly clears auth', async () => {
    const server = installStatefulSettingsServer(initialCatalog());
    const client = new SettingsClient({ host: 'http://example.com' });

    await client.patchMcpServer('github', {
      auth: { strategy: 'api_key', header_name: 'X-API-Key', value: 'replacement' },
    });
    expect(server.getCatalog().github).toMatchObject({
      auth: { strategy: 'api_key', header_name: 'X-API-Key', value: 'replacement' },
    });

    await client.patchMcpServer('github', { auth: null });
    expect(server.fetchMock).toHaveBeenCalledTimes(2);
    expect(server.getCatalog().github).not.toHaveProperty('auth');
  });

  it('deletes a map entry without altering sibling credentials', async () => {
    const server = installStatefulSettingsServer(initialCatalog());
    const client = new SettingsClient({ host: 'http://example.com' });

    await client.deleteMcpServer('filesystem');

    expect(server.fetchMock).toHaveBeenCalledTimes(1);
    expect(server.getCatalog()).toEqual({
      github: {
        transport: 'http',
        url: 'https://api.githubcopilot.com/mcp/',
        auth: { strategy: 'bearer', value: 'github-secret' },
        headers: { 'X-Existing': 'keep-me', 'X-Remove': 'old' },
      },
    });
    expect(server.fetchMock).toHaveBeenCalledWith(
      'http://example.com/api/settings/mcp/filesystem',
      expect.objectContaining({
        method: 'DELETE',
      })
    );
  });

  it('uses null to delete one nested map value while preserving its siblings', async () => {
    const server = installStatefulSettingsServer(initialCatalog());
    const client = new SettingsClient({ host: 'http://example.com' });

    await client.patchMcpServer('github', {
      headers: { 'X-Remove': null },
    });

    expect(server.fetchMock).toHaveBeenCalledTimes(1);
    expect(server.getCatalog().github).toMatchObject({
      headers: { 'X-Existing': 'keep-me' },
      auth: { strategy: 'bearer', value: 'github-secret' },
    });
    expect(
      (server.getCatalog().github as JsonObject).headers as Record<string, string>
    ).not.toHaveProperty('X-Remove');
  });

  it('enforces create and existing-server preconditions', async () => {
    const server = installStatefulSettingsServer(initialCatalog());
    const client = new SettingsClient({ host: 'http://example.com' });

    await expect(
      client.createMcpServer('github', {
        transport: 'http',
        url: 'https://replacement.example/mcp',
      })
    ).rejects.toMatchObject({ status: 409 });
    await expect(
      client.patchMcpServer('missing', { url: 'https://example.test/mcp' })
    ).rejects.toMatchObject({ status: 404 });
    await expect(client.deleteMcpServer('missing')).rejects.toMatchObject({ status: 404 });

    expect(server.fetchMock).toHaveBeenCalledTimes(3);
    expect(server.getCatalog()).toEqual(initialCatalog());
  });
});
