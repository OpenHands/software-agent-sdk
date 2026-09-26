/**
 * Secret Registry for Conversations
 *
 * Manages secrets and injects them into commands when needed.
 * Provides secret masking in output to prevent accidental exposure.
 * This mirrors the Python SDK's SecretRegistry class.
 */

import { SecretValue } from '../types/base';

// Re-export so existing consumers of this module still find the type
export type { SecretValue } from '../types/base';

/**
 * Secret source types
 */
export type SecretSourceKind = 'static' | 'callable';

/**
 * Base interface for secret sources
 */
export interface SecretSource {
  kind: SecretSourceKind;
  getValue(): string | null;
}

/**
 * Static secret source - stores a fixed value
 */
export class StaticSecretSource implements SecretSource {
  readonly kind: SecretSourceKind = 'static';
  private value: string;

  constructor(value: string) {
    this.value = value;
  }

  getValue(): string {
    return this.value;
  }
}

/**
 * Callable secret source - evaluates a function to get the value
 */
export class CallableSecretSource implements SecretSource {
  readonly kind: SecretSourceKind = 'callable';
  private getter: () => string;

  constructor(getter: () => string) {
    this.getter = getter;
  }

  getValue(): string | null {
    try {
      return this.getter();
    } catch {
      return null;
    }
  }
}

/**
 * Wrap a SecretValue into a SecretSource
 */
function wrapSecret(value: SecretValue): SecretSource {
  if (typeof value === 'function') {
    return new CallableSecretSource(value);
  }
  return new StaticSecretSource(value);
}

/**
 * Manages secrets and injects them into bash commands when needed.
 *
 * The secret registry stores a mapping of secret keys to SecretSources
 * that retrieve the actual secret values. When a bash command is about to be
 * executed, it scans the command for any secret keys and provides the
 * corresponding environment variables.
 *
 * Additionally, it tracks the latest exported values to enable consistent masking
 * even when callable secrets fail on subsequent calls.
 *
 * Example:
 * ```typescript
 * const registry = new SecretRegistry();
 * registry.updateSecrets({
 *   'API_KEY': 'sk-secret-key',
 *   'DYNAMIC_TOKEN': () => getToken(),
 * });
 *
 * // Get env vars for a command
 * const envVars = registry.getSecretsAsEnvVars('curl -H "Authorization: Bearer $API_KEY" ...');
 *
 * // Mask secrets in output
 * const maskedOutput = registry.maskSecretsInOutput('Response with sk-secret-key visible');
 * // Returns: 'Response with <secret-hidden> visible'
 * ```
 */
export class SecretRegistry {
  /** Map of secret key to secret source */
  private secretSources: Map<string, SecretSource> = new Map();

  /** Cache of successfully exported values for masking */
  private exportedValues: Map<string, string> = new Map();

  /**
   * Add or update secrets in the registry.
   *
   * @param secrets - Dictionary mapping secret keys to values or callable functions
   */
  updateSecrets(secrets: Record<string, SecretValue>): void {
    for (const [key, value] of Object.entries(secrets)) {
      this.secretSources.set(key, wrapSecret(value));
    }
  }

  /**
   * Remove a secret from the registry.
   *
   * @param key - The secret key to remove
   */
  removeSecret(key: string): void {
    this.secretSources.delete(key);
    this.exportedValues.delete(key);
  }

  /**
   * Clear all secrets from the registry.
   */
  clearSecrets(): void {
    this.secretSources.clear();
    this.exportedValues.clear();
  }

  /**
   * Get the number of registered secrets.
   */
  get size(): number {
    return this.secretSources.size;
  }

  /**
   * Get all registered secret keys.
   */
  get keys(): string[] {
    return Array.from(this.secretSources.keys());
  }

  /**
   * Find all secret keys mentioned in the given text.
   * Uses case-insensitive matching.
   *
   * @param text - The text to search for secret keys
   * @returns Set of secret keys found in the text
   */
  findSecretsInText(text: string): Set<string> {
    const foundKeys = new Set<string>();
    const lowerText = text.toLowerCase();

    for (const key of this.secretSources.keys()) {
      if (lowerText.includes(key.toLowerCase())) {
        foundKeys.add(key);
      }
    }

    return foundKeys;
  }

  /**
   * Get secrets that should be exported as environment variables for a command.
   *
   * @param command - The bash command to check for secret references
   * @returns Dictionary of environment variables to export (key -> value)
   */
  getSecretsAsEnvVars(command: string): Record<string, string> {
    const foundSecrets = this.findSecretsInText(command);

    if (foundSecrets.size === 0) {
      return {};
    }

    const envVars: Record<string, string> = {};

    for (const key of foundSecrets) {
      const source = this.secretSources.get(key);
      if (!source) continue;

      const value = source.getValue();
      if (value) {
        envVars[key] = value;
        this.exportedValues.set(key, value);
      }
    }

    return envVars;
  }

  /**
   * Mask secret values in the given text.
   *
   * This method uses the currently exported values to ensure comprehensive masking.
   * It replaces all known secret values with '<secret-hidden>'.
   *
   * @param text - The text to mask secrets in
   * @returns Text with secret values replaced by <secret-hidden>
   */
  maskSecretsInOutput(text: string): string {
    if (!text) {
      return text;
    }

    let maskedText = text;

    // Mask using currently exported values
    for (const value of this.exportedValues.values()) {
      if (value) {
        // Use a global replace
        maskedText = maskedText.split(value).join('<secret-hidden>');
      }
    }

    // Also try to get fresh values from sources (in case they weren't exported yet)
    for (const [key, source] of this.secretSources) {
      try {
        const value = source.getValue();
        if (value && !this.exportedValues.has(key)) {
          maskedText = maskedText.split(value).join('<secret-hidden>');
        }
      } catch {
        // Ignore errors when trying to mask - we'll use cached values
      }
    }

    return maskedText;
  }

  /**
   * Check if a specific secret key is registered.
   *
   * @param key - The secret key to check
   * @returns True if the key is registered
   */
  hasSecret(key: string): boolean {
    return this.secretSources.has(key);
  }

  /**
   * Serialize the registry state for persistence.
   * Note: Callable secrets cannot be serialized and will be omitted.
   * Secret values are redacted by default for security.
   *
   * @param exposeSecrets - If true, include actual secret values (use with caution!)
   * @returns Serialized registry state
   */
  serialize(exposeSecrets: boolean = false): Record<string, string | null> {
    const result: Record<string, string | null> = {};

    for (const [key, source] of this.secretSources) {
      if (source.kind === 'callable') {
        // Callable secrets can't be serialized
        result[key] = null;
      } else if (exposeSecrets) {
        result[key] = source.getValue();
      } else {
        result[key] = '**********';
      }
    }

    return result;
  }

  /**
   * Restore secrets from serialized state.
   * Only static secrets can be restored.
   *
   * @param state - Previously serialized state
   */
  deserialize(state: Record<string, string | null>): void {
    for (const [key, value] of Object.entries(state)) {
      if (value !== null && value !== '**********') {
        this.secretSources.set(key, new StaticSecretSource(value));
      }
    }
  }
}
