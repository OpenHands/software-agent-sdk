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
const pathPrefix = process.env.AGENT_SERVER_OPENAPI_PATH_PREFIX;

function selectPathPrefix(schema, prefix) {
  const paths = Object.fromEntries(
    Object.entries(schema.paths ?? {}).filter(([path]) => path.startsWith(prefix))
  );
  if (Object.keys(paths).length === 0) {
    throw new Error(`No OpenAPI paths start with ${prefix}`);
  }

  const schemas = schema.components?.schemas ?? {};
  const pending = new Set();
  const selected = {};
  const addReferences = (value) => {
    if (Array.isArray(value)) {
      value.forEach(addReferences);
    } else if (value && typeof value === 'object') {
      const reference = value.$ref;
      const schemaPrefix = '#/components/schemas/';
      if (typeof reference === 'string' && reference.startsWith(schemaPrefix)) {
        pending.add(
          reference.slice(schemaPrefix.length).replaceAll('~1', '/').replaceAll('~0', '~')
        );
      }
      Object.values(value).forEach(addReferences);
    }
  };

  addReferences(paths);
  while (pending.size > 0) {
    const name = pending.values().next().value;
    pending.delete(name);
    if (Object.hasOwn(selected, name)) continue;
    if (!Object.hasOwn(schemas, name)) {
      throw new Error(`OpenAPI schema reference not found: ${name}`);
    }
    selected[name] = schemas[name];
    addReferences(schemas[name]);
  }

  return {
    ...schema,
    paths,
    components: { ...schema.components, schemas: selected },
  };
}

async function main() {
  const {
    schema: sourceSchema,
    source,
    image,
  } = await loadPinnedAgentServerOpenApi({
    repositoryRoot,
    explicitSchemaPath,
  });
  const schema = pathPrefix ? selectPathPrefix(sourceSchema, pathPrefix) : sourceSchema;

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
    const header = pathPrefix
      ? [
          '// Generated file. Do not edit by hand.',
          `// Source: ${explicitSchemaSource ?? image} (paths matching ${pathPrefix})`,
          '// Regenerate with:',
          '//   AGENT_SERVER_OPENAPI_PATH=/path/to/openapi.json',
          `//   AGENT_SERVER_OPENAPI_PATH_PREFIX=${pathPrefix}`,
          '//   AGENT_SERVER_GENERATED_OUTPUT=src/generated/prompt-enhancement-schema.ts',
          `//   npm run ${process.env.AGENT_SERVER_GENERATE_COMMAND ?? 'generate:agent-server-api'}`,
          '',
        ].join('\n')
      : [
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
