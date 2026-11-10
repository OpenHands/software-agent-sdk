import { HttpClient } from './http-client';
import type { StartConversationRequest } from '../generated/agent-server-schema';

/**
 * Projects (`/api/projects`): a named group of conversations led by an
 * optional coordinator conversation, with the project's files (artifacts) and
 * memory. A conversation belongs to a project when its `project` tag holds the
 * project's id in plain hex.
 *
 * Projects is an experimental feature (`projects` under
 * `/api/settings/experimental`). While it is off, and on servers without
 * Projects, every route answers 404.
 */

export interface ProjectsClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

export interface Project {
  id: string;
  name: string;
  description: string;
  /** The conversation the person mainly talks to; it is also a member. */
  coordinator_conversation_id: string | null;
  /** Agent profile the project's workers start from. Unset, the active one. */
  worker_agent_profile_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectsListResponse {
  projects: Project[];
}

/** What a conversation needs from the person, most urgent first. */
export const PROJECT_ATTENTION_ORDER = [
  'needs_you',
  'needs_input',
  'working',
  'finished',
  'idle',
  'resolved',
] as const;

export type ProjectAttention = (typeof PROJECT_ATTENTION_ORDER)[number];

export interface ProjectConversation {
  conversation_id: string;
  title: string | null;
  execution_status: string;
  attention: ProjectAttention;
  /** Who resolved it: the person, or the server on a high critic score. */
  resolved: 'user' | 'auto' | null;
  /** Why the server resolved it, such as `0.95 critic`. */
  resolve_note?: string | null;
  /** Whether the agent's last message asked the person something. */
  turn: 'asked' | 'answered' | null;
  /** The question, in one line, while `turn` is `asked`. */
  ask: string | null;
  automation_id: string | null;
  parent_conversation_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectConversationsResponse {
  project_id: string;
  coordinator: ProjectConversation | null;
  conversations: ProjectConversation[];
}

/** A coordinator conversation the server starts together with the project. */
export interface NewProjectCoordinator {
  working_dir: string;
  /** Unset, the person's active agent profile. */
  agent_profile_id?: string;
  title?: string;
  /** Reserved by the client so it can find the coordinator after a lost response. */
  conversation_id?: string;
  /** More tags; the server adds `project`. */
  tags?: Record<string, string>;
  /** The following are as in a conversation start request. */
  conversation_runtime?: StartConversationRequest['conversation_runtime'];
  agent_launch_additions?: StartConversationRequest['agent_launch_additions'];
  confirmation_policy?: StartConversationRequest['confirmation_policy'];
  security_analyzer?: StartConversationRequest['security_analyzer'];
  max_iterations?: number;
}

export interface CreateProjectRequest {
  name: string;
  description?: string;
  /** Start a new coordinator. Giving it with `coordinator_conversation_id` is a 422. */
  coordinator?: NewProjectCoordinator;
  coordinator_conversation_id?: string | null;
  worker_agent_profile_id?: string | null;
}

export type UpdateProjectRequest = Partial<Omit<CreateProjectRequest, 'coordinator'>>;

export interface ProjectTranscriptMessage {
  id: string;
  /** `user`, `agent` or `environment`. */
  source: string;
  sender: string | null;
  /** Cut at 4000 characters. */
  text: string;
  /** Whether it is the text of a `finish`. */
  finish: boolean;
  timestamp: string;
}

export interface ProjectTranscriptResponse {
  conversation_id: string;
  messages: ProjectTranscriptMessage[];
}

export interface ProjectArtifact {
  name: string;
  size: number;
  /** `user` for an upload, `agent` for a file a conversation saved. */
  source: 'user' | 'agent';
  conversation_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectArtifactsResponse {
  artifacts: ProjectArtifact[];
}

export interface SaveConversationArtifactRequest {
  /** A member of the project. */
  conversation_id: string;
  /** A file inside that conversation's working directory. */
  path: string;
  /** Name in the project. Unset, the file's own name. */
  name?: string;
}

export interface ProjectMemoryFile {
  name: string;
  size: number;
  /** Pass it back to replace or delete the file. */
  revision: string;
  updated_at: string;
}

export interface ProjectMemoryFileContent extends ProjectMemoryFile {
  content: string;
}

export interface ProjectMemoryResponse {
  /** `MEMORY.md` first, then topic files. */
  files: ProjectMemoryFile[];
}

export interface ProjectDeleteResponse {
  deleted: boolean;
}

/** Ids compare without dashes or case: the `project` tag stores the plain hex form. */
export const projectIdKey = (id: string): string => id.replace(/-/g, '').toLowerCase();

export class ProjectsClient {
  public readonly host: string;
  public readonly apiKey?: string;
  private readonly client: HttpClient;

