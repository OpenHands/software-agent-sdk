import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

export function getPinnedAgentServer(packageJson) {
  const image = packageJson.config?.agentServerImage;
  if (typeof image !== 'string') {
    throw new Error('package.json config.agentServerImage must be a string');
  }

  const match = image.match(/^ghcr\.io\/openhands\/agent-server:(\d+\.\d+\.\d+)-python$/);
  if (!match) {
    throw new Error(
      `config.agentServerImage must use an exact release tag, received ${JSON.stringify(image)}`
    );
  }

  return { image, version: match[1] };
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    redirect: 'follow',
    signal: AbortSignal.timeout(options.timeout ?? 30_000),
  });
  if (!response.ok) {
    const error = new Error(`GET ${url} returned HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

async function loadFromReleaseArtifact(version) {
  const url =
    `https://github.com/OpenHands/software-agent-sdk/releases/download/` +
    `v${version}/openapi.json`;
  try {
    const schema = await fetchJson(url);
    if (schema.info?.version !== version) {
      throw new Error(
        `OpenAPI artifact version ${JSON.stringify(schema.info?.version)} ` +
          `does not match pinned Agent Server ${version}`
      );
    }
    return { schema, source: url };
  } catch (error) {
    if (error.status === 404) {
      return undefined;
    }
    throw error;
  }
}

function docker(repositoryRoot, ...args) {
  return execFileSync('docker', args, {
    cwd: repositoryRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

async function waitForOpenApi(url) {
  const deadline = Date.now() + 120_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      return await fetchJson(url, { timeout: 5_000 });
    } catch (error) {
      lastError = error;
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 500));
    }
  }
  throw new Error(`Agent Server did not publish ${url}: ${lastError?.message}`);
}

async function loadFromPinnedImage(repositoryRoot, image, version) {
  const containerName = `typescript-client-openapi-${process.pid}-${randomUUID()}`;
  let containerId;
  try {
    containerId = docker(
      repositoryRoot,
      'run',
      '--detach',
      '--rm',
      '--name',
      containerName,
      '--publish',
      '127.0.0.1::8000',
      image
    );
    const publishedPort = docker(repositoryRoot, 'port', containerId, '8000/tcp').split(':').at(-1);
    if (!publishedPort || !/^\d+$/.test(publishedPort)) {
      throw new Error(`Could not resolve the published Agent Server port: ${publishedPort}`);
    }

    const url = `http://127.0.0.1:${publishedPort}/openapi.json`;
    const schema = await waitForOpenApi(url);
    return {
      schema,
      source: `${image} (${url}, isolated container for legacy release ${version})`,
    };
  } finally {
    if (containerId) {
      try {
        docker(repositoryRoot, 'stop', containerId);
      } catch {
        // The --rm container may already have stopped itself.
      }
    }
  }
}

export function validateOpenApi(schema) {
  if (
    !schema ||
    typeof schema !== 'object' ||
    typeof schema.openapi !== 'string' ||
    typeof schema.paths !== 'object' ||
    typeof schema.components?.schemas !== 'object'
  ) {
    throw new Error('The Agent Server input is not a complete OpenAPI document');
  }
}

export async function loadPinnedAgentServerOpenApi({ repositoryRoot, explicitSchemaPath }) {
  const packageJson = JSON.parse(await readFile(join(repositoryRoot, 'package.json'), 'utf8'));
  const { image, version } = getPinnedAgentServer(packageJson);

  let result;
  if (explicitSchemaPath) {
    const absolutePath = resolve(repositoryRoot, explicitSchemaPath);
    result = {
      schema: JSON.parse(await readFile(absolutePath, 'utf8')),
      source: absolutePath,
    };
  } else {
    result =
      (await loadFromReleaseArtifact(version)) ??
      (await loadFromPinnedImage(repositoryRoot, image, version));
  }

  validateOpenApi(result.schema);
  return { ...result, image, version };
}
