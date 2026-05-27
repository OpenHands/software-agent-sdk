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
 * One selectable model for a built-in ACP provider's model picker. Mirrors
 * `openhands.sdk.settings.acp_providers.ACPModelOption` field-for-field.
 */
export interface ACPModelOption {
  /** Exact model identifier sent to the ACP server as `acp_model`. */
  readonly id: string;
  /** Human-readable label shown in the model picker (e.g. `"Claude Opus 4.7"`). */
  readonly label: string;
}

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
  /**
   * `true` if this provider selects its *initial* model via the
   * `set_session_model` protocol call (rather than session `_meta`).
   */
  readonly supports_set_session_model: boolean;
  /**
   * `true` if this provider supports `set_session_model` for *runtime*,
   * mid-conversation switching (the capability `switchAcpModel` relies on).
   */
  readonly supports_runtime_model_switch: boolean;
  /** Top-level `_meta` key for model selection, or `null`. */
  readonly session_meta_key: string | null;
  /**
   * Curated `acp_model` candidates surfaced in this provider's model picker.
   * Suggestions, not authoritative access checks — a custom `acp_model` is
   * always allowed, and availability depends on the account's plan tier.
   */
  readonly available_models: readonly ACPModelOption[];
  /**
   * Model ID preselected when none is configured (one of {@link available_models}),
   * or `null` to let the ACP server pick its own default.
   */
  readonly default_model: string | null;
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
