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
        baseUrl: 'http://localhost:8000',
        apiKey: 'test-key'
      };
      
      const conversation = new RemoteConversation(config);
      expect(conversation).toBeInstanceOf(RemoteConversation);
    });

    it('should have workspace property', () => {
      const config = {
        baseUrl: 'http://localhost:8000',
        apiKey: 'test-key'
      };
      
      const conversation = new RemoteConversation(config);
      expect(conversation.workspace).toBeDefined();
      expect(conversation.workspace).toBeInstanceOf(RemoteWorkspace);
    });
  });

  describe('RemoteWorkspace', () => {
    it('should create instance with http client', () => {
      const mockHttpClient = {
        get: jest.fn(),
        post: jest.fn(),
        put: jest.fn(),
        delete: jest.fn()
      };
      
      const workspace = new RemoteWorkspace(mockHttpClient as any);
      expect(workspace).toBeInstanceOf(RemoteWorkspace);
    });
  });
});