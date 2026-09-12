import { BashClient } from './bash-client';
import { DesktopClient } from './desktop-client';
import { FileClient } from './file-client';
import { GitClient } from './git-client';
import { MCPClient } from './mcp-client';
import { VSCodeClient } from './vscode-client';
import { ServerConnection } from './server-connection';
import type { ServerConnectionOptions } from './server-connection';
import { RuntimeTransport } from './runtime-transport';

export interface RuntimeClientOptions extends ServerConnectionOptions {
  conversationId: string;
  connection?: ServerConnection;
}

/** Services belonging to one immutable conversation runtime. */
export class RuntimeClient {
  readonly conversationId: string;
  readonly connection: ServerConnection;
  readonly transport: RuntimeTransport;
  readonly files: Pick<
    FileClient,
    'downloadFile' | 'downloadTextFile' | 'uploadFile' | 'uploadTextFile' | 'downloadTrajectory'
  >;
  readonly bash: BashClient;
  readonly git: GitClient;
  readonly desktop: DesktopClient;
  readonly vscode: VSCodeClient;
  readonly mcp: Pick<MCPClient, 'testServer'>;

  constructor(options: RuntimeClientOptions) {
    this.conversationId = options.conversationId;
    this.connection = options.connection ?? new ServerConnection(options);
    this.transport = new RuntimeTransport(this.connection, this.conversationId);
    const services = {
      host: this.connection.host,
      apiKey: this.connection.sessionApiKey,
      runtimeTransport: this.transport,
    };
    this.files = new FileClient(services);
    this.bash = new BashClient(services);
    this.git = new GitClient(services);
    this.desktop = new DesktopClient(services);
    this.vscode = new VSCodeClient(services);
    this.mcp = new MCPClient(services);
  }

  /** URL construction never includes the session API key. */
  url(path: string, params?: Record<string, unknown>): Promise<string> {
    return this.transport.url(path, params);
  }

  async startWorkspaceSession(): Promise<string> {
    await this.connection.post('/api/auth/workspace-session', undefined, {
      credentials: 'include',
    });
    return `${this.connection.host}/api/conversations/${encodeURIComponent(this.conversationId)}/workspace/`;
  }
}
