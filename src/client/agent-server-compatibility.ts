import { HttpClient } from './http-client';
import type { ServerInfo } from '../types/base';

export const AGENT_SERVER_VERSION_ERROR_CODE = 'AGENT_SERVER_VERSION_TOO_OLD';

export interface AgentServerFeatureRequirement {
  feature: string;
  displayName: string;
  minVersion: string;
}

export const AgentServerFeatureRequirements = {
  workspaces: {
    feature: 'workspaces',
    displayName: 'Workspaces',
    minVersion: '1.23.0',
  },
} as const satisfies Record<string, AgentServerFeatureRequirement>;

export class AgentServerVersionError extends Error {
  public readonly code = AGENT_SERVER_VERSION_ERROR_CODE;
  public readonly feature: string;
  public readonly displayName: string;
  public readonly requiredVersion: string;
  public readonly actualVersion: string;

  constructor({
    requirement,
    actualVersion,
  }: {
    requirement: AgentServerFeatureRequirement;
    actualVersion: string;
  }) {
    super(
      `${requirement.displayName} requires agent-server ${requirement.minVersion} or newer; this backend is running ${actualVersion}. Please upgrade your agent-server backend.`
    );
    this.name = 'AgentServerVersionError';
    this.feature = requirement.feature;
    this.displayName = requirement.displayName;
    this.requiredVersion = requirement.minVersion;
    this.actualVersion = actualVersion;

    Object.setPrototypeOf(this, AgentServerVersionError.prototype);
  }
}

interface ParsedVersion {
  major: number;
  minor: number;
  patch: number;
  prerelease?: string;
}

let serverInfoCache = new WeakMap<HttpClient, Promise<ServerInfo>>();

export function isAgentServerVersionError(error: unknown): error is AgentServerVersionError {
  return (
    error instanceof AgentServerVersionError ||
    (typeof error === 'object' &&
      error !== null &&
      'code' in error &&
      (error as { code?: unknown }).code === AGENT_SERVER_VERSION_ERROR_CODE)
  );
}

export function clearAgentServerInfoCache(client?: HttpClient): void {
  if (client) {
    serverInfoCache.delete(client);
    return;
  }
  serverInfoCache = new WeakMap<HttpClient, Promise<ServerInfo>>();
}

export async function getCachedAgentServerInfo(client: HttpClient): Promise<ServerInfo> {
  const cached = serverInfoCache.get(client);
  if (cached) {
    return cached;
  }

  const request = client.get<ServerInfo>('/server_info').then((response) => response.data);
  serverInfoCache.set(client, request);
  return request;
}

export async function assertAgentServerSupports(
  client: HttpClient,
  requirement: AgentServerFeatureRequirement
): Promise<ServerInfo> {
  const serverInfo = await getCachedAgentServerInfo(client);
  const actualVersion =
    typeof serverInfo.version === 'string' && serverInfo.version.trim()
      ? serverInfo.version.trim()
      : 'unknown';
  const comparison = compareAgentServerVersions(actualVersion, requirement.minVersion);

  if (comparison === null || comparison < 0) {
    throw new AgentServerVersionError({ requirement, actualVersion });
  }

  return serverInfo;
}

export function compareAgentServerVersions(actual: string, required: string): number | null {
  const parsedActual = parseAgentServerVersion(actual);
  const parsedRequired = parseAgentServerVersion(required);

  if (!parsedActual || !parsedRequired) {
    return null;
  }

  for (const key of ['major', 'minor', 'patch'] as const) {
    if (parsedActual[key] > parsedRequired[key]) {
      return 1;
    }
    if (parsedActual[key] < parsedRequired[key]) {
      return -1;
    }
  }

  if (parsedActual.prerelease && !parsedRequired.prerelease) {
    return -1;
  }
  if (!parsedActual.prerelease && parsedRequired.prerelease) {
    return 1;
  }
  if (parsedActual.prerelease && parsedRequired.prerelease) {
    return parsedActual.prerelease.localeCompare(parsedRequired.prerelease);
  }

  return 0;
}

function parseAgentServerVersion(version: string): ParsedVersion | null {
  const trimmed = version.trim().replace(/^v/, '');
  const [withoutBuild] = trimmed.split('+');
  const [core, prerelease] = withoutBuild.split('-', 2);
  const parts = core.split('.');

  if (parts.length !== 3) {
    return null;
  }

  const [major, minor, patch] = parts.map((part) => Number(part));
  if (![major, minor, patch].every((part) => Number.isInteger(part) && part >= 0)) {
    return null;
  }

  return { major, minor, patch, prerelease };
}
