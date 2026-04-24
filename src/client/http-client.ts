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

  async request<T = unknown>(options: RequestOptions): Promise<HttpResponse<T>> {
    const relativePath = options.url.startsWith('/') ? options.url.slice(1) : options.url;
    const url = new URL(relativePath, this.baseUrl + '/');

    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          if (Array.isArray(value)) {
            value.forEach((item) => url.searchParams.append(key, String(item)));
          } else {
            url.searchParams.append(key, String(value));
          }
        }
      });
    }

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