  constructor(options: ProjectsClientOptions) {
    this.host = options.host.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.client = new HttpClient({
      baseUrl: this.host,
      apiKey: this.apiKey,
      timeout: options.timeout || 60000,
    });
  }

  async listProjects(): Promise<Project[]> {
    const response = await this.client.get<ProjectsListResponse>('/api/projects');
    return response.data.projects ?? [];
  }

  /**
   * Create a project. With `coordinator`, the server also starts its
   * coordinator conversation, idle, and returns the project with
   * `coordinator_conversation_id` set; if it cannot start, no project is kept
   * and the request answers 422.
   */
  async createProject(request: CreateProjectRequest): Promise<Project> {
    const response = await this.client.post<Project>('/api/projects', request);
    return response.data;
  }

  async getProject(projectId: string): Promise<Project> {
    const response = await this.client.get<Project>(projectPath(projectId));
    return response.data;
  }

  async updateProject(projectId: string, request: UpdateProjectRequest): Promise<Project> {
    const response = await this.client.patch<Project>(projectPath(projectId), request);
    return response.data;
  }

  /** Delete a project with its artifacts and memory. Its conversations are kept. */
  async deleteProject(projectId: string): Promise<ProjectDeleteResponse> {
    const response = await this.client.delete<ProjectDeleteResponse>(projectPath(projectId));
    return response.data;
  }

  /** The project's conversations with what each needs from the person. */
  async listConversations(projectId: string): Promise<ProjectConversationsResponse> {
    const response = await this.client.get<ProjectConversationsResponse>(
      projectPath(projectId, '/conversations')
    );
    return {
      project_id: response.data.project_id ?? projectId,
      coordinator: response.data.coordinator ?? null,
      conversations: response.data.conversations ?? [],
    };
  }

  /**
   * Add an existing conversation; it leaves any other project. Idempotent. A
   * conversation that coordinates another project cannot move (409).
   */
  async addConversation(projectId: string, conversationId: string): Promise<void> {
    await this.client.put(conversationPath(projectId, conversationId));
  }

  /** Take a conversation out of the project. The coordinator cannot leave (409). */
  async removeConversation(
    projectId: string,
    conversationId: string
  ): Promise<ProjectDeleteResponse> {
    const response = await this.client.delete<ProjectDeleteResponse>(
      conversationPath(projectId, conversationId)
    );
    return response.data;
  }

  /**
   * Resolve a member conversation for the person (`resolved=user`). Idempotent.
   * Use this rather than a tag PATCH: the server owns the board tags.
   */
  async resolveConversation(projectId: string, conversationId: string): Promise<void> {
    await this.client.post(conversationPath(projectId, conversationId, '/resolve'));
  }

  /** Reopen a resolved member conversation. Idempotent. */
  async reopenConversation(projectId: string, conversationId: string): Promise<void> {
    await this.client.post(conversationPath(projectId, conversationId, '/reopen'));
  }

  /** A member's latest messages and `finish` texts, oldest first. */
  async readConversation(
    projectId: string,
    conversationId: string,
    options: { limit?: number } = {}
  ): Promise<ProjectTranscriptResponse> {
    const response = await this.client.get<ProjectTranscriptResponse>(
      conversationPath(projectId, conversationId, '/messages'),
      options.limit === undefined ? undefined : { params: { limit: options.limit } }
    );
    return response.data;
  }

