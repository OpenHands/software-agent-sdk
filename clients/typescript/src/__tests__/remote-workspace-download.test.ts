import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('RemoteWorkspace downloads', () => {
  let workspace: RemoteWorkspace;

  beforeEach(() => {
    workspace = new RemoteWorkspace({ host: 'https://example.com', workingDir: '/workspace' });
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response('file contents'));
  });

  afterEach(() => {
    workspace.close();
    vi.restoreAllMocks();
  });

  it('rejects browser downloads in Node.js before making a request', async () => {
    await expect(workspace.downloadAndSave('/workspace/file.txt')).rejects.toThrow(
      'downloadAndSave() is only available in browser environments. ' +
        'Use downloadAsBlob() or downloadAsText() in Node.js.'
    );

    expect(global.fetch).not.toHaveBeenCalled();
  });

  it.each(['downloadAsBlob', 'downloadAsText'] as const)(
    'keeps %s available in Node.js',
    async (method) => {
      const content = await workspace[method]('/workspace/file.txt');

      expect(content instanceof Blob ? await content.text() : content).toBe('file contents');
    }
  );

  it('returns a non-UTF-8 binary file byte for byte', async () => {
    const bytes = [0xff, 0x00, 0x80, 0xfe, 0x41];
    vi.mocked(global.fetch).mockImplementation(
      async () =>
        new Response(new Uint8Array(bytes), {
          headers: { 'content-type': 'application/octet-stream' },
        })
    );

    const blob = await workspace.downloadAsBlob('/workspace/file.bin');
    expect([...new Uint8Array(await blob.arrayBuffer())]).toEqual(bytes);

    const result = await workspace.fileDownload('/workspace/file.bin');
    expect(result.file_size).toBe(bytes.length);
    expect(result.content).toBeInstanceOf(Blob);
    expect([...new Uint8Array(await (result.content as Blob).arrayBuffer())]).toEqual(bytes);
  });

  it('triggers a download with the requested filename when a document is available', async () => {
    const link = { href: '', download: '', click: vi.fn() };
    const body = { appendChild: vi.fn(), removeChild: vi.fn() };
    Object.defineProperty(global, 'document', {
      configurable: true,
      value: { createElement: () => link, body },
    });
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download');
    const revokeObjectURL = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    try {
      await workspace.downloadAndSave('/workspace/file.txt', 'saved.txt');

      expect(link.href).toBe('blob:download');
      expect(link.download).toBe('saved.txt');
      expect(link.click).toHaveBeenCalledTimes(1);
      expect(body.removeChild).toHaveBeenCalledWith(link);
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:download');
    } finally {
      Reflect.deleteProperty(global, 'document');
    }
  });
});
