import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createClient } from '@hey-api/openapi-ts';
import { format, resolveConfig } from 'prettier';

import { loadPinnedAgentServerOpenApi } from './agent-server-openapi.mjs';

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const generatedFile = resolve(
  repositoryRoot,
  process.env.AGENT_SERVER_GENERATED_OUTPUT ?? 'src/generated/agent-server-schema.ts'
);
const explicitSchemaPath = process.env.AGENT_SERVER_OPENAPI_PATH;
const explicitSchemaSource = process.env.AGENT_SERVER_SCHEMA_SOURCE;

async function main() {
  const { schema, source, image } = await loadPinnedAgentServerOpenApi({
    repositoryRoot,
    explicitSchemaPath,
  });

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
      `// Source: ${explicitSchemaSource ?? image}`,
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
