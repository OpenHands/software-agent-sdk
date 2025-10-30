/**
 * HTTP client for OpenHands Agent Server API
 */

export interface HttpClientOptions {
  baseUrl: string;
  apiKey?: string;
  timeout?: number;
}

export interface RequestOptions {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  url: string;
  params?: Record<string, any>;
  data?: any;
  headers?: Record<string, string>;
  timeout?: number;
  acceptableStatusCodes?: Set<number>;
}

export interface HttpResponse<T = any> {
  data: T;
  status: number;
  statusText: string;
  headers: Record<string, string>;
}

export class HttpError extends Error {
  constructor(
    public status: number,
    public statusText: string,
    public response?: any,
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

  async request<T = any>(options: RequestOptions): Promise<HttpResponse<T>> {
    const url = new URL(options.url, this.baseUrl);
    
    // Add query parameters
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          url.searchParams.append(key, String(value));
        }
      });
    }

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...options.headers,
    };

    // Add API key header if available
    if (this.apiKey) {
      headers['X-Session-API-Key'] = this.apiKey;
    }

    const requestInit: RequestInit = {
      method: options.method,
      headers,
      signal: AbortSignal.timeout(options.timeout || this.timeout),
    };

    // Add body for non-GET requests
    if (options.data && options.method !== 'GET') {
      if (options.data instanceof FormData) {
        // Remove content-type header for FormData (browser will set it with boundary)
        delete headers['Content-Type'];
        requestInit.body = options.data;
      } else {
        requestInit.body = JSON.stringify(options.data);
      }
    }

    try {
      const response = await fetch(url.toString(), requestInit);
      
      // Check if status code is acceptable
      const isAcceptable = options.acceptableStatusCodes?.has(response.status) || 
                          (!options.acceptableStatusCodes && response.ok);
      
      if (!isAcceptable) {
        let errorContent: any;
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

      // Parse response
      let data: T;
      const contentType = response.headers.get('content-type');
      if (contentType?.includes('application/json')) {
        data = await response.json();
      } else {
        data = (await response.text()) as unknown as T;
      }

      // Convert headers to plain object
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
          throw new Error(`Request timeout after ${options.timeout || this.timeout}ms`);
        }
        throw new Error(`Request failed: ${error.message}`);
      }
      
      throw new Error('Unknown request error');
    }
  }

  async get<T = any>(url: string, options?: Omit<RequestOptions, 'method' | 'url'>): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'GET', url, ...options });
  }

  async post<T = any>(url: string, data?: any, options?: Omit<RequestOptions, 'method' | 'url' | 'data'>): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'POST', url, data, ...options });
  }

  async put<T = any>(url: string, data?: any, options?: Omit<RequestOptions, 'method' | 'url' | 'data'>): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'PUT', url, data, ...options });
  }

  async delete<T = any>(url: string, options?: Omit<RequestOptions, 'method' | 'url'>): Promise<HttpResponse<T>> {
    return this.request<T>({ method: 'DELETE', url, ...options });
  }

  close(): void {
    // No cleanup needed for fetch-based client
  }
}