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
  hooks: {
    feature: 'hooks',
    displayName: 'Hooks API',
    minVersion: '1.23.0',
  },
  mcpTest: {
    feature: 'mcp-test',
    displayName: 'MCP test API',
    minVersion: '1.23.0',
  },
  mcpOAuth: {
    feature: 'mcp-oauth',
    displayName: 'MCP OAuth API',
    minVersion: '1.31.0',
  },
  executeTool: {
    feature: 'execute-tool',
    displayName: 'Direct tool execution API',
    // Gated on the `direct_tool_execution_v1` capability (see
    // FEATURE_CAPABILITIES), not this version floor: the route ships in the same
    // package version as servers that predate it (this repo's ServerInfo
    // reports 1.55.1), so a version compare would reject a working endpoint.
    // minVersion is only the documented floor named in the error message.
    minVersion: '1.57.0',
  },
} as const satisfies Record<string, AgentServerFeatureRequirement>;

/**
 * Capabilities that supersede the version floor for a feature requirement.
 *
 * A version compare cannot distinguish a server built from this branch from an
 * older server carrying the same package version, so for these features the
 * advertised capability is authoritative and the version floor is only used for
 * the error message.
 */
const FEATURE_CAPABILITIES: Partial<Record<string, string>> = {
  'execute-tool': 'direct_tool_execution_v1',
};

export class AgentServerVersionError extends Error {
  public readonly code = AGENT_SERVER_VERSION_ERROR_CODE;
  public readonly feature: string;
  public readonly displayName: string;
  public readonly requiredVersion: string;
  public readonly actualVersion: string;
  public readonly capability?: string;

  constructor({
    requirement,
    actualVersion,
    capability,
  }: {
    requirement: AgentServerFeatureRequirement;
    actualVersion: string;
    capability?: string;
  }) {
    const requirementText = capability
      ? `requires an agent-server that advertises the "${capability}" capability (agent-server ${requirement.minVersion} or newer)`
      : `requires agent-server ${requirement.minVersion} or newer`;
    super(
      `${requirement.displayName} ${requirementText}; this backend is running ${actualVersion}. Please upgrade your agent-server backend.`
    );
    this.name = 'AgentServerVersionError';
    this.feature = requirement.feature;
    this.displayName = requirement.displayName;
    this.requiredVersion = requirement.minVersion;
    this.actualVersion = actualVersion;
    this.capability = capability;

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
  void request.catch(() => {
    if (serverInfoCache.get(client) === request) {
      serverInfoCache.delete(client);
    }
  });
  return request;
}

export async function assertAgentServerSupports(
  client: HttpClient,
  requirement: AgentServerFeatureRequirement
): Promise<ServerInfo> {
  const serverInfo = await getCachedAgentServerInfo(client);
  const actualVersion = normalizeAgentServerVersion(serverInfo.version);

  // A capability probe is authoritative where the route ships in the same
  // package version as servers that predate it: the version compare cannot tell
  // them apart, and the capability is only advertised by a server that actually
  // exposes the endpoint.
  const capability = FEATURE_CAPABILITIES[requirement.feature];
  if (capability) {
    if (serverInfo.capabilities?.includes(capability)) {
      return serverInfo;
    }
    throw new AgentServerVersionError({ requirement, actualVersion, capability });
  }

  const comparison = compareAgentServerVersions(actualVersion, requirement.minVersion);
  if (comparison === null || comparison < 0) {
    throw new AgentServerVersionError({ requirement, actualVersion });
  }

  return serverInfo;
}

function normalizeAgentServerVersion(version: unknown): string {
  return typeof version === 'string' && version.trim() ? version.trim() : 'unknown';
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
