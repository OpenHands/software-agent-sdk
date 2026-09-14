import { HttpClient, HttpError } from './http-client';
import type { HttpResponse, RequestOptions } from './http-client';
import { clearAgentServerInfoCache, getCachedAgentServerInfo } from './agent-server-compatibility';

export interface RuntimeServiceOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
  conversationId?: string;
}

/** Internal transport for operations on one conversation's workspace. */
class RuntimeTransport extends HttpClient {
  constructor(
    options: RuntimeServiceOptions,
    private readonly server: HttpClient,
    private readonly conversationId: string
  ) {
    super({ baseUrl: options.host });
    if (!conversationId.trim()) throw new Error('A runtime requires a conversation ID');
  }

  override async request<T = HttpResponse['data']>(
    options: RequestOptions
  ): Promise<HttpResponse<T>> {
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
      throw new Error('A runtime conversation cannot be overridden');
    }
    const info = await getCachedAgentServerInfo(this.server).catch((error: unknown) => {
      if (error instanceof HttpError && error.status === 404) return null;
      clearAgentServerInfoCache(this.server);
      throw error;
    });
    const request = info?.capabilities?.includes('conversation_runtime_routes_v1')
      ? {
          ...options,
          url: `/api/conversations/${encodeURIComponent(this.conversationId)}${options.url.slice(4)}`,
        }
      : { ...options, params: { ...options.params, cid: this.conversationId } };
    return this.server.request<T>(request);
  }
}

export function runtimeServiceConnections(options: RuntimeServiceOptions): {
  server: HttpClient;
  runtime: HttpClient;
} {
  const server = new HttpClient({
    baseUrl: options.host,
    apiKey: options.apiKey,
    timeout: options.timeout,
  });
  return {
    server,
    runtime:
      options.conversationId === undefined
        ? server
        : new RuntimeTransport(options, server, options.conversationId),
  };
}
