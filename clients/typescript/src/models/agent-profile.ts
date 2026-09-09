/**
 * AgentProfile — TypeScript mirror of the Python SDK's
 * `openhands.sdk.profiles.agent_profile` module.
 *
 * The AgentProfile union is discriminated on `agent_kind` and carries
 * *references* (llm_profile_ref / mcp_server_refs) rather than resolved
 * credentials, mirroring the Python design. See epic #3713.
 */

import type { ACPProviderKey } from './acp';

// ── Shared supporting types ──────────────────────────────────────────────────

/** Discriminator across the {@link AgentProfile} union. */
export type AgentKind = 'openhands' | 'acp';

/** ACP backend selector: a built-in provider key or the `'custom'` sentinel. */
export type ACPServerKind = ACPProviderKey | 'custom';

/** Secret-free critic/refinement policy stored inside an OpenHands profile. */
export interface ProfileVerificationSettings {
  critic_enabled: boolean;
  critic_mode: string;
  enable_iterative_refinement: boolean;
  critic_threshold: number;
  max_refinement_iterations: number;
  critic_server_url: string | null;
  critic_model_name: string | null;
}

// ── Profile variants ─────────────────────────────────────────────────────────

interface AgentProfileBase {
  schema_version?: number;
  /** Stable UUID handle — never changes on rename. */
  id: string;
  /** Human-facing, renameable key. */
  name: string;
  /** Monotonic revision counter, bumped on each saved edit. */
  revision: number;
  /**
   * Which of the user's globally configured MCP servers to expose.
   * `null` = all; `[]` = none; a non-null list = filter to the named keys.
   */
  mcp_server_refs: string[] | null;
}

/** `agent_kind="openhands"` variant — references an LLM profile by name. */
export interface OpenHandsAgentProfile extends AgentProfileBase {
  agent_kind: 'openhands';
  /** Name of the LLM profile to resolve. */
  llm_profile_ref: string;
  agent: string;
  skills: unknown[];
  system_message_suffix: string | null;
  condenser: unknown;
  verification: ProfileVerificationSettings;
  enable_sub_agents: boolean;
  tool_concurrency_limit: number;
}

/** `agent_kind="acp"` variant — names an ACP backend; stores no credential. */
export interface ACPAgentProfile extends AgentProfileBase {
  agent_kind: 'acp';
  /** ACP provider key, or `'custom'` for a user-supplied command. */
  acp_server: ACPServerKind;
  acp_model: string | null;
  acp_session_mode: string | null;
  acp_prompt_timeout: number;
  acp_command: string | null;
  acp_args: string[] | null;
}

/**
 * Discriminated union on `agent_kind`. Use the `agent_kind` field to narrow:
 * ```ts
 * if (profile.agent_kind === 'openhands') { ... }
 * ```
 */
export type AgentProfile = OpenHandsAgentProfile | ACPAgentProfile;

/**
 * Request body for `POST /api/agent-profiles/{name}` (create or overwrite).
 *
 * Discriminated on `agent_kind`, so a payload cannot mix fields from the other
 * variant (the server model is `extra=forbid`). Non-discriminant fields are
 * optional: server-managed identity (`id`/`revision`/`schema_version`) is
 * minted on create and preserved on overwrite. A field left unset takes the
 * server-side default where one exists — except `llm_profile_ref`, which is
 * required for the `openhands` variant (omitting it yields a 422).
 */
export type AgentProfileSaveInput =
  | ({ agent_kind: 'openhands' } & Partial<Omit<OpenHandsAgentProfile, 'agent_kind'>>)
  | ({ agent_kind: 'acp' } & Partial<Omit<ACPAgentProfile, 'agent_kind'>>);

// ── Summary projection (list endpoint) ──────────────────────────────────────

/** Lightweight summary returned by the list endpoint. */
export interface AgentProfileSummary {
  id: string | null;
  name: string;
  agent_kind: AgentKind;
  revision: number | null;
  llm_profile_ref: string | null;
  mcp_server_refs: string[] | null;
}

// ── Materialize diagnostics ──────────────────────────────────────────────────

/**
 * Dry-run resolve report returned by `POST /api/agent-profiles/{name}/materialize`.
 * Mirrors `openhands.sdk.profiles.resolver.AgentProfileDiagnostics` field-for-field.
 *
 * Dangling references are reported here (valid=false) rather than as HTTP errors.
 */
export interface AgentProfileDiagnostics {
  agent_kind: AgentKind;
  valid: boolean;
  errors: string[];

  // OpenHands LLM reference.
  llm_profile_ref: string | null;
  llm_profile_resolved: boolean;
  llm_api_key_set: boolean;

  // MCP composition (both variants).
  mcp_server_refs: string[] | null;
  resolved_mcp_servers: string[];
  dangling_mcp_server_refs: string[];

  // ACP provider credential channels (ACP variant only).
  acp_api_key_secret_name: string | null;
  acp_base_url_secret_name: string | null;
  acp_file_secret_names: string[];

  // Redacted resolved settings, present iff valid.
  resolved_settings: Record<string, unknown> | null;
}

// ── Provenance (Part B — lands with PR #3784) ────────────────────────────────

/**
 * Provenance snapshot recorded when a profile launches a conversation.
 * Mirrors `openhands.sdk.profiles.agent_profile.LaunchedProfile`.
 *
 * Stored on the conversation so clients can identify which profile is current
 * without fragile settings-comparison. See epic #3713.
 */
export interface LaunchedProfile {
  /** Stable id of the profile that launched the conversation. */
  profile_id: string;
  /** Revision of the profile at launch time. */
  revision: number;
}
