import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const generatedFile = resolve(
  repositoryRoot,
  process.env.AGENT_SERVER_GENERATED_OUTPUT ?? 'src/generated/agent-server-schema.ts'
);
const generator = join(repositoryRoot, 'scripts/generate-agent-server-api.mjs');

const before = await readFile(generatedFile, 'utf8');
const result = spawnSync(process.execPath, [generator], {
  cwd: repositoryRoot,
  env: process.env,
  stdio: 'inherit',
});
if (result.error) {
  throw result.error;
}
if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

const after = await readFile(generatedFile, 'utf8');
if (before !== after) {
  process.stderr.write(
    `${generatedFile} was stale and has been regenerated. ` +
      'Commit the generated contract diff.\n'
  );
  process.exit(1);
}

process.stdout.write('Checked-in Agent Server contract is current.\n');
