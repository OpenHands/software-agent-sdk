import { RemoteWorkspace } from '../workspace/remote-workspace';

describe('RemoteWorkspace downloads', () => {
  let workspace: RemoteWorkspace;

  beforeEach(() => {
    workspace = new RemoteWorkspace({ host: 'https://example.com', workingDir: '/workspace' });
    jest.spyOn(global, 'fetch').mockResolvedValue(new Response('file contents'));
  });

  afterEach(() => {
    workspace.close();
    jest.restoreAllMocks();
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

  it('triggers a download with the requested filename when a document is available', async () => {
    const link = { href: '', download: '', click: jest.fn() };
    const body = { appendChild: jest.fn(), removeChild: jest.fn() };
    Object.defineProperty(global, 'document', {
      configurable: true,
      value: { createElement: () => link, body },
    });
    jest.spyOn(URL, 'createObjectURL').mockReturnValue('blob:download');
    const revokeObjectURL = jest.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

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