  async listArtifacts(projectId: string): Promise<ProjectArtifact[]> {
    const response = await this.client.get<ProjectArtifactsResponse>(artifactPath(projectId));
    return response.data.artifacts ?? [];
  }

  /** Upload a file to the project. Too large a file is a 413. */
  async uploadArtifact(
    projectId: string,
    file: Blob,
    options: { name?: string } = {}
  ): Promise<ProjectArtifact> {
    const body = new FormData();
    const fileName = options.name ?? (file instanceof File ? file.name : 'upload');
    body.append('file', file, fileName);
    if (options.name !== undefined) body.append('name', options.name);
    const response = await this.client.post<ProjectArtifact>(artifactPath(projectId), body);
    return response.data;
  }

  /** Copy a file from a member conversation's workspace into the project. */
  async saveConversationArtifact(
    projectId: string,
    request: SaveConversationArtifactRequest
  ): Promise<ProjectArtifact> {
    const response = await this.client.post<ProjectArtifact>(
      projectPath(projectId, '/artifacts/from-conversation'),
      request
    );
    return response.data;
  }

  /** Download an artifact. The server always sends it as an attachment. */
  async downloadArtifact(projectId: string, name: string): Promise<Blob> {
    const response = await this.client.get<Blob>(artifactPath(projectId, name), {
      responseType: 'blob',
    });
    return response.data;
  }

  /** An artifact's contents as text, for a preview. */
  async readArtifactText(projectId: string, name: string): Promise<string> {
    const response = await this.client.get<string>(artifactPath(projectId, name), {
      responseType: 'text',
    });
    return response.data;
  }

  async deleteArtifact(projectId: string, name: string): Promise<ProjectDeleteResponse> {
    const response = await this.client.delete<ProjectDeleteResponse>(artifactPath(projectId, name));
    return response.data;
  }

  async listMemory(projectId: string): Promise<ProjectMemoryFile[]> {
    const response = await this.client.get<ProjectMemoryResponse>(memoryPath(projectId));
    return response.data.files ?? [];
  }

  async readMemory(projectId: string, name: string): Promise<ProjectMemoryFileContent> {
    const response = await this.client.get<ProjectMemoryFileContent>(memoryPath(projectId, name));
    return response.data;
  }

  /**
   * Write a whole memory file. Pass the `revision` you read as `baseRevision`;
   * leave it out to create a new file. A stale revision answers 409 with the
   * current file to merge.
   */
  async writeMemory(
    projectId: string,
    name: string,
    content: string,
    options: { baseRevision?: string | null } = {}
  ): Promise<ProjectMemoryFileContent> {
    const response = await this.client.put<ProjectMemoryFileContent>(memoryPath(projectId, name), {
      content,
      base_revision: options.baseRevision ?? null,
    });
    return response.data;
  }

  async deleteMemory(
    projectId: string,
    name: string,
    options: { baseRevision?: string } = {}
  ): Promise<ProjectDeleteResponse> {
    const response = await this.client.delete<ProjectDeleteResponse>(
      memoryPath(projectId, name),
      options.baseRevision === undefined
        ? undefined
        : { params: { base_revision: options.baseRevision } }
    );
    return response.data;
  }

  close(): void {
    this.client.close();
  }
}

function projectPath(projectId: string, rest = ''): string {
  return `/api/projects/${encodeURIComponent(projectId)}${rest}`;
}

function conversationPath(projectId: string, conversationId: string, rest = ''): string {
  return projectPath(projectId, `/conversations/${encodeURIComponent(conversationId)}${rest}`);
}

function artifactPath(projectId: string, name?: string): string {
  return projectPath(
    projectId,
    `/artifacts${name === undefined ? '' : `/${encodeURIComponent(name)}`}`
  );
}

function memoryPath(projectId: string, name?: string): string {
  return projectPath(
    projectId,
    `/memory${name === undefined ? '' : `/${encodeURIComponent(name)}`}`
  );
}
