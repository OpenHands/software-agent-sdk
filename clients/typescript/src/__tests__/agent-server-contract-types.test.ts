import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import type { MCPClient } from '../client/mcp-client';
import type { ConversationClient } from '../client/conversation-client';
import type { FileClient } from '../client/file-client';
import type { GitClient } from '../client/git-client';
import type { InitClient } from '../client/init-client';
import type { PluginsClient } from '../client/plugins-client';
import type { RemoteWorkspace } from '../workspace/remote-workspace';
import type {
  GitChange as PublicGitChange,
  GitChangeStatus as PublicGitChangeStatus,
} from '../index';
import type { SettingsClient } from '../client/settings-client';
import type { ConversationInfo } from '../models/conversation';
import type { ACPEffortOption } from '../models/acp-model-discovery';
import type {
  AcpAgentProfile as GeneratedACPAgentProfile,
  AgentProfileDiagnostics as GeneratedAgentProfileDiagnostics,
  AgentProfileInfo as GeneratedAgentProfileSummary,
  GitChange as GeneratedGitChange,
  GitChangeStatus as GeneratedGitChangeStatus,
  McpConfigPatchWritable,
  McpServerPatchWritable,
  OpenHandsAgentProfile as GeneratedOpenHandsAgentProfile,
} from '../generated/agent-server-schema';
import type {
  ACPAgentProfile,
  AgentProfileDiagnostics,
  AgentProfileSaveInput,
  AgentProfileSummary,
  OpenHandsAgentProfile,
} from '../models/agent-profile';
import type {
  AgentServerArchiveQuery,
  AgentServerBatchEventIds,
  AgentServerBatchEventsResponse,
  AgentServerGitCommitChangesResponse,
  AgentServerGitCommitsResponse,
  AgentServerInitRequest,
  AgentServerInitResponse,
  AgentServerInitStatus,
  AgentServerLoadPluginRequest,
  AgentServerLoadPluginResponse,
  AgentServerMCPOAuthCallbackRequest,
  AgentServerMCPOAuthCallbackResponse,
  AgentServerMCPOAuthStatusResponse,
  AgentServerMCPStartOAuthRequest,
  AgentServerMCPStartOAuthResponse,
  AgentServerMCPTestRequest,
  AgentServerMCPTestResponse,
  AgentServerSettingsPatchRequest,
  AgentServerSettingsPatchResponse,
  AgentServerSettingsResponse,
  AgentServerSettingsSchema,
} from '../models/agent-server-api';
import type {
  BatchGetConversationEventsApiConversationsConversationIdEventsGetData,
  BatchGetConversationEventsApiConversationsConversationIdEventsGetResponse,
  GitCommitChangesQueryApiGitCommitsShaChangesGetResponse,
  GitCommitsQueryApiGitCommitsGetResponse,
  InitStatus as GeneratedInitStatus,
} from '../generated/agent-server-schema';
import type {
  MCPAuthCredential,
  MCPConfig,
  MCPConfigPatch,
  MCPServer,
  MCPServerPatch,
} from '../models/mcp-settings';

type Equal<Left, Right> =
  (<Value>() => Value extends Left ? 1 : 2) extends <Value>() => Value extends Right ? 1 : 2
    ? true
    : false;

type IsAny<Value> = 0 extends 1 & Value ? true : false;
type IsBareUnknown<Value> =
  IsAny<Value> extends true
    ? false
    : unknown extends Value
      ? [keyof Value] extends [never]
        ? true
        : false
      : false;

type GeneratedMCPClientContract = {
  testServer(request: AgentServerMCPTestRequest): Promise<AgentServerMCPTestResponse>;
  startOAuth(request: AgentServerMCPStartOAuthRequest): Promise<AgentServerMCPStartOAuthResponse>;
};

function assertType<Type extends true>(_value: Type): void {
  // Compiling this call is the assertion.
}

