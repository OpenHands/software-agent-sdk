import { HttpClient } from './http-client';
import type {
  AgentProfile,
  AgentProfileDiagnostics,
  AgentProfileSaveInput,
  AgentProfileSummary,
} from '../models/agent-profile';
import type { ExposeSecretsMode } from '../models/api';

export interface AgentProfilesClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface GetAgentProfileOptions {
  /**
   * Controls server-side `skills[].mcp_tools` secret exposure via
   * `X-Expose-Secrets`:
   * - `"encrypted"`: Fernet-encrypted values (safe for frontend round-trip)
   * - `"plaintext"`: raw secret values (backend clients only)
   * - omitted: values are masked/redacted
   */
  exposeSecrets?: ExposeSecretsMode;
}

export interface AgentProfileListResponse {
  profiles: AgentProfileSummary[];
  active_agent_profile_id: string | null;
}

export interface AgentProfileDetailResponse {
  name: string;
  profile: AgentProfile;
}

export interface AgentProfileMutationResponse {
  name: string;
  message: string;
}

export interface ActivateAgentProfileResponse {
  id: string;
  message: string;
  /** Always false — activation is pointer-only (does not write agent_settings). */
  agent_settings_applied: boolean;
}

/**
 * Client for the agent-server `/api/agent-profiles` endpoints.
 *
 * Mirrors `agent_profiles_router.py` in the OpenHands agent-server.
 * Error status codes surfaced via `HttpError.status`:
 * - 404: profile not found (get/rename/materialize)
 * - 409: profile limit exceeded (save) or new_name already exists (rename)
 * - 422: validation error (save)
 *
 * `delete` is idempotent — a missing name resolves 200, not 404. `materialize`
 * never raises on dangling LLM/MCP refs; they are reported in the response body
 * (`valid: false`, `dangling_mcp_server_refs`), not as a 4xx.
 */
export class AgentProfilesClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: AgentProfilesClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout ?? 60000,
    });
  }

  async listAgentProfiles(): Promise<AgentProfileListResponse> {
    const response = await this.client.get<AgentProfileListResponse>('/api/agent-profiles');
    return response.data;
  }

  async getAgentProfile(
    name: string,
    options: GetAgentProfileOptions = {}
  ): Promise<AgentProfileDetailResponse> {
    const headers: Record<string, string> = {};
    if (options.exposeSecrets) {
      headers['X-Expose-Secrets'] = options.exposeSecrets;
    }
    const response = await this.client.get<AgentProfileDetailResponse>(
      `/api/agent-profiles/${encodeURIComponent(name)}`,
      { headers }
    );
    return response.data;
  }

  async saveAgentProfile(
    name: string,
    profile: AgentProfileSaveInput
  ): Promise<AgentProfileMutationResponse> {
    const response = await this.client.post<AgentProfileMutationResponse>(
      `/api/agent-profiles/${encodeURIComponent(name)}`,
      profile
    );
    return response.data;
  }

  async deleteAgentProfile(name: string): Promise<AgentProfileMutationResponse> {
    const response = await this.client.delete<AgentProfileMutationResponse>(
      `/api/agent-profiles/${encodeURIComponent(name)}`
    );
    return response.data;
  }

  async renameAgentProfile(name: string, newName: string): Promise<AgentProfileMutationResponse> {
    const response = await this.client.post<AgentProfileMutationResponse>(
      `/api/agent-profiles/${encodeURIComponent(name)}/rename`,
      { new_name: newName }
    );
    return response.data;
  }

  /**
   * Activate a profile by its stable UUID.
   * Pointer-only — does NOT write `agent_settings`.
   *
   * @param profileId  The stable `id` UUID of the profile to activate.
   */
  async activateAgentProfile(profileId: string): Promise<ActivateAgentProfileResponse> {
    const response = await this.client.post<ActivateAgentProfileResponse>(
      `/api/agent-profiles/${encodeURIComponent(profileId)}/activate`,
      {}
    );
    return response.data;
  }

  /**
   * Dry-run resolve a profile's LLM/MCP references.
   * Returns an {@link AgentProfileDiagnostics} report; never raises on
   * dangling refs — those appear in the body with `valid: false`.
   *
   * @param name  Profile name (path slug, not the UUID).
   */
  async materializeAgentProfile(name: string): Promise<AgentProfileDiagnostics> {
    const response = await this.client.post<AgentProfileDiagnostics>(
      `/api/agent-profiles/${encodeURIComponent(name)}/materialize`,
      {}
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}
