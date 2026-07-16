export class DeviceFlowError extends Error {
  constructor(
    message: string,
    public readonly code?: string
  ) {
    super(message);
    this.name = 'DeviceFlowError';
    Object.setPrototypeOf(this, DeviceFlowError.prototype);
  }
}

export interface DeviceAuthorizationResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string;
  expires_in: number;
  interval: number;
}

export interface DeviceTokenResponse {
  access_token: string;
  token_type: string;
  expires_in?: number;
}

export interface PollDeviceTokenOptions {
  interval: number;
  timeout?: number;
  signal?: AbortSignal;
}

interface DeviceTokenErrorResponse {
  error: string;
  error_description?: string;
  interval?: number;
}

const DEFAULT_TIMEOUT_MS = 600_000;
const MAX_INTERVAL_MS = 30_000;

export function isOpenHandsCloudHost(host: string): boolean {
  try {
    const trimmed = host.trim().toLowerCase();
    const withProtocol = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
    const hostname = new URL(withProtocol).hostname;
    return (
      hostname.endsWith('.all-hands.dev') ||
      hostname === 'all-hands.dev' ||
      hostname.endsWith('.openhands.dev') ||
      hostname === 'openhands.dev'
    );
  } catch {
    return false;
  }
}

function normalizeHost(host: string): string {
  return host.replace(/\/+$/, '');
}

async function requestCloudDeviceEndpoint(
  host: string,
  path: string,
  body: unknown,
  contentType: string,
  signal?: AbortSignal
): Promise<Response> {
  const requestBody =
    typeof body === 'string' ||
    body instanceof Blob ||
    body instanceof FormData ||
    body instanceof URLSearchParams
      ? body
      : JSON.stringify(body);

  return fetch(`${normalizeHost(host)}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': contentType },
    body: requestBody,
    signal,
  });
}

export async function startDeviceFlow(host: string): Promise<DeviceAuthorizationResponse> {
  try {
    const response = await requestCloudDeviceEndpoint(
      host,
      '/oauth/device/authorize',
      {},
      'application/json'
    );

    if (!response.ok) {
      throw new DeviceFlowError(`Failed to start device flow: Server returned ${response.status}`);
    }

    const data = await response.json();
    if (!data.device_code || !data.user_code || !data.verification_uri) {
      throw new DeviceFlowError(
        'Invalid response from device authorization endpoint: missing required fields'
      );
    }

    return {
      device_code: data.device_code,
      user_code: data.user_code,
      verification_uri: data.verification_uri,
      verification_uri_complete:
        data.verification_uri_complete ??
        `${data.verification_uri}?user_code=${encodeURIComponent(data.user_code)}`,
      expires_in: data.expires_in ?? 600,
      interval: data.interval ?? 5,
    };
  } catch (error) {
    if (error instanceof DeviceFlowError) {
      throw error;
    }
    throw new DeviceFlowError(
      `Failed to start device flow: ${error instanceof Error ? error.message : String(error)}`
    );
  }
}

export async function pollForToken(
  host: string,
  deviceCode: string,
  options: PollDeviceTokenOptions
): Promise<DeviceTokenResponse> {
  const timeout = options.timeout ?? DEFAULT_TIMEOUT_MS;
  let interval = Math.max(1, options.interval) * 1000;
  const startTime = Date.now();

  while (Date.now() - startTime < timeout) {
    if (options.signal?.aborted) {
      throw new DeviceFlowError('Authorization cancelled', 'cancelled');
    }

    try {
      const body = new URLSearchParams({
        grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
        device_code: deviceCode,
      });
      const response = await requestCloudDeviceEndpoint(
        host,
        '/oauth/device/token',
        body,
        'application/x-www-form-urlencoded',
        options.signal
      );

      if (response.ok) {
        const data = await response.json();
        if (!data.access_token) {
          throw new DeviceFlowError('Invalid token response: missing access_token');
        }
        return {
          access_token: data.access_token,
          token_type: data.token_type ?? 'Bearer',
          expires_in: data.expires_in,
        };
      }

      let errorData: DeviceTokenErrorResponse;
      try {
        errorData = await response.json();
      } catch {
        throw new DeviceFlowError(`Unexpected response from server: ${response.status}`);
      }

      switch (errorData.error) {
        case 'authorization_pending':
          break;
        case 'slow_down':
          if (
            typeof errorData.interval === 'number' &&
            Number.isFinite(errorData.interval) &&
            errorData.interval > 0
          ) {
            interval = Math.max(1, Math.min(errorData.interval, 30)) * 1000;
          } else {
            interval = Math.min(interval + 5000, MAX_INTERVAL_MS);
          }
          break;
        case 'expired_token':
          throw new DeviceFlowError('Device code has expired. Please try again.', 'expired_token');
        case 'access_denied':
          throw new DeviceFlowError('Authorization request was denied.', 'access_denied');
        default:
          throw new DeviceFlowError(
            `Authorization error: ${errorData.error}${
              errorData.error_description ? ` - ${errorData.error_description}` : ''
            }`,
            errorData.error
          );
      }
    } catch (error) {
      if (error instanceof DeviceFlowError) {
        throw error;
      }
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new DeviceFlowError('Authorization cancelled', 'cancelled');
      }
      console.warn('Network error during polling, retrying:', error);
    }

    try {
      await sleep(interval, options.signal);
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new DeviceFlowError('Authorization cancelled', 'cancelled');
      }
      throw error;
    }
  }

  throw new DeviceFlowError('Timeout waiting for authorization. Please try again.', 'timeout');
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timeoutId = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timeoutId);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true }
    );
  });
}
