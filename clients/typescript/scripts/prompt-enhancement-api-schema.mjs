import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const typescriptRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(typescriptRoot, '../..');
const generatedFile = join(typescriptRoot, 'src/generated/prompt-enhancement-schema.ts');
const exporter = join(repositoryRoot, '.github/scripts/export_agent_server_openapi.py');
const generator = join(typescriptRoot, 'scripts/generate-agent-server-api.mjs');
const write = process.argv[2] === '--write';

function run(command, args, options) {
  const result = spawnSync(command, args, { stdio: 'inherit', ...options });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${command} exited with status ${result.status}`);
}

const temporaryDirectory = await mkdtemp(
  join(dirname(generatedFile), '.prompt-enhancement-schema-')
);
try {
  const openapiFile = join(temporaryDirectory, 'openapi.json');
  const candidateFile = write ? generatedFile : join(temporaryDirectory, 'schema.ts');

  run(
    'uv',
    [
      'run',
      '--frozen',
      '--package',
      'openhands-agent-server',
      'python',
      exporter,
      '--output',
      openapiFile,
    ],
    {
      cwd: repositoryRoot,
      env: { ...process.env, OPENHANDS_SUPPRESS_BANNER: '1' },
    }
  );

  run(process.execPath, [generator], {
    cwd: typescriptRoot,
    env: {
      ...process.env,
      AGENT_SERVER_OPENAPI_PATH: openapiFile,
      AGENT_SERVER_OPENAPI_PATH_PREFIX: '/api/prompt-enhancement/',
      AGENT_SERVER_SCHEMA_SOURCE: 'current branch Agent Server OpenAPI',
      AGENT_SERVER_GENERATE_COMMAND: 'generate:prompt-enhancement-api',
      AGENT_SERVER_GENERATED_OUTPUT: candidateFile,
    },
  });

  if (write) {
    process.stdout.write(
      'Generated prompt enhancement contract from current Agent Server source.\n'
    );
  } else if ((await readFile(generatedFile, 'utf8')) !== (await readFile(candidateFile, 'utf8'))) {
    process.stderr.write(
      'Prompt enhancement TypeScript contract is stale. Run npm run generate:prompt-enhancement-api.\n'
    );
    process.exitCode = 1;
  } else {
    process.stdout.write('Checked-in prompt enhancement contract is current.\n');
  }
} finally {
  await rm(temporaryDirectory, { recursive: true, force: true });
}
