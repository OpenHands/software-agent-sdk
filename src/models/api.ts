/**
 * Models for auxiliary Agent Server APIs.
 */

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
