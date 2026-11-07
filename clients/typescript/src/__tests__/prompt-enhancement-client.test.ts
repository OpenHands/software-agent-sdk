import {
  PROMPT_ENHANCEMENT_CAPABILITY,
  PromptEnhancementClient,
  PromptEnhancementUnavailableError,
} from '../client/prompt-enhancement-client';

const originalFetch = global.fetch;

describe('PromptEnhancementClient', () => {
  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('returns unavailable for a backend without the capability', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ version: '1.40.0', capabilities: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;
    const client = new PromptEnhancementClient({ host: 'https://agent.example.test' });

    await expect(client.checkAvailability('default')).resolves.toEqual({
      available: false,
      code: 'unsupported_backend',
      message: 'This Agent Server does not support prompt enhancement.',
    });
  });

  it('turns an unsupported profile configuration into an availability result', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            version: '1.51.0',
            capabilities: [PROMPT_ENHANCEMENT_CAPABILITY],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            code: 'unsupported_configuration',
            message: 'The selected model configuration is unsupported.',
          }),
          { status: 422, headers: { 'content-type': 'application/json' } }
        )
      ) as typeof fetch;
    const client = new PromptEnhancementClient({ host: 'https://agent.example.test' });

    await expect(client.checkAvailability('default')).resolves.toEqual({
      available: false,
      code: 'unsupported_configuration',
      message: 'The selected model configuration is unsupported.',
    });
  });

  it('posts a typed request and returns the enhanced draft', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            version: '1.51.0',
            capabilities: [PROMPT_ENHANCEMENT_CAPABILITY],
          }),
          { status: 200, headers: { 'content-type': 'application/json' } }
        )
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ enhanced_text: 'Find the cause and explain it.' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      ) as typeof fetch;
    const client = new PromptEnhancementClient({ host: 'https://agent.example.test' });

    await expect(
      client.enhancePrompt({ profile_name: 'default', text: 'Find the cause.' })
    ).resolves.toEqual({ enhanced_text: 'Find the cause and explain it.' });

    const calls = (global.fetch as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls[1][0]).toBe('https://agent.example.test/api/prompt-enhancement/enhance');
    expect(JSON.parse(calls[1][1].body as string)).toEqual({
      profile_name: 'default',
      text: 'Find the cause.',
    });
  });

  it('rejects promptly when the caller aborts', async () => {
    const signals: AbortSignal[] = [];
    global.fetch = vi.fn().mockImplementation((url: string, init: RequestInit) => {
      if (url.endsWith('/server_info')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              version: '1.51.0',
              capabilities: [PROMPT_ENHANCEMENT_CAPABILITY],
            }),
            { status: 200, headers: { 'content-type': 'application/json' } }
          )
        );
      }
      const signal = init.signal as AbortSignal;
      signals.push(signal);
      return new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () =>
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
        );
      });
    }) as unknown as typeof fetch;
    const client = new PromptEnhancementClient({
      host: 'https://agent.example.test',
      timeout: 60000,
    });
    const controller = new AbortController();
    const pending = client.enhancePrompt(
      { profile_name: 'default', text: 'Wait for this prompt.' },
      { signal: controller.signal }
    );
    await vi.waitFor(() => expect(signals).toHaveLength(1));

    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(signals[0].aborted).toBe(true);
  });

  it('cancels the first capability probe before sending a draft', async () => {
    let probeSignal: AbortSignal | undefined;
    global.fetch = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      probeSignal = init.signal as AbortSignal;
      return new Promise((_resolve, reject) => {
        probeSignal?.addEventListener('abort', () =>
          reject(new DOMException('Aborted', 'AbortError'))
        );
      });
    }) as unknown as typeof fetch;
    const client = new PromptEnhancementClient({ host: 'https://agent.example.test' });
    const controller = new AbortController();
    const pending = client.enhancePrompt(
      { profile_name: 'default', text: 'Private draft.' },
      { signal: controller.signal }
    );
    await vi.waitFor(() => expect(probeSignal).toBeDefined());
    controller.abort();

    expect(probeSignal?.aborted).toBe(true);
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('throws a typed error when enhancement is called on an unsupported backend', async () => {
    global.fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ version: '1.40.0', capabilities: [] }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    ) as typeof fetch;
    const client = new PromptEnhancementClient({ host: 'https://agent.example.test' });

    await expect(
      client.enhancePrompt({ profile_name: 'default', text: 'Improve this.' })
    ).rejects.toBeInstanceOf(PromptEnhancementUnavailableError);
  });
});
