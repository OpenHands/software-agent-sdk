/**
 * Tests for SecretRegistry class
 *
 * These tests mirror the Python SDK's secrets manager tests to ensure
 * consistent behavior across implementations.
 */

import {
  SecretRegistry,
  StaticSecretSource,
  CallableSecretSource,
} from '../conversation/secret-registry';

describe('SecretRegistry', () => {
  describe('Update Secrets', () => {
    it('should update secrets with static string values', () => {
      const registry = new SecretRegistry();
      const secrets = {
        API_KEY: 'test-api-key',
        DATABASE_URL: 'postgresql://localhost/test',
      };

      registry.updateSecrets(secrets);

      expect(registry.size).toBe(2);
      expect(registry.hasSecret('API_KEY')).toBe(true);
      expect(registry.hasSecret('DATABASE_URL')).toBe(true);
    });

    it('should overwrite existing secrets', () => {
      const registry = new SecretRegistry();

      // Add initial secret
      registry.updateSecrets({ API_KEY: 'old-value' });
      expect(registry.size).toBe(1);

      // Update with new value
      registry.updateSecrets({ API_KEY: 'new-value', NEW_KEY: 'key-value' });
      expect(registry.size).toBe(2);

      // Verify the API_KEY was updated
      const envVars = registry.getSecretsAsEnvVars('$API_KEY');
      expect(envVars.API_KEY).toBe('new-value');
    });

    it('should support callable secret sources', () => {
      const registry = new SecretRegistry();
      let callCount = 0;

      registry.updateSecrets({
        DYNAMIC_TOKEN: () => {
          callCount++;
          return `token-${callCount}`;
        },
      });

      // First call
      const envVars1 = registry.getSecretsAsEnvVars('$DYNAMIC_TOKEN');
      expect(envVars1.DYNAMIC_TOKEN).toBe('token-1');

      // Second call - should invoke the function again
      const envVars2 = registry.getSecretsAsEnvVars('$DYNAMIC_TOKEN');
      expect(envVars2.DYNAMIC_TOKEN).toBe('token-2');
    });
  });

  describe('Find Secrets In Text', () => {
    it('should find secrets in text (case insensitive)', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'test-key',
        DATABASE_PASSWORD: 'test-password',
      });

      // Test various case combinations
      let found = registry.findSecretsInText('echo api_key=$API_KEY');
      expect(found.has('API_KEY')).toBe(true);

      found = registry.findSecretsInText('echo $database_password');
      expect(found.has('DATABASE_PASSWORD')).toBe(true);

      found = registry.findSecretsInText('API_KEY and DATABASE_PASSWORD');
      expect(found.has('API_KEY')).toBe(true);
      expect(found.has('DATABASE_PASSWORD')).toBe(true);

      found = registry.findSecretsInText('echo hello world');
      expect(found.size).toBe(0);
    });

    it('should handle partial matches correctly', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'test-key',
        API: 'test-api', // Shorter key contained in API_KEY
      });

      // Both should be found since "API" is contained in "API_KEY"
      const found = registry.findSecretsInText('export API_KEY=$API_KEY');
      expect(found.has('API_KEY')).toBe(true);
      expect(found.has('API')).toBe(true);
    });
  });

  describe('Get Secrets As Env Vars', () => {
    it('should return env vars for secrets found in command', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'test-api-key',
        DATABASE_URL: 'postgresql://localhost/test',
      });

      const envVars = registry.getSecretsAsEnvVars("curl -H 'X-API-Key: $API_KEY'");
      expect(envVars).toEqual({ API_KEY: 'test-api-key' });
    });

    it('should return multiple env vars when multiple secrets found', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'test-api-key',
        DATABASE_URL: 'postgresql://localhost/test',
      });

      const envVars = registry.getSecretsAsEnvVars(
        'export API_KEY=$API_KEY && export DATABASE_URL=$DATABASE_URL'
      );
      expect(envVars).toEqual({
        API_KEY: 'test-api-key',
        DATABASE_URL: 'postgresql://localhost/test',
      });
    });

    it('should return empty object when no secrets found', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({ API_KEY: 'test-key' });

      const envVars = registry.getSecretsAsEnvVars('echo hello world');
      expect(envVars).toEqual({});
    });

    it('should handle callable exceptions gracefully', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        FAILING_SECRET: () => {
          throw new Error('Secret retrieval failed');
        },
        WORKING_SECRET: () => 'working-value',
      });

      // Should not throw, should skip failing secret
      const envVars = registry.getSecretsAsEnvVars(
        'export FAILING_SECRET=$FAILING_SECRET && export WORKING_SECRET=$WORKING_SECRET'
      );

      // Only working secret should be returned
      expect(envVars).toEqual({ WORKING_SECRET: 'working-value' });
    });
  });

  describe('Mask Secrets In Output', () => {
    it('should mask secret values in output', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'sk-secret-key-12345',
        PASSWORD: 'super-secret-password',
      });

      // Export the secrets first (so they're in the cache)
      registry.getSecretsAsEnvVars('$API_KEY $PASSWORD');

      const output = 'Response: sk-secret-key-12345, Password: super-secret-password';
      const masked = registry.maskSecretsInOutput(output);

      expect(masked).toBe('Response: <secret-hidden>, Password: <secret-hidden>');
    });

    it('should return original text when no secrets to mask', () => {
      const registry = new SecretRegistry();
      const output = 'Hello, World!';
      const masked = registry.maskSecretsInOutput(output);
      expect(masked).toBe('Hello, World!');
    });

    it('should handle empty text', () => {
      const registry = new SecretRegistry();
      expect(registry.maskSecretsInOutput('')).toBe('');
    });

    it('should mask multiple occurrences of same secret', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({ TOKEN: 'abc123' });
      registry.getSecretsAsEnvVars('$TOKEN');

      const output = 'Token: abc123, Again: abc123, And again: abc123';
      const masked = registry.maskSecretsInOutput(output);

      expect(masked).toBe(
        'Token: <secret-hidden>, Again: <secret-hidden>, And again: <secret-hidden>'
      );
    });
  });

  describe('Remove and Clear Secrets', () => {
    it('should remove individual secrets', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        KEY1: 'value1',
        KEY2: 'value2',
      });

      expect(registry.size).toBe(2);
      registry.removeSecret('KEY1');
      expect(registry.size).toBe(1);
      expect(registry.hasSecret('KEY1')).toBe(false);
      expect(registry.hasSecret('KEY2')).toBe(true);
    });

    it('should clear all secrets', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        KEY1: 'value1',
        KEY2: 'value2',
        KEY3: 'value3',
      });

      expect(registry.size).toBe(3);
      registry.clearSecrets();
      expect(registry.size).toBe(0);
    });
  });

  describe('Keys Property', () => {
    it('should return all registered secret keys', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'key1',
        DATABASE_URL: 'url1',
        TOKEN: 'token1',
      });

      const keys = registry.keys;
      expect(keys).toHaveLength(3);
      expect(keys).toContain('API_KEY');
      expect(keys).toContain('DATABASE_URL');
      expect(keys).toContain('TOKEN');
    });
  });

  describe('Serialization', () => {
    it('should serialize with redacted values by default', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'secret-value',
        TOKEN: 'another-secret',
      });

      const serialized = registry.serialize();
      expect(serialized.API_KEY).toBe('**********');
      expect(serialized.TOKEN).toBe('**********');
    });

    it('should serialize with exposed values when requested', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        API_KEY: 'secret-value',
      });

      const serialized = registry.serialize(true);
      expect(serialized.API_KEY).toBe('secret-value');
    });

    it('should serialize callable secrets as null', () => {
      const registry = new SecretRegistry();
      registry.updateSecrets({
        STATIC: 'static-value',
        CALLABLE: () => 'dynamic-value',
      });

      const serialized = registry.serialize(true);
      expect(serialized.STATIC).toBe('static-value');
      expect(serialized.CALLABLE).toBeNull();
    });

    it('should deserialize serialized state', () => {
      const registry = new SecretRegistry();
      registry.deserialize({
        API_KEY: 'restored-value',
        TOKEN: 'another-value',
        REDACTED: '**********', // Should be ignored
        NULL_VALUE: null, // Should be ignored
      });

      expect(registry.hasSecret('API_KEY')).toBe(true);
      expect(registry.hasSecret('TOKEN')).toBe(true);
      expect(registry.hasSecret('REDACTED')).toBe(false);
      expect(registry.hasSecret('NULL_VALUE')).toBe(false);

      const envVars = registry.getSecretsAsEnvVars('$API_KEY $TOKEN');
      expect(envVars.API_KEY).toBe('restored-value');
      expect(envVars.TOKEN).toBe('another-value');
    });
  });

  describe('Secret Sources', () => {
    it('should create StaticSecretSource correctly', () => {
      const source = new StaticSecretSource('test-value');
      expect(source.kind).toBe('static');
      expect(source.getValue()).toBe('test-value');
    });

    it('should create CallableSecretSource correctly', () => {
      let counter = 0;
      const source = new CallableSecretSource(() => {
        counter++;
        return `value-${counter}`;
      });

      expect(source.kind).toBe('callable');
      expect(source.getValue()).toBe('value-1');
      expect(source.getValue()).toBe('value-2');
    });

    it('should handle CallableSecretSource exceptions', () => {
      const source = new CallableSecretSource(() => {
        throw new Error('Failed');
      });

      // Should return null instead of throwing
      expect(source.getValue()).toBeNull();
    });
  });
});
