import { ACP_PROVIDERS, getAcpProvider } from '../index';
import type { ACPProviderKey } from '../index';

/**
 * The canvas ACP authentication section (#3728) renders provider-specific
 * credential forms by reading the descriptor fields off {@link ACP_PROVIDERS}
 * rather than hardcoding provider lists. These tests lock in that every
 * built-in provider exposes the credential metadata that UI relies on:
 * `api_key_env_var`, `base_url_env_var`, and `file_secrets`.
 */
describe('ACP provider credential descriptors', () => {
  const KEYS: ACPProviderKey[] = ['claude-code', 'codex', 'gemini-cli'];

  it('exposes an entry for each built-in provider', () => {
    for (const key of KEYS) {
      expect(ACP_PROVIDERS[key]).toBeDefined();
      expect(ACP_PROVIDERS[key].key).toBe(key);
    }
  });

  it('defines the credential descriptor fields on every provider', () => {
    for (const key of KEYS) {
      const provider = ACP_PROVIDERS[key];
      // api_key_env_var / base_url_env_var are `string | null` — present (not
      // undefined) on every provider so callers can branch on the value.
      expect(provider).toHaveProperty('api_key_env_var');
      expect(provider).toHaveProperty('base_url_env_var');
      expect(Array.isArray(provider.file_secrets)).toBe(true);
    }
  });

  it.each<[ACPProviderKey, string, string]>([
    ['claude-code', 'ANTHROPIC_API_KEY', 'ANTHROPIC_BASE_URL'],
    ['codex', 'OPENAI_API_KEY', 'OPENAI_BASE_URL'],
    ['gemini-cli', 'GEMINI_API_KEY', 'GEMINI_BASE_URL'],
  ])('%s declares its api_key/base_url env vars', (key, apiKeyEnvVar, baseUrlEnvVar) => {
    const provider = ACP_PROVIDERS[key];
    expect(provider.api_key_env_var).toBe(apiKeyEnvVar);
    expect(provider.base_url_env_var).toBe(baseUrlEnvVar);
  });

  it('uses the maintained Codex adapter and exposes GPT-5.6 models', () => {
    const codex = ACP_PROVIDERS.codex;
    expect(codex.default_command).toEqual(['npx', '-y', '@agentclientprotocol/codex-acp@1.1.7']);
    expect(codex.default_session_mode).toBe('agent-full-access');
    expect(codex.available_models.map((model) => model.id)).toEqual(
      expect.arrayContaining(['gpt-5.6', 'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna'])
    );
  });

  describe('file_secrets', () => {
    it('claude-code authenticates via env var only (no file secrets)', () => {
      expect(ACP_PROVIDERS['claude-code'].file_secrets).toEqual([]);
    });

    it('codex declares its ChatGPT auth.json file secret', () => {
      const fileSecrets = ACP_PROVIDERS['codex'].file_secrets;
      expect(fileSecrets).toHaveLength(1);
      expect(fileSecrets[0]).toMatchObject({
        secret_name: 'CODEX_AUTH_JSON',
        filename: 'auth.json',
        env_var: 'CODEX_HOME',
      });
    });

    it('gemini-cli declares its Vertex SA credential file secret', () => {
      const fileSecrets = ACP_PROVIDERS['gemini-cli'].file_secrets;
      expect(fileSecrets).toHaveLength(1);
      expect(fileSecrets[0]).toMatchObject({
        secret_name: 'GOOGLE_APPLICATION_CREDENTIALS_JSON',
        filename: 'gcloud-credentials.json',
        env_var: 'GOOGLE_APPLICATION_CREDENTIALS',
      });
    });

    it('every file secret carries the descriptor fields the canvas renders from', () => {
      for (const key of KEYS) {
        for (const spec of ACP_PROVIDERS[key].file_secrets) {
          expect(typeof spec.secret_name).toBe('string');
          expect(spec.secret_name.length).toBeGreaterThan(0);
          expect(typeof spec.filename).toBe('string');
          expect(spec.filename.length).toBeGreaterThan(0);
          expect(typeof spec.env_var).toBe('string');
          expect(spec.env_var.length).toBeGreaterThan(0);
        }
      }
    });
  });

  describe('getAcpProvider', () => {
    it('returns the descriptor (with credential fields) for a known key', () => {
      const provider = getAcpProvider('codex');
      expect(provider).not.toBeNull();
      expect(provider?.api_key_env_var).toBe('OPENAI_API_KEY');
      expect(provider?.base_url_env_var).toBe('OPENAI_BASE_URL');
    });

    it('returns null for the custom discriminator and unknown / falsy keys', () => {
      expect(getAcpProvider('custom')).toBeNull();
      expect(getAcpProvider('nonexistent')).toBeNull();
      expect(getAcpProvider(null)).toBeNull();
      expect(getAcpProvider(undefined)).toBeNull();
    });
  });
});
