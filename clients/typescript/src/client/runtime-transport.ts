import { HttpClient, HttpError } from './http-client';
import type { HttpResponse, RequestOptions } from './http-client';
import { clearAgentServerInfoCache, getCachedAgentServerInfo } from './agent-server-compatibility';

export interface RuntimeServiceClientOptions {
  host: string;
  apiKey?: string;
  timeout?: number;
  /** Owning conversation. Omit for host-level use before a conversation exists. */
  conversationId?: string;
}

function assertWorkspaceApiPath(options: RequestOptions): void {
  if (
    !options.url.startsWith('/api/') ||
    options.url.includes('?') ||
    options.url.includes('#') ||
    options.url.includes('\\') ||
    options.url.split('/').some((segment) => ['.', '..'].includes(decodeURIComponent(segment)))
  ) {
    throw new Error('Workspace requests require an API path and separate query parameters');
  }
}

/** Internal transport for operations on one conversation's workspace. */
class ConversationScopedHttpClient extends HttpClient {
  constructor(
    options: RuntimeServiceClientOptions,
    private readonly serverClient: HttpClient,
    private readonly conversationId: string
  ) {
    super({ baseUrl: options.host });
    if (!conversationId.trim()) throw new Error('A runtime requires a conversation ID');
  }

  override async request<T = HttpResponse['data']>(
    options: RequestOptions
  ): Promise<HttpResponse<T>> {
    assertWorkspaceApiPath(options);
    if (options.params?.cid != null) {
      throw new Error('A runtime conversation cannot be overridden');
    }
    const serverInfo = await getCachedAgentServerInfo(this.serverClient).catch((error: unknown) => {
      if (error instanceof HttpError && error.status === 404) return null;
      clearAgentServerInfoCache(this.serverClient);
      throw error;
    });
    const scopedRequest = serverInfo?.capabilities?.includes('conversation_runtime_routes_v1')
      ? {
          ...options,
          url: `/api/conversations/${encodeURIComponent(this.conversationId)}${options.url.slice(4)}`,
        }
      : { ...options, params: { ...options.params, cid: this.conversationId } };
    return this.serverClient.request<T>(scopedRequest);
  }
}

/** Transport for the server's trusted host workspace. */
class HostWorkspaceHttpClient extends HttpClient {
  constructor(
    options: RuntimeServiceClientOptions,
    private readonly serverClient: HttpClient
  ) {
    super({ baseUrl: options.host });
  }

  override async request<T = HttpResponse['data']>(
    options: RequestOptions
  ): Promise<HttpResponse<T>> {
    assertWorkspaceApiPath(options);
    return this.serverClient.request<T>({
      ...options,
      url: `/api/host/${options.url.slice(5)}`,
    });
  }
}

export function createRuntimeHttpClients(options: RuntimeServiceClientOptions): {
  serverClient: HttpClient;
  runtimeClient: HttpClient;
} {
  const serverClient = new HttpClient({
    baseUrl: options.host,
    apiKey: options.apiKey,
    timeout: options.timeout,
  });
  return {
    serverClient,
    runtimeClient:
      options.conversationId === undefined
        ? new HostWorkspaceHttpClient(options, serverClient)
        : new ConversationScopedHttpClient(options, serverClient, options.conversationId),
  };
}
