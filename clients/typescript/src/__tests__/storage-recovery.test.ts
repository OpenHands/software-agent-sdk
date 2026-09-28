import { ConversationClient } from '../client/conversation-client';
import { SettingsClient } from '../client/settings-client';
import {
  Agent,
  HttpError,
  RemoteConversation,
  RemoteWorkspace,
  type RecoverStorageRequest,
  type AgentOptions,
  type AgentServerSettingsPatchRequest,
  type NotesRetrievalCondenserConfig,
  type NotesRetrievalCondenserSettings,
} from '../index';

afterEach(() => vi.unstubAllGlobals());

it('accepts typed notes configurations in agent options and settings requests', async () => {
  const condenser: NotesRetrievalCondenserConfig = {
    kind: 'NotesRetrievalCondenser',
    max_size: 40,
    keep_recent: 2,
  };
  const options: AgentOptions = { llm: { model: 'test-model' }, condenser };
  expect(new Agent(options).condenser).toEqual(condenser);

  const settings: NotesRetrievalCondenserSettings = {
    condenser_kind: 'notes_retrieval',
    enabled: true,
    max_size: 40,
  };
  const request: AgentServerSettingsPatchRequest = {
    agent_settings_diff: { condenser: settings },
  };
  const fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({}), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  );
  vi.stubGlobal('fetch', fetch);
  await new SettingsClient({ host: 'http://example.com' }).updateSettings(request);
  expect(fetch).toHaveBeenCalledExactlyOnceWith(
    'http://example.com/api/settings',
    expect.objectContaining({ method: 'PATCH', body: JSON.stringify(request) })
  );
});

it.each<RecoverStorageRequest>([
  {},
  { acknowledge_unknown_outcomes: false },
  { acknowledge_unknown_outcomes: true, head_event_id: 'inspected-head' },
  { acknowledge_unknown_outcomes: true, head_event_id: null },
])('posts the recovery options without implicitly approving outcomes: %j', async (request) => {
  const fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ success: true }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })
  );
  vi.stubGlobal('fetch', fetch);
  const client = new ConversationClient({ host: 'http://example.com' });

  await expect(client.recoverStorage('c1', request)).resolves.toEqual({ success: true });
  expect(fetch).toHaveBeenCalledExactlyOnceWith(
    'http://example.com/api/conversations/c1/storage/recover',
    expect.objectContaining({ method: 'POST', body: JSON.stringify(request) })
  );
});

it('refreshes remote state after recovery without starting a run', async () => {
  const fetch = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ success: true, id: 'c1', execution_status: 'paused' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
  );
  vi.stubGlobal('fetch', fetch);
  const conversation = new RemoteConversation(
    new Agent({ llm: { model: 'test-model' } }),
    new RemoteWorkspace({ host: 'http://example.com', workingDir: '/tmp' }),
    { conversationId: 'c1' }
  );

  await conversation.recoverStorage({ acknowledge_unknown_outcomes: true });

  expect(fetch).toHaveBeenNthCalledWith(
    1,
    'http://example.com/api/conversations/c1/storage/recover',
    expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ acknowledge_unknown_outcomes: true }),
    })
  );
  expect(fetch).toHaveBeenNthCalledWith(
    2,
    'http://example.com/api/conversations/c1',
    expect.objectContaining({ method: 'GET' })
  );
  expect(fetch).toHaveBeenCalledTimes(2);
});

it.each([409, 507])('preserves recovery diagnostics from HTTP %s', async (status) => {
  const diagnostic = { code: 'StorageWriteFailed', required_bytes: 1024 };
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(diagnostic), {
        status,
        headers: { 'content-type': 'application/json' },
      })
    )
  );
  const client = new ConversationClient({ host: 'http://example.com' });

  await expect(client.recoverStorage('c1')).rejects.toMatchObject({
    name: HttpError.name,
    status,
    response: diagnostic,
  });
});
