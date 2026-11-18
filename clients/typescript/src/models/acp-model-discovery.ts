import type { SecretObject } from './conversation';

/** One model an ACP server offers; mirrors `openhands.sdk.agent.acp_models.ACPModelInfo`. */
export interface ACPModelInfo {
  model_id: string;
  name?: string | null;
  description?: string | null;
}

/** One reasoning-effort level an ACP server offers; mirrors `openhands.sdk.agent.acp_models.ACPEffortOption`. */
export interface ACPEffortOption {
  value: string;
  name?: string | null;
}

export interface ACPModelDiscoveryError {
  /** `ACPAuthRequired`, `ACPStartupTimeout`, `ACPSpawnError` or `ACPInitError`. */
  code: string;
  detail: string;
}

/** Response of `POST /api/acp/models`; mirrors `openhands.sdk.agent.acp_models.ACPModelDiscovery`. */
export interface ACPModelDiscovery {
  agent_name: string | null;
  agent_version: string | null;
  /** The server's own default for the given credentials; may be an alias such as `default`. */
  current_model_id: string | null;
  available_models: ACPModelInfo[];
  /** Effort the server uses for `current_model_id` by default; `null` when the model offers none. */
  current_effort?: string | null;
  /** Effort levels the server offers for `current_model_id`. */
  available_efforts?: ACPEffortOption[];
  /** Effort levels each model offers, keyed by `model_id`; `[]` when it offers none. */
  efforts_by_model?: Record<string, ACPEffortOption[]>;
  supports_runtime_model_switch: boolean;
  error: ACPModelDiscoveryError | null;
}

export interface ACPModelDiscoveryRequest {
  agent_settings: {
    agent_kind: 'acp';
    acp_server: string;
    acp_command?: string[] | null;
    acp_args?: string[];
  };
  /** Same shape as a conversation start request's `secrets`. */
  secrets?: Record<string, SecretObject>;
  /** Ask the server again instead of reusing a cached result. */
  refresh?: boolean;
}
