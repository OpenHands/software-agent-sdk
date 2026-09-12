/**
 * Canonical persisted MCP settings types.
 *
 * The generated Agent Server component remains the field-level source of
 * truth. These aliases add the stable transport discrimination and sparse
 * RFC 7386 mutation semantics expected by client callers.
 */
import type {
  McpConfigPatchWritable,
  McpServerInputWritable,
  McpServerPatchWritable,
  McpoAuthAuthenticationInputWritable,
  McpoAuthStateInputWritable,
} from '../generated/agent-server-schema';

type GeneratedMCPServer = McpServerInputWritable;
type GeneratedMCPTransport = NonNullable<GeneratedMCPServer['transport']>;
type GeneratedMCPAuthCredential = NonNullable<GeneratedMCPServer['auth']>;

type MCPDisplayFields = Pick<GeneratedMCPServer, 'description' | 'icon' | 'timeout'>;
type MCPStdioFields = Pick<GeneratedMCPServer, 'args' | 'env' | 'cwd'>;
type MCPRemoteFields = Pick<
  GeneratedMCPServer,
  'headers' | 'auth' | 'sse_read_timeout' | 'keep_alive'
>;

export type MCPTransport = GeneratedMCPTransport;
export type RemoteMCPTransport = Exclude<MCPTransport, 'stdio'>;
export type MCPAuthCredential = GeneratedMCPAuthCredential;
export type MCPOAuthAuthentication = McpoAuthAuthenticationInputWritable;
export type MCPOAuthState = McpoAuthStateInputWritable;

export type StdioMCPServer = MCPDisplayFields &
  MCPStdioFields & {
    transport: 'stdio';
    command: string;
    url?: never;
  };

export type RemoteMCPServer = MCPDisplayFields &
  MCPRemoteFields & {
    transport: RemoteMCPTransport;
    url: string;
    command?: never;
  };

export type MCPServer = StdioMCPServer | RemoteMCPServer;
export type MCPConfig = Record<string, MCPServer>;
export type MCPServerPatch = McpServerPatchWritable;
export type MCPConfigPatch = McpConfigPatchWritable;