describe('Agent Server generated contract aliases', () => {
  it('typechecks the contract assertions with TypeScript', () => {
    const root = fileURLToPath(new URL('../../', import.meta.url));
    const configPath = fileURLToPath(new URL('../../tsconfig.json', import.meta.url));
    const config = ts.readConfigFile(configPath, ts.sys.readFile);
    const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
    const program = ts.createProgram([fileURLToPath(import.meta.url)], {
      ...parsed.options,
      noEmit: true,
    });
    const diagnostics = [
      ...(config.error ? [config.error] : []),
      ...parsed.errors,
      ...ts.getPreEmitDiagnostics(program),
    ];
    expect(
      ts.formatDiagnostics(diagnostics, {
        getCanonicalFileName: (fileName) => fileName,
        getCurrentDirectory: ts.sys.getCurrentDirectory,
        getNewLine: () => ts.sys.newLine,
      })
    ).toBe('');
  }, 30_000);

  it('exposes typed conversation effort fields', () => {
    assertType<Equal<ConversationInfo['current_effort'], string | null | undefined>>(true);
    assertType<Equal<ConversationInfo['available_efforts'], ACPEffortOption[] | undefined>>(true);
  });

  it('statically checks settings client request and response types', () => {
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['getAgentSchema']>>, AgentServerSettingsSchema>
    >(true);
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['getSettings']>>, AgentServerSettingsResponse>
    >(true);
    assertType<
      Equal<Parameters<SettingsClient['updateSettings']>[0], AgentServerSettingsPatchRequest>
    >(true);
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['updateSettings']>>, AgentServerSettingsPatchResponse>
    >(true);
    assertType<Equal<Parameters<SettingsClient['createMcpServer']>[1], MCPServer>>(true);
    assertType<Equal<Parameters<SettingsClient['patchMcpServer']>[1], MCPServerPatch>>(true);
    assertType<
      Equal<
        Awaited<ReturnType<SettingsClient['createMcpServer']>>,
        AgentServerSettingsPatchResponse
      >
    >(true);
    assertType<
      Equal<Awaited<ReturnType<SettingsClient['patchMcpServer']>>, AgentServerSettingsPatchResponse>
    >(true);
    assertType<
      Equal<
        Awaited<ReturnType<SettingsClient['deleteMcpServer']>>,
        AgentServerSettingsPatchResponse
      >
    >(true);
  });

  it('keeps important MCP contract exports stronger than any or bare unknown', () => {
    assertType<Equal<IsAny<MCPServer>, false>>(true);
    assertType<Equal<IsBareUnknown<MCPServer>, false>>(true);
    assertType<Equal<IsAny<MCPConfig>, false>>(true);
    assertType<Equal<IsBareUnknown<MCPConfig>, false>>(true);
    assertType<Equal<IsAny<MCPAuthCredential>, false>>(true);
    assertType<Equal<IsBareUnknown<MCPAuthCredential>, false>>(true);
    assertType<Equal<IsAny<MCPServerPatch>, false>>(true);
    assertType<Equal<IsBareUnknown<MCPServerPatch>, false>>(true);
    assertType<Equal<IsAny<MCPConfigPatch>, false>>(true);
    assertType<Equal<IsBareUnknown<MCPConfigPatch>, false>>(true);
  });

  it('keeps MCP client methods checked against generated operations', () => {
    assertType<MCPClient extends GeneratedMCPClientContract ? true : false>(true);
    assertType<
      Equal<Awaited<ReturnType<MCPClient['getOAuthStatus']>>, AgentServerMCPOAuthStatusResponse>
    >(true);
    assertType<
      Equal<Parameters<MCPClient['submitOAuthCallback']>[1], AgentServerMCPOAuthCallbackRequest>
    >(true);
    assertType<
      Equal<
        Awaited<ReturnType<MCPClient['submitOAuthCallback']>>,
        AgentServerMCPOAuthCallbackResponse
      >
    >(true);
  });

  it('statically checks the seven uncovered operations against generated types', () => {
    assertType<
      Equal<
        AgentServerBatchEventIds,
        BatchGetConversationEventsApiConversationsConversationIdEventsGetData['body']
      >
    >(true);
    assertType<
      Equal<
        AgentServerBatchEventsResponse,
        BatchGetConversationEventsApiConversationsConversationIdEventsGetResponse
      >
    >(true);
    assertType<
      Equal<
        Awaited<ReturnType<ConversationClient['batchGetEvents']>>,
        AgentServerBatchEventsResponse
      >
    >(true);
    assertType<Equal<Parameters<ConversationClient['batchGetEvents']>[1], string[]>>(true);
    assertType<Equal<AgentServerGitCommitsResponse, GitCommitsQueryApiGitCommitsGetResponse>>(true);
    assertType<
      Equal<
        AgentServerGitCommitChangesResponse,
        GitCommitChangesQueryApiGitCommitsShaChangesGetResponse
      >
    >(true);
    assertType<Equal<Awaited<ReturnType<GitClient['getCommits']>>, AgentServerGitCommitsResponse>>(
      true
    );
    assertType<
      Equal<Awaited<ReturnType<GitClient['getCommitChanges']>>, AgentServerGitCommitChangesResponse>
    >(true);
    assertType<Equal<Awaited<ReturnType<FileClient['downloadArchive']>>, Blob>>(true);
    assertType<Equal<Awaited<ReturnType<FileClient['downloadArchiveBuffer']>>, ArrayBuffer>>(true);
    assertType<Equal<AgentServerArchiveQuery['path'], string>>(true);
    assertType<Equal<AgentServerInitStatus, GeneratedInitStatus>>(true);
    assertType<Equal<Awaited<ReturnType<InitClient['getStatus']>>, AgentServerInitStatus>>(true);
    assertType<Equal<Parameters<InitClient['initialize']>[0], AgentServerInitRequest>>(true);
    assertType<Equal<AgentServerInitRequest['session_api_keys'], string[] | null | undefined>>(
      true
    );
    assertType<Equal<Awaited<ReturnType<InitClient['initialize']>>, AgentServerInitResponse>>(true);
    assertType<Equal<AgentServerLoadPluginRequest, { plugin_ref: string }>>(true);
    assertType<
      Equal<Awaited<ReturnType<ConversationClient['loadPlugin']>>, AgentServerLoadPluginResponse>
    >(true);
    assertType<
      Equal<
        Awaited<ReturnType<PluginsClient['loadPluginForConversation']>>,
        AgentServerLoadPluginResponse
      >
    >(true);
  });

  it('types git change statuses with the generated wire enum', () => {
    assertType<Equal<PublicGitChange['status'], GeneratedGitChange['status']>>(true);
    assertType<Equal<PublicGitChangeStatus, GeneratedGitChangeStatus>>(true);
    assertType<
      Equal<
        Awaited<ReturnType<RemoteWorkspace['gitChanges']>>[number]['status'],
        GeneratedGitChangeStatus
      >
    >(true);

    const statuses = ['ADDED', 'UPDATED', 'DELETED', 'MOVED'] satisfies PublicGitChange['status'][];
    expect(statuses).toHaveLength(4);
  });

  it('distinguishes omitted patch fields from explicit clearing', () => {
    const omitted = {} satisfies MCPServerPatch;
    const cleared = { auth: null } satisfies MCPServerPatch;

    expect('auth' in omitted).toBe(false);
    expect(cleared.auth).toBeNull();
  });

  it('derives every MCP patch field from the generated server contract', () => {
    assertType<Equal<MCPServerPatch, McpServerPatchWritable>>(true);
    assertType<Equal<MCPConfigPatch, McpConfigPatchWritable>>(true);
    assertType<Equal<MCPServerPatch['env'], Record<string, string | null> | null | undefined>>(
      true
    );
    assertType<Equal<MCPServerPatch['headers'], Record<string, string | null> | null | undefined>>(
      true
    );
  });

  it('declares every agent-profile payload field the server sends, and no others', () => {
    assertType<Equal<keyof AgentProfileSummary, keyof GeneratedAgentProfileSummary>>(true);
    assertType<Equal<keyof OpenHandsAgentProfile, keyof GeneratedOpenHandsAgentProfile>>(true);
    assertType<Equal<keyof ACPAgentProfile, keyof GeneratedACPAgentProfile>>(true);
    assertType<Equal<keyof AgentProfileDiagnostics, keyof GeneratedAgentProfileDiagnostics>>(true);
  });

  it('preserves optional nullable ACP effort in profiles and save inputs', () => {
    assertType<
      Equal<Pick<ACPAgentProfile, 'acp_effort'>, Pick<GeneratedACPAgentProfile, 'acp_effort'>>
    >(true);
    assertType<
      Equal<
        Pick<Extract<AgentProfileSaveInput, { agent_kind: 'acp' }>, 'acp_effort'>,
        Pick<GeneratedACPAgentProfile, 'acp_effort'>
      >
    >(true);
  });

  it('preserves optional nullable public-skill selection in profiles and diagnostics', () => {
    assertType<
      Equal<
        Pick<OpenHandsAgentProfile, 'public_skills'>,
        Pick<GeneratedOpenHandsAgentProfile, 'public_skills'>
      >
    >(true);
    assertType<
      Equal<
        Pick<AgentProfileDiagnostics, 'public_skills'>,
        Pick<GeneratedAgentProfileDiagnostics, 'public_skills'>
      >
    >(true);
    assertType<
      Equal<
        Pick<Extract<AgentProfileSaveInput, { agent_kind: 'openhands' }>, 'public_skills'>,
        Pick<GeneratedOpenHandsAgentProfile, 'public_skills'>
      >
    >(true);
  });
});
