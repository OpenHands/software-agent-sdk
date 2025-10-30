import { Conversation, Agent, Workspace, RemoteConversation, RemoteWorkspace } from '../index';

describe('OpenHands Agent Server TypeScript Client', () => {
  describe('Exports', () => {
    it('should export Conversation', () => {
      expect(Conversation).toBeDefined();
      expect(typeof Conversation).toBe('function');
    });

    it('should export Agent', () => {
      expect(Agent).toBeDefined();
      expect(typeof Agent).toBe('function');
    });

    it('should export Workspace', () => {
      expect(Workspace).toBeDefined();
      expect(typeof Workspace).toBe('function');
    });

    it('should export RemoteConversation for backwards compatibility', () => {
      expect(RemoteConversation).toBeDefined();
      expect(typeof RemoteConversation).toBe('function');
    });

    it('should export RemoteWorkspace for backwards compatibility', () => {
      expect(RemoteWorkspace).toBeDefined();
      expect(typeof RemoteWorkspace).toBe('function');
    });
  });

  describe('New API - Conversation', () => {
    it('should create instance with agent and workspace', () => {
      const agent = new Agent({
        llm: {
          model: 'gpt-4',
          api_key: 'test-key',
        },
      });

      const workspace = new Workspace({
        host: 'http://localhost:8000',
        workingDir: '/tmp',
        apiKey: 'test-key',
      });

      const conversation = new Conversation(agent, workspace);
      expect(conversation).toBeInstanceOf(RemoteConversation);
      expect(conversation.workspace).toBe(workspace);
    });
  });

  describe('Agent', () => {
    it('should create instance with LLM config', () => {
      const agent = new Agent({
        llm: {
          model: 'gpt-4',
          api_key: 'test-key',
        },
      });

      expect(agent).toBeInstanceOf(Agent);
      expect(agent.kind).toBe('Agent');
      expect(agent.llm.model).toBe('gpt-4');
      expect(agent.llm.api_key).toBe('test-key');
    });

    it('should allow custom kind', () => {
      const agent = new Agent({
        kind: 'CustomAgent',
        llm: {
          model: 'gpt-4',
          api_key: 'test-key',
        },
      });

      expect(agent.kind).toBe('CustomAgent');
    });
  });

  describe('Workspace', () => {
    it('should create instance with options', () => {
      const workspace = new Workspace({
        host: 'http://localhost:8000',
        workingDir: '/tmp',
        apiKey: 'test-key',
      });

      expect(workspace).toBeInstanceOf(RemoteWorkspace);
      expect(workspace.host).toBe('http://localhost:8000');
      expect(workspace.workingDir).toBe('/tmp');
      expect(workspace.apiKey).toBe('test-key');
    });
  });
});
