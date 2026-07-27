import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createClient } from '@hey-api/openapi-ts';
import { format, resolveConfig } from 'prettier';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const packageJsonPath = join(repositoryRoot, 'package.json');
const generatedFile = join(repositoryRoot, 'src/generated/agent-server-schema.ts');
const explicitSchemaPath = process.env.AGENT_SERVER_OPENAPI_PATH;

function getPinnedAgentServer(packageJson) {
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

function docker(...args) {
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

async function loadFromPinnedImage(image, version) {
  const containerName = `typescript-client-openapi-${process.pid}-${randomUUID()}`;
  let containerId;
  try {
    containerId = docker(
      'run',
      '--detach',
      '--rm',
      '--name',
      containerName,
      '--publish',
      '127.0.0.1::8000',
      image
    );
    const publishedPort = docker('port', containerId, '8000/tcp').split(':').at(-1);
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
        docker('stop', containerId);
      } catch {
        // The --rm container may already have stopped itself.
      }
    }
  }
}

async function loadSchema(image, version) {
  if (explicitSchemaPath) {
    const absolutePath = resolve(repositoryRoot, explicitSchemaPath);
    return {
      schema: JSON.parse(await readFile(absolutePath, 'utf8')),
      source: absolutePath,
    };
  }

  const artifact = await loadFromReleaseArtifact(version);
  return artifact ?? loadFromPinnedImage(image, version);
}

function validateOpenApi(schema) {
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

async function main() {
  const packageJson = JSON.parse(await readFile(packageJsonPath, 'utf8'));
  const { image, version } = getPinnedAgentServer(packageJson);
  const { schema, source } = await loadSchema(image, version);
  validateOpenApi(schema);

  const temporaryOutput = await mkdtemp(join(tmpdir(), 'agent-server-schema-'));
  try {
    const schemaPath = join(temporaryOutput, 'openapi.json');
    const generatorOutput = join(temporaryOutput, 'generated');
    await writeFile(schemaPath, `${JSON.stringify(schema)}\n`);
    await createClient({
      input: schemaPath,
      output: generatorOutput,
      plugins: ['@hey-api/typescript'],
    });

    const generated = await readFile(join(generatorOutput, 'types.gen.ts'), 'utf8');
    const header = [
      '// Generated file. Do not edit by hand.',
      `// Source image: ${image}`,
      `// Regenerate with: npm run generate:agent-server-api`,
      '',
    ].join('\n');
    const prettierConfig = (await resolveConfig(generatedFile)) ?? {};
    const formatted = await format(`${header}${generated}`, {
      ...prettierConfig,
      parser: 'typescript',
    });
    await mkdir(dirname(generatedFile), { recursive: true });
    await writeFile(generatedFile, formatted);
    process.stdout.write(`Generated ${generatedFile} from ${source}\n`);
  } finally {
    await rm(temporaryOutput, { recursive: true, force: true });
  }
}

await main();
