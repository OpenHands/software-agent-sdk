import { HttpClient, HttpError } from './http-client';
import { clearAgentServerInfoCache, getCachedAgentServerInfo } from './agent-server-compatibility';

export interface ServerConnectionOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
}

/** One authenticated server connection, shared by its runtime clients. */
export class ServerConnection extends HttpClient {
  readonly host: string;
  readonly sessionApiKey?: string;
  private runtimeRoutes?: Promise<boolean>;

  constructor(options: ServerConnectionOptions) {
    super({ baseUrl: options.host, apiKey: options.apiKey, timeout: options.timeout });
    this.host = options.host.replace(/\/$/, '');
    this.sessionApiKey = options.apiKey;
  }

  supportsRuntimeRoutes(): Promise<boolean> {
    this.runtimeRoutes ??= getCachedAgentServerInfo(this)
      .then((info) => info.capabilities?.includes('conversation_runtime_routes_v1') ?? false)
      .catch((error: unknown) => {
        if (error instanceof HttpError && error.status === 404) return false;
        this.runtimeRoutes = undefined;
        clearAgentServerInfoCache(this);
        throw error;
      });
    return this.runtimeRoutes;
  }
}
