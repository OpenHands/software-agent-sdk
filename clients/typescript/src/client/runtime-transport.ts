import { HttpClient } from './http-client';
import type { HttpResponse, RequestOptions } from './http-client';
import { ServerConnection } from './server-connection';
import type { ServerConnectionOptions } from './server-connection';

export interface RuntimeServiceOptions extends ServerConnectionOptions {
  conversationId?: string;
  connection?: ServerConnection;
  runtimeTransport?: RuntimeTransport;
}

/** Routes only runtime requests. Global operations must use the server connection. */
export class RuntimeTransport extends HttpClient {
  constructor(
    readonly connection: ServerConnection,
    readonly conversationId: string
  ) {
    super({ baseUrl: connection.host, apiKey: connection.sessionApiKey });
    if (!conversationId.trim()) throw new Error('A runtime requires a conversation ID');
  }

  private async scope(options: RequestOptions): Promise<RequestOptions> {
    if (
      !options.url.startsWith('/api/') ||
      options.url.includes('?') ||
      options.url.includes('#') ||
      options.url.includes('\\') ||
      options.url.split('/').some((segment) => ['.', '..'].includes(decodeURIComponent(segment)))
    ) {
      throw new Error('Runtime requests require an API path and separate query parameters');
    }
    if (options.params?.cid != null) {
      throw new Error('A runtime conversation cannot be overridden; select another RuntimeClient');
    }
    if (await this.connection.supportsRuntimeRoutes()) {
      return {
        ...options,
        url: `/api/conversations/${encodeURIComponent(this.conversationId)}${options.url.slice(4)}`,
      };
    }
    return { ...options, params: { ...options.params, cid: this.conversationId } };
  }

  override async request<T = unknown>(options: RequestOptions): Promise<HttpResponse<T>> {
    return this.connection.request<T>(await this.scope(options));
  }

  async url(path: string, params?: Record<string, unknown>): Promise<string> {
    const scoped = await this.scope({ method: 'GET', url: path, params });
    return this.buildUrl(scoped.url, scoped.params).toString();
  }
}

export function runtimeServiceConnections(options: RuntimeServiceOptions): {
  server: ServerConnection;
  runtime: HttpClient;
} {
  const server =
    options.runtimeTransport?.connection ?? options.connection ?? new ServerConnection(options);
  if (
    options.runtimeTransport &&
    options.conversationId !== undefined &&
    options.conversationId !== options.runtimeTransport.conversationId
  ) {
    throw new Error('Conflicting runtime conversation IDs');
  }
  const runtime =
    options.runtimeTransport ??
    (options.conversationId !== undefined
      ? new RuntimeTransport(server, options.conversationId)
      : server);
  return { server, runtime };
}
