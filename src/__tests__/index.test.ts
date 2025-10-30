import { RemoteConversation, RemoteWorkspace } from '../index';

describe('OpenHands Agent Server TypeScript Client', () => {
  describe('Exports', () => {
    it('should export RemoteConversation', () => {
      expect(RemoteConversation).toBeDefined();
      expect(typeof RemoteConversation).toBe('function');
    });

    it('should export RemoteWorkspace', () => {
      expect(RemoteWorkspace).toBeDefined();
      expect(typeof RemoteWorkspace).toBe('function');
    });
  });

  describe('RemoteConversation', () => {
    it('should create instance with config', () => {
      const config = {
        host: 'http://localhost:8000',
        apiKey: 'test-key',
      };

      const conversation = new RemoteConversation(config);
      expect(conversation).toBeInstanceOf(RemoteConversation);
    });

    it('should throw error when accessing workspace before initialization', () => {
      const config = {
        host: 'http://localhost:8000',
        apiKey: 'test-key',
      };

      const conversation = new RemoteConversation(config);
      expect(() => conversation.workspace).toThrow(
        'Workspace not initialized. Create or load a conversation first.'
      );
    });
  });

  describe('RemoteWorkspace', () => {
    it('should create instance with options', () => {
      const options = {
        host: 'http://localhost:8000',
        workingDir: '/tmp',
        apiKey: 'test-key',
      };

      const workspace = new RemoteWorkspace(options);
      expect(workspace).toBeInstanceOf(RemoteWorkspace);
      expect(workspace.host).toBe('http://localhost:8000');
      expect(workspace.workingDir).toBe('/tmp');
      expect(workspace.apiKey).toBe('test-key');
    });
  });
});
