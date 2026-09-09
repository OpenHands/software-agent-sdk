/**
 * HTTP client for OpenHands Agent Server API
 */

export interface HttpClientOptions {
  baseUrl: string;
  apiKey?: string;
  timeout?: number;
}

export type ResponseType = 'auto' | 'json' | 'text' | 'blob' | 'arrayBuffer';

export interface RequestOptions {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  url: string;
  params?: Record<string, unknown>;
  data?: unknown;
  headers?: Record<string, string>;
  timeout?: number;
  acceptableStatusCodes?: Set<number>;
  responseType?: ResponseType;
  /**
   * Credentials mode for `fetch`. Use `'include'` for endpoints that issue or
   * consume cookies (e.g. the workspace-session cookie minted by
   * `POST /api/auth/workspace-session`) so the browser persists them across
   * origins.
   */
  credentials?: RequestCredentials;
}

export interface HttpResponse<T = unknown> {
  data: T;
  status: number;
  statusText: string;
  headers: Record<string, string>;
}

export class HttpError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public response?: unknown,
    message?: string
  ) {
    super(message || `HTTP ${status}: ${statusText}`);
    this.name = 'HttpError';
  }
}

export class HttpClient {
  private baseUrl: string;
  private apiKey?: string;
  private timeout: number;

  constructor(options: HttpClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.apiKey = options.apiKey;
    this.timeout = options.timeout || 60000;
  }

