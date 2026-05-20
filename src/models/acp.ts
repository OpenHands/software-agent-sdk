/**
 * ACP (Agent Client Protocol) provider registry — TypeScript mirror of the
 * Python source of truth at
 * `openhands.sdk.settings.acp_providers.ACP_PROVIDERS` in
 * https://github.com/OpenHands/software-agent-sdk.
 *
 * The data lives in `./acp-providers.json` so the Python drift check in
 * `scripts/check-acp-drift.py` can read it without executing TypeScript.
 * To add or modify a provider, edit `acp_providers.py` in software-agent-sdk
 * first, then mirror the change in `acp-providers.json` here. CI will fail
 * until the two match.
 */

import providersData from './acp-providers.json';

/**
 * Stable registry key for a built-in ACP provider.
 *
 * Does **not** include `'custom'` — `custom` is a UI-side discriminator
 * meaning "user typed their own command" and intentionally has no registry
 * entry.
 */
export type ACPProviderKey = 'claude-code' | 'codex' | 'gemini-cli';

/**
 * Immutable metadata record for one built-in ACP provider. Mirrors
 * `openhands.sdk.settings.acp_providers.ACPProviderInfo` field-for-field.
 */
export interface ACPProviderInfo {
  readonly key: ACPProviderKey;
  readonly display_name: string;
  readonly default_command: readonly string[];
  /** `null` for providers that authenticate via browser login. */
  readonly api_key_env_var: string | null;
  /** `null` if the provider does not support env-based base-URL override. */
  readonly base_url_env_var: string | null;
  /** ACP session-mode ID that suppresses all permission prompts. */
  readonly default_session_mode: string;
  /** Lowercase substring fragments matched against the runtime agent name. */
  readonly agent_name_patterns: readonly string[];
  /** `true` if this provider uses the `set_session_model` protocol call. */
  readonly supports_set_session_model: boolean;
  /** Top-level `_meta` key for model selection, or `null`. */
  readonly session_meta_key: string | null;
}

export const ACP_PROVIDERS: Readonly<Record<ACPProviderKey, ACPProviderInfo>> =
  providersData as Readonly<Record<ACPProviderKey, ACPProviderInfo>>;

/**
 * Return the {@link ACPProviderInfo} for `key`, or `null` if unknown.
 * Mirrors the Python `get_acp_provider` semantics — returns `null` for the
 * UI-side `'custom'` discriminator and for any unknown / falsy value.
 */
export function getAcpProvider(key: string | null | undefined): ACPProviderInfo | null {
  if (!key) {
    return null;
  }
  return (ACP_PROVIDERS as Record<string, ACPProviderInfo | undefined>)[key] ?? null;
}

/**
 * Allow-list of fields that travel on an `ACPAgent` settings payload.
 * Mirrors `openhands.sdk.settings.model.ACPAgentSettings`'s field set.
 * Clients filter ACP-only fields out when switching to a non-ACP variant.
 */
export const ACP_SETTINGS_KEYS: readonly string[] = [
  'acp_command',
  'acp_args',
  'acp_env',
  'acp_model',
  'acp_session_mode',
  'acp_prompt_timeout',
  'acp_server',
];
