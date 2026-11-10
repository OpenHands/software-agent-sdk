import type { SecretObject } from './conversation';

/** One model an ACP server offers; mirrors `openhands.sdk.agent.acp_models.ACPModelInfo`. */
export interface ACPModelInfo {
  model_id: string;
  name?: string | null;
  description?: string | null;
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
