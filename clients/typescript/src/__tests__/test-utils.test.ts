/**
 * Unit tests for test utilities
 */

import * as fs from 'fs';
import { getTestConfig, skipIfNoConfig, createTestLLMConfig } from './integration/test-config';
import {
  uniqueFileName,
  uniqueDirName,
  randomContent,
  sleep,
  waitFor,
} from './integration/test-utils';

describe('Test Utilities', () => {
  describe('Test Configuration', () => {
    const originalEnv = { ...process.env };

    beforeEach(() => {
      // Reset environment to original values
      Object.keys(process.env).forEach((key) => {
        if (!(key in originalEnv)) {
          delete process.env[key];
        }
      });
      Object.assign(process.env, originalEnv);
    });

    afterAll(() => {
      // Restore original environment
      Object.keys(process.env).forEach((key) => {
        if (!(key in originalEnv)) {
          delete process.env[key];
        }
      });
      Object.assign(process.env, originalEnv);
    });

    it('should throw if LLM_API_KEY is not set', () => {
      delete process.env.LLM_API_KEY;
      delete process.env.LLM_MODEL;

      expect(() => getTestConfig()).toThrow('LLM_API_KEY');
    });

    it('should throw if LLM_MODEL is not set', () => {
      process.env.LLM_API_KEY = 'test-key';
      delete process.env.LLM_MODEL;

      expect(() => getTestConfig()).toThrow('LLM_MODEL');
    });

    it('should return config when required env vars are set', () => {
      process.env.LLM_API_KEY = 'test-api-key';
      process.env.LLM_MODEL = 'test/model';

      const config = getTestConfig();

      expect(config.llmApiKey).toBe('test-api-key');
      expect(config.llmModel).toBe('test/model');
      expect(config.agentServerUrl).toBe('http://localhost:8010');
    });

    it('should use custom values from environment', () => {
      process.env.LLM_API_KEY = 'custom-key';
      process.env.LLM_MODEL = 'custom/model';
      process.env.AGENT_SERVER_URL = 'http://custom:9000';
      process.env.HOST_WORKSPACE_DIR = '/custom/path';
      process.env.LLM_BASE_URL = 'https://custom-llm.com';

      const config = getTestConfig();

      expect(config.llmApiKey).toBe('custom-key');
      expect(config.llmModel).toBe('custom/model');
      expect(config.agentServerUrl).toBe('http://custom:9000');
      expect(config.hostWorkspaceDir).toBe('/custom/path');
      expect(config.llmBaseUrl).toBe('https://custom-llm.com');
    });

    it('should skip if no config', () => {
      delete process.env.LLM_API_KEY;
      delete process.env.LLM_MODEL;

      expect(skipIfNoConfig()).toBe(true);
    });

    it('should not skip if config exists', () => {
      process.env.LLM_API_KEY = 'test-key';
      process.env.LLM_MODEL = 'test/model';

      expect(skipIfNoConfig()).toBe(false);
    });

    it('should create LLM config from environment', () => {
      process.env.LLM_API_KEY = 'api-key';
      process.env.LLM_MODEL = 'my/model';
      process.env.LLM_BASE_URL = 'https://api.example.com';

      const llmConfig = createTestLLMConfig();

      expect(llmConfig.model).toBe('my/model');
      expect(llmConfig.api_key).toBe('api-key');
      expect(llmConfig.base_url).toBe('https://api.example.com');
    });
  });

  describe('Test Utility Functions', () => {
    const testDir = '/tmp/test-utils-test';

    beforeAll(() => {
      // Create test directory
      if (!fs.existsSync(testDir)) {
        fs.mkdirSync(testDir, { recursive: true });
      }
    });

    afterAll(() => {
      // Cleanup
      if (fs.existsSync(testDir)) {
        fs.rmSync(testDir, { recursive: true });
      }
    });

    it('should generate unique file names', () => {
      const name1 = uniqueFileName('test');
      const name2 = uniqueFileName('test');

      expect(name1).not.toBe(name2);
      expect(name1).toMatch(/^test-\d+-[a-z0-9]+\.txt$/);
    });

    it('should generate unique file names with custom extension', () => {
      const name = uniqueFileName('myfile', 'json');
      expect(name).toMatch(/^myfile-\d+-[a-z0-9]+\.json$/);
    });

    it('should generate unique directory names', () => {
      const name1 = uniqueDirName('dir');
      const name2 = uniqueDirName('dir');

      expect(name1).not.toBe(name2);
      expect(name1).toMatch(/^dir-\d+-[a-z0-9]+$/);
    });

    it('should generate random content', () => {
      const content1 = randomContent(100);
      const content2 = randomContent(100);

      expect(content1).toHaveLength(100);
      expect(content2).toHaveLength(100);
      // Random content should (almost always) be different
      expect(content1).not.toBe(content2);
    });

    it('should sleep for specified duration', async () => {
      const start = Date.now();
      await sleep(100);
      const elapsed = Date.now() - start;

      expect(elapsed).toBeGreaterThanOrEqual(90);
      expect(elapsed).toBeLessThan(200);
    });

    it('should wait for condition to be true', async () => {
      let value = false;
      setTimeout(() => {
        value = true;
      }, 100);

      await waitFor(() => value, { timeout: 1000, interval: 50 });
      expect(value).toBe(true);
    });

    it('should throw on waitFor timeout', async () => {
      await expect(
        waitFor(() => false, { timeout: 100, interval: 20, message: 'test condition' })
      ).rejects.toThrow('Timeout waiting: test condition');
    });
  });
});