  private buildUrl(path: string, params?: Record<string, unknown>): URL {
    const relativePath = path.startsWith('/') ? path.slice(1) : path;
    const url = new URL(relativePath, this.baseUrl + '/');

    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          if (Array.isArray(value)) {
            value.forEach((item) => url.searchParams.append(key, String(item)));
          } else {
            url.searchParams.append(key, String(value));
          }
        }
      });
    }

    return url;
  }

  async request<T = unknown>(options: RequestOptions): Promise<HttpResponse<T>> {
    // `fetch` (and browsers) reject a body on a GET request, but a few
    // agent-server batch endpoints (e.g. `GET /api/bash/bash_events/` and
    // `GET /api/conversations/{id}/events`) are declared as GET-with-required-body.
    // Route those through Node's http(s) module, which permits a GET body, and
    // leave the fetch path untouched for every other request.
    if (options.method === 'GET' && options.data !== undefined && options.data !== null) {
      return this.requestWithBodyOnGet<T>(options);
    }

    const url = this.buildUrl(options.url, options.params);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    if (this.apiKey) {
      headers['X-Session-API-Key'] = this.apiKey;
    }

    const requestInit: RequestInit = {
      method: options.method,
      headers,
      signal: AbortSignal.timeout(options.timeout || this.timeout),
    };

    if (options.credentials) {
      requestInit.credentials = options.credentials;
    }

    if (options.data && options.method !== 'GET') {
      if (options.data instanceof FormData) {
        delete headers['Content-Type'];
        requestInit.body = options.data;
      } else {
        requestInit.body = JSON.stringify(options.data);
      }
    }

    try {
      const response = await fetch(url.toString(), requestInit);

      const isAcceptable =
        options.acceptableStatusCodes?.has(response.status) ||
        (!options.acceptableStatusCodes && response.ok);

      if (!isAcceptable) {
        let errorContent: unknown;
        try {
          const contentType = response.headers.get('content-type');
          if (contentType?.includes('application/json')) {
            errorContent = await response.json();
          } else {
            errorContent = await response.text();
          }
        } catch {
          errorContent = null;
        }

        throw new HttpError(
          response.status,
          response.statusText,
          errorContent,
          `HTTP request failed (${response.status} ${response.statusText}): ${JSON.stringify(errorContent)}`
        );
      }

      const data = (await this.parseResponse<T>(response, options.responseType || 'auto')) as T;

      const responseHeaders: Record<string, string> = {};
      response.headers.forEach((value, key) => {
        responseHeaders[key] = value;
      });

      return {
        data,
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      };
    } catch (error) {
      if (error instanceof HttpError) {
        throw error;
      }

      if (error instanceof Error) {
        if (error.name === 'AbortError') {
          throw new Error(`Request timeout after ${options.timeout || this.timeout}ms`, {
            cause: error,
          });
        }
        throw new Error(`Request failed: ${error.message}`, { cause: error });
      }

      throw new Error('Unknown request error', { cause: error });
    }
  }

  /**
   * Issue a GET request that carries a JSON body via Node's `http`/`https`
   * module. `fetch` throws `TypeError: Request with GET/HEAD method cannot have
   * body`, so this is the only way to call the agent-server's GET-with-body
   * batch endpoints. Mirrors the URL/header/error/parse behavior of the fetch
   * path in {@link request}. Node-only by nature (browsers forbid GET bodies).
   */
  private async requestWithBodyOnGet<T = unknown>(
    options: RequestOptions
  ): Promise<HttpResponse<T>> {
    const url = this.buildUrl(options.url, options.params);

    const body = typeof options.data === 'string' ? options.data : JSON.stringify(options.data);

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...options.headers,
      'Content-Length': String(Buffer.byteLength(body)),
    };

    if (this.apiKey) {
      headers['X-Session-API-Key'] = this.apiKey;
    }

    const transport =
      url.protocol === 'https:' ? await import('node:https') : await import('node:http');
    const timeoutMs = options.timeout || this.timeout;

    return new Promise<HttpResponse<T>>((resolve, reject) => {
      const req = transport.request(url, { method: options.method, headers }, (res) => {
        const chunks: Buffer[] = [];
        res.on('data', (chunk: Buffer) => chunks.push(chunk));
        res.on('end', () => {
          try {
            const status = res.statusCode ?? 0;
            const statusText = res.statusMessage ?? '';
            const responseHeaders: Record<string, string> = {};
            for (const [key, value] of Object.entries(res.headers)) {
              if (value !== undefined) {
                responseHeaders[key] = Array.isArray(value) ? value.join(', ') : value;
              }
            }

            const buffer = Buffer.concat(chunks);
            const contentType = responseHeaders['content-type'];

            const isAcceptable =
              options.acceptableStatusCodes?.has(status) ||
              (!options.acceptableStatusCodes && status >= 200 && status < 300);

            if (!isAcceptable) {
              const text = buffer.toString('utf-8');
              let errorContent: unknown;
              try {
                errorContent = contentType?.includes('application/json') ? JSON.parse(text) : text;
              } catch {
                errorContent = text || null;
              }

              reject(
                new HttpError(
                  status,
                  statusText,
                  errorContent,
                  `HTTP request failed (${status} ${statusText}): ${JSON.stringify(errorContent)}`
                )
              );
              return;
            }

            const data = this.parseNodeResponse<T>(
              buffer,
              contentType,
              options.responseType || 'auto'
            );

            resolve({ data, status, statusText, headers: responseHeaders });
          } catch (error) {
            reject(error);
          }
        });
      });

      req.on('error', (error: Error) => {
        reject(new Error(`Request failed: ${error.message}`, { cause: error }));
      });

      req.setTimeout(timeoutMs, () => {
        req.destroy(new Error(`Request timeout after ${timeoutMs}ms`));
      });

      req.end(body);
    });
  }

  private parseNodeResponse<T>(
    buffer: Buffer,
    contentType: string | undefined,
    responseType: ResponseType
  ): T {
    if (responseType === 'blob') {
      return new Blob([new Uint8Array(buffer)]) as T;
    }

    if (responseType === 'arrayBuffer') {
      return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength) as T;
    }

    const text = buffer.toString('utf-8');

    if (responseType === 'text') {
      return text as T;
    }

    if (responseType === 'json') {
      return (text ? JSON.parse(text) : undefined) as T;
    }

    if (contentType?.includes('application/json')) {
      return (text ? JSON.parse(text) : undefined) as T;
    }

    return text as T;
  }

  private async parseResponse<T>(response: Response, responseType: ResponseType): Promise<T> {
    if (responseType === 'json') {
      return (await response.json()) as T;
    }

    if (responseType === 'text') {
      return (await response.text()) as T;
    }

    if (responseType === 'blob') {
      return (await response.blob()) as T;
    }

    if (responseType === 'arrayBuffer') {
      return (await response.arrayBuffer()) as T;
    }

    const contentType = response.headers.get('content-type');
    if (contentType?.includes('application/json')) {
      return (await response.json()) as T;
    }
    return (await response.text()) as T;
  }

  async get<T = unknown>(
    url: string,
    options?: Omit<RequestOptions, 'method' | 'url'>
  ): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'GET', url, ...options });
  }

  async post<T = unknown>(
    url: string,
    data?: unknown,
    options?: Omit<RequestOptions, 'method' | 'url' | 'data'>
  ): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'POST', url, data, ...options });
  }

  async put<T = unknown>(
    url: string,
    data?: unknown,
    options?: Omit<RequestOptions, 'method' | 'url' | 'data'>
  ): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'PUT', url, data, ...options });
  }

  async patch<T = unknown>(
    url: string,
    data?: unknown,
    options?: Omit<RequestOptions, 'method' | 'url' | 'data'>
  ): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'PATCH', url, data, ...options });
  }

  async delete<T = unknown>(
    url: string,
    options?: Omit<RequestOptions, 'method' | 'url'>
  ): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'DELETE', url, ...options });
  }

  close(): void {
    // No cleanup needed for fetch-based client
  }
}
