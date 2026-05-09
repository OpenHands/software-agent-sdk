/**
 * Models for auxiliary Agent Server APIs.
 */

import { LLM } from '../types/base';

export interface AliveStatus {
  status: string;
}

export interface ReadyStatus {
  status: string;
  message?: string;
}

export interface ProvidersResponse {
  providers: string[];
}

export interface ModelsResponse {
  models: string[];
}

export interface VerifiedModelsResponse {
  models: Record<string, string[]>;
}

export interface SettingsSchema {
  model_name: string;
  sections: Array<Record<string, unknown>>;
}

export interface ExposedUrl {
  name: string;
  url: string;
  port: number;
}

export interface OrgConfig {
  repository: string;
  provider: string;
  org_repo_url: string;
  org_name: string;
}

export interface SandboxConfig {
  exposed_urls: ExposedUrl[];
}

export interface SkillsRequest {
  load_public?: boolean;
  load_user?: boolean;
  load_project?: boolean;
  load_org?: boolean;
  marketplace_path?: string | null;
  project_dir?: string | null;
  org_config?: OrgConfig | null;
  sandbox_config?: SandboxConfig | null;
}

export interface SkillInfo {
  name: string;
  type: 'repo' | 'knowledge' | 'agentskills';
  content: string;
  triggers: string[];
  source?: string | null;
  description?: string | null;
  is_agentskills_format?: boolean;
}

export interface SkillsResponse {
  skills: SkillInfo[];
  sources: Record<string, number>;
}

export interface SyncResponse {
  status: 'success' | 'error';
  message: string;
}

export interface DesktopUrlResponse {
  url: string | null;
}

export interface VSCodeUrlResponse {
  url: string | null;
}

export interface VSCodeStatusResponse {
  running: boolean;
  enabled: boolean;
  message?: string;
}

export interface ProfileInfo {
  name: string;
  model: string | null;
  base_url: string | null;
  api_key_set: boolean;
}

export interface ProfileListResponse {
  profiles: ProfileInfo[];
  active_profile: string | null;
}

export interface ProfileDetailResponse {
  name: string;
  config: Record<string, unknown>;
  api_key_set: boolean;
}

export interface ProfileMutationResponse {
  name: string;
  message: string;
}

export interface ActivateProfileResponse {
  name: string;
  message: string;
  llm_applied: boolean;
}

export interface SaveProfileRequest {
  llm: LLM;
  include_secrets?: boolean;
}

export interface RenameProfileRequest {
  new_name: string;
}

export type ExposeSecretsMode = 'encrypted' | 'plaintext';
