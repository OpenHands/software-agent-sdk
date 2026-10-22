export type SettingsDocumentValue =
  | boolean
  | number
  | string
  | null
  | SettingsDocumentValue[]
  | { [key: string]: SettingsDocumentValue };

export type SettingsDocumentObject = { [key: string]: SettingsDocumentValue };

export interface SettingsSecretReference {
  ref: string;
  is_set: boolean;
}

export interface ProductSettings extends SettingsDocumentObject {
  language: string;
  enable_sound_notifications: boolean;
  user_consents_to_analytics: boolean | null;
  git_user_name: string;
  git_user_email: string;
  title_llm_profile: string | null;
  disabled_skills: string[];
  enabled_skills: string[] | null;
  run_router_at_conversation_start: boolean;
  workspace_file_discovery: SettingsDocumentObject;
  default_profile_migrated: boolean;
}

export interface LLMProfileDocument {
  settings: SettingsDocumentObject;
  secret_references: Record<string, SettingsSecretReference>;
}

export interface ProviderConnectionDocument {
  id: string;
  display_name: string;
  provider: string;
  base_url: string | null;
  created_at: number | null;
  updated_at: number | null;
  api_key: SettingsSecretReference | null;
}

export interface SettingsProfilePointers {
  active_llm_profile: string | null;
  active_agent_profile_id: string | null;
  active_model_router: string | null;
}

export interface SettingsResources {
  llm_profiles: Record<string, LLMProfileDocument>;
  agent_profiles: SettingsDocumentObject;
  provider_connections: Record<string, ProviderConnectionDocument>;
  model_routers: SettingsDocumentObject;
  marketplaces: SettingsDocumentValue[];
  credentials: Record<string, SettingsSecretReference>;
  pointers: SettingsProfilePointers;
}

export interface SettingsDocument {
  schema_version: 1;
  agent: SettingsDocumentObject;
  conversation: SettingsDocumentObject;
  product: ProductSettings;
  resources: SettingsResources;
}

export interface SettingsProfilePointersPatch {
  active_llm_profile?: string | null;
  active_agent_profile_id?: string | null;
  active_model_router?: string | null;
}

export interface SettingsDocumentPatch {
  agent?: SettingsDocumentObject;
  conversation?: SettingsDocumentObject;
  product?: SettingsDocumentObject | null;
  resources?: { pointers?: SettingsProfilePointersPatch };
}
