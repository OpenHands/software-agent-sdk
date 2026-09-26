import type { MCPClient } from '../client/mcp-client';
import type { SettingsClient } from '../client/settings-client';
import type {
  McpConfigPatchWritable,
  McpServerPatchWritable,
} from '../generated/agent-server-schema';
import type {
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
});
