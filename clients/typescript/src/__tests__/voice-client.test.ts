import { ConversationManager } from '../index';
import { VoiceClient } from '../clients';
import { HttpError } from '../client/http-client';

const originalFetch = global.fetch;

afterEach(() => {
  global.fetch = originalFetch;
  vi.restoreAllMocks();
});

it('sends authenticated conversation-bound voice requests through the manager', async () => {
  const responses = [
    {
      available: true,
      run_active: false,
      execution_status: 'idle',
      provider: 'codex',
      delegation: 'server',
    },
    { sdp: 'v=0\r\nanswer', call_id: 'call-1', provider: 'codex', delegation: 'server' },
    {
      status: 'error',
      transcripts: [{ id: 'transcript-1', role: 'user', text: 'Check the tests' }],
      error_code: 'request_not_sent',
    },
    { success: true },
  ];
  const pendingResponses = [...responses];
  const fetchMock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(pendingResponses.shift()), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    )
  );
  global.fetch = fetchMock;
  const manager = new ConversationManager({
    host: 'https://agent.example.test/',
    apiKey: 'session-fixture',
  });
  try {
    expect(await manager.voice.availability('saved/cat')).toEqual(responses[0]);
    expect(await manager.voice.createCall('saved/cat', { sdp: 'v=0\r\noffer' })).toEqual(
      responses[1]
    );
    expect(await manager.voice.getCallStatus('saved/cat', 'call/1')).toEqual(responses[2]);
    expect(await manager.voice.endCall('saved/cat', 'call/1')).toEqual(responses[3]);
    const calls = fetchMock.mock.calls;
    expect(calls.map(([url, init]) => [url, init.method])).toEqual([
      ['https://agent.example.test/api/conversations/saved%2Fcat/voice', 'GET'],
      ['https://agent.example.test/api/conversations/saved%2Fcat/voice/realtime', 'POST'],
      ['https://agent.example.test/api/conversations/saved%2Fcat/voice/realtime/call%2F1', 'GET'],
      [
        'https://agent.example.test/api/conversations/saved%2Fcat/voice/realtime/call%2F1',
        'DELETE',
      ],
    ]);
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ sdp: 'v=0\r\noffer' });
    expect(calls.every(([, init]) => init.headers['X-Session-API-Key'] === 'session-fixture')).toBe(
      true
    );
  } finally {
    manager.close();
  }
});

it('propagates a rejected call without retrying or switching providers', async () => {
  global.fetch = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: 'Voice requires an Insider Cat controller' }), {
      status: 409,
      headers: { 'content-type': 'application/json' },
    })
  );
  const voice = new VoiceClient({ host: 'https://agent.example.test' });
  try {
    await expect(voice.createCall('conversation', { sdp: 'v=0' })).rejects.toMatchObject({
      name: HttpError.name,
      status: 409,
      response: { detail: 'Voice requires an Insider Cat controller' },
    });
    expect(global.fetch).toHaveBeenCalledTimes(1);
  } finally {
    voice.close();
  }
});

it('lets a caller cancel an outstanding status poll', async () => {
  global.fetch = vi.fn().mockImplementation(
    (_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError'))
        );
      })
  );
  const voice = new VoiceClient({ host: 'https://agent.example.test' });
  const controller = new AbortController();
  try {
    const pending = voice.getCallStatus('conversation', 'call', controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(global.fetch).toHaveBeenCalledTimes(1);
  } finally {
    voice.close();
  }
});
